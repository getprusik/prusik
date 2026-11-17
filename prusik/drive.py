"""`prusik drive <feature>` — run a sprint from a written plan to `sprint-complete` in code
(fb-13faaeb53496).

The state machine is deterministic Python; the LLM is a worker the machine calls. Each role
is spawned headless through the platform adapter with the tier model and the claude.ai or
API-key auth `drive.auth` names, under the writer lease the driver holds and delegates. The
gate commands run unchanged (`gate plan`, `prove-red`, `prove`, `advance`, `fix-round`,
`sprint-complete`), so every verdict still comes from prusik's own evidence. The driver stops
at the judgement points with `reports/<f>/drive-escalation.json` and rc 3.

Stdout is bounded: the auth line, one line per phase, the completion line or an escalation
block. Gate and tool output goes to `reports/<f>/drive.log`.

Spend is capped, not advised, and capped in USD at API list price (priced per model from
prusik/prices.json): `drive.budget_usd` (required) caps the sprint's measured cost, a phase's
`budget_usd` caps that phase, and `drive.session_budget_usd` caps what the host session running
drive spends on the feature. Raw tokens are reported beside the cost, never compared. Drive
measures before and after every spawn and escalates `budget_exceeded` at the first cap
crossed; a budget it cannot measure (including a model with no price) escalates
`budget_unmeasurable`, never a silent pass. A leftover `budget_tokens` key is refused.

Exit codes: 0 complete, 2 not drivable, 3 escalated.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, TextIO

import yaml

from prusik import fix_round, ledger, main_writer, phases, pin, pricing, roles, usage
from prusik import worktrees as _worktrees
from prusik.platform_adapter import (
    AgentSpec, PlatformAdapter, SpawnError, SpawnRequest, SpawnResult, get_adapter,
)

ESCALATION_KINDS = ("scope_rejected", "triage_rejected", "solo_handback", "plan_rejected",
                    "prove_red_failed", "suite_failed", "fix_round_cap", "permission_denied",
                    "spawn_failed", "tier_unresolved", "auth_mismatch", "writer_busy",
                    "advance_blocked", "tree_written", "budget_exceeded",
                    "budget_unmeasurable")
DRIVEN = ("scoping", "triage", "planning", "building", "reviewing", "integrating")
BUILDER_ROLES = ("backend-builder", "frontend-builder", "test-writer")
REVIEWERS = (("regression-sentinel", "regression.txt"),
             ("conventions-enforcer", "conventions.txt"))

_DEFAULTS = {"auth": "claude.ai", "spawn_timeout_min": 60, "suite_command": None,
             "design_rounds": 3, "budget_usd": None, "session_budget_usd": None}
_TICK_S = 300


def _denial_tool(d: dict) -> str:
    return str(d.get("tool_name") or d.get("tool") or "?")


def _unmatched_denials(denials: Iterable[dict], events: Iterable[dict],
                       session: str) -> list[str]:
    """Sorted tool names of denials with no `gate_blocked` event whose `session` == session
    and whose `tool` equals the denial's tool. An event with no `session` key matches
    nothing."""
    blocked = {e.get("tool") for e in events
               if e.get("event") == "gate_blocked" and e.get("session") == session}
    return sorted({t for t in (_denial_tool(d) for d in denials) if t not in blocked})


class Escalation(Exception):
    """A judgement point: drive stops, records why, and hands back to a person."""

    def __init__(self, kind: str, phase: str, role: str | None, reason: str,
                 evidence: list[str] | None = None) -> None:
        if kind not in ESCALATION_KINDS:
            raise ValueError(f"unknown escalation kind {kind!r}")
        super().__init__(f"{kind}: {reason}")
        self.kind, self.phase, self.role = kind, phase, role
        self.reason, self.evidence = reason, list(evidence or [])


class _Drive:
    def __init__(self, feature: str, adapter: PlatformAdapter, out: TextIO) -> None:
        self.f = feature
        self.adapter = adapter
        self.out = out
        self.root = ledger.project_root()
        self.cfg: dict[str, Any] = dict(_DEFAULTS)
        self.env: dict[str, str] = {}
        self.holder = ""
        self.phase = ""
        self.steps: list[str] = []
        self.session: str | None = None    # the host session running drive, if any
        self.spawned = False
        self.hand_off = False              # the session cap stopped drive: release the lease

    # ── cli plumbing ────────────────────────────────────────────────────────
    @property
    def reports(self) -> Path:
        return self.root / "reports" / self.f

    def _cli(self, argv: list[str], cwd: Path | None = None,
             env: dict[str, str] | None = None) -> tuple[int, str]:
        """Run a command, append its output to drive.log, return (rc, last 20 lines)."""
        p = subprocess.run(argv, cwd=str(cwd or self.root), capture_output=True, text=True,
                           check=False, env=env)
        text = (p.stdout or "") + (p.stderr or "")
        self.reports.mkdir(parents=True, exist_ok=True)
        with open(self.reports / "drive.log", "a", encoding="utf-8") as fh:
            fh.write(f"$ {' '.join(argv[:12])}{' …' if len(argv) > 12 else ''}"
                     f"  (rc {p.returncode})\n{text}\n")
        return p.returncode, "\n".join(text.strip().splitlines()[-20:])

    def _gate(self, *args: str) -> tuple[int, str]:
        return self._prusik("gate", *args)

    def _prusik(self, *args: str, cwd: Path | None = None) -> tuple[int, str]:
        # Through the launcher: the child must import this process's prusik (fb-f381d16021db).
        argv, env = pin.child_command(*args)
        return self._cli(argv, cwd, env=env)

    def _log(self, text: str) -> None:
        self.reports.mkdir(parents=True, exist_ok=True)
        with open(self.reports / "drive.log", "a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    # ── spawning ────────────────────────────────────────────────────────────
    def _agent_spec(self, role: str) -> AgentSpec:
        project = self.root / ".claude" / "agents" / f"{role}.md"
        packaged = (Path(__file__).parent / "templates" / ".claude" / "agents" / f"{role}.md")
        path = project if project.is_file() else packaged
        if not path.is_file():
            raise Escalation("spawn_failed", self.phase, role,
                             f"no agent spec for role {role!r}", [str(project)])
        text = path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if not text.startswith("---") or len(parts) < 3:
            raise Escalation("spawn_failed", self.phase, role,
                             f"agent spec {path.name} has no frontmatter", [str(path)])
        try:
            meta = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError as e:
            raise Escalation("spawn_failed", self.phase, role,
                             f"agent spec {path.name} frontmatter unreadable: {e}",
                             [str(path)]) from e
        tools = tuple(t.strip() for t in str(meta.get("tools") or "").split(",") if t.strip())
        return AgentSpec(name=str(meta.get("name") or role),
                         description=str(meta.get("description") or role),
                         tools=tools, prompt=parts[2].strip())

    def _model(self, role: str) -> str | None:
        rc, tail = self._prusik("tier", "resolve", self.f, "--role", role)
        line = next((ln.strip() for ln in reversed(tail.splitlines())
                     if ln.strip().startswith("model=")), None)
        if rc != 0 or line is None:
            raise Escalation("tier_unresolved", self.phase, role,
                             f"`prusik tier resolve --role {role}` rc {rc}",
                             tail.splitlines()[-5:])
        model = line.split("=", 1)[1].strip()
        return None if model in ("off", "") else model

    def _beat(self) -> None:
        main_writer.heartbeat(self.root, self.holder)

    def _spawn(self, role: str, prompt: str) -> tuple[SpawnResult, float]:
        self._check_budget(role)
        model = self._model(role)
        spec = self._agent_spec(role)
        sid = str(uuid.uuid4())
        ok, holder, _ = main_writer.check_and_touch(self.root, self.holder, feature=self.f,
                                                    phase=self.phase)
        if not ok or not main_writer.delegate(self.root, self.holder, sid, role=role,
                                              feature=self.f):
            who = ((holder or {}).get("session") or "?")[:12]
            raise Escalation("writer_busy", self.phase, role,
                             f"the writer lease is not held by this drive (holder {who})", [])
        usage.tag_session(sid, role=role, feature=self.f)
        req = SpawnRequest(
            role=role, prompt=prompt, model=model, cwd=str(self.root), root=str(self.root),
            session_id=sid, agent_spec=spec,
            timeout_s=int(self.cfg["spawn_timeout_min"]) * 60, env=self.env,
            on_tick=self._beat, tick_s=_TICK_S)
        before = self._tree_state()
        started = time.time()
        try:
            res = self.adapter.spawn(req)
        except SpawnError as e:
            self._check_tree(before, role, sid)
            raise Escalation("spawn_failed", self.phase, role, str(e), [sid]) from e
        ledger.append("drive_step", feature=self.f, phase=self.phase, role=role,
                      session=sid, rc=res.rc, is_error=res.is_error)
        self._log(f"# spawn {role} session {sid} rc {res.rc} is_error {res.is_error}\n"
                  f"{res.text}\n")
        self._check_tree(before, role, sid)
        self.spawned = True
        self._check_budget(role)
        if res.denials:
            unmatched = _unmatched_denials(res.denials, ledger.read_all(), sid)
            if unmatched:
                raise Escalation("permission_denied", self.phase, role,
                                 f"{role} was denied: {', '.join(unmatched)}", [sid])
            tools = sorted({_denial_tool(d) for d in res.denials})
            self._log(f"# gate denials recovered: {', '.join(tools)}")
            ledger.append("drive_gate_denials", feature=self.f, phase=self.phase, role=role,
                          session=sid, tools=tools)
        if res.is_error or res.timed_out:
            first = (res.text.strip().splitlines() or ["no output"])[0][:200]
            raise Escalation("spawn_failed", self.phase, role,
                             f"timed out: {first}" if res.timed_out else first, [sid])
        self.steps.append(f"spawn {role}")
        return res, started

    def _check_budget(self, role: str | None) -> None:
        """Escalate when the feature's measured cost (API list price) exceeds a cap: the
        sprint's (`drive.budget_usd`), the current phase's (`budget_usd` on its phase spec),
        or the driving host session's (`drive.session_budget_usd`). Unmeasurable, including a
        model with no price, escalates; the one exception is before drive's first spawn with
        no session yet registered, where nothing has been spent that a transcript could show."""
        u = usage.measure(self.f)                      # total: never raises
        if not u.measured:
            if not self.spawned and self.session is None and u.reason \
                    and u.reason.startswith("no registered session"):
                return
            raise Escalation("budget_unmeasurable", self.phase, role,
                             f"the cost budget could not be measured: {u.reason}",
                             [str(usage.registry_path()), str(ledger.ledger_path())])
        spec = phases.get_phase_spec(phases.load_sprint_config(self.root) or {},
                                     self.phase) or {}
        caps = [("sprint", u.cost_usd, self.cfg["budget_usd"], "drive.budget_usd"),
                (f"phase {self.phase}", u.cost_by_phase.get(self.phase, 0.0),
                 spec.get("budget_usd"), f"phases[{self.phase}].budget_usd")]
        if self.session is not None:
            caps.append(("this session", u.cost_by_session.get(self.session, 0.0),
                         self.cfg["session_budget_usd"], "drive.session_budget_usd"))
        from prusik import effort
        for scope, used, cap, key in caps:
            if cap and used > float(cap):
                self.hand_off = scope == "this session"
                remedy = ("resume in a fresh session (drive released this session's writer "
                          "lease)" if self.hand_off else f"raise {key} to continue")
                raise Escalation("budget_exceeded", self.phase, role,
                                 f"{scope} cost {effort.fmt_usd(used)} ({pricing.LABEL}), over "
                                 f"its cap {effort.fmt_usd(float(cap))} ({key}); {remedy}",
                                 [f"{key}={cap}", f"cost={used:.2f}",
                                  f"sprint_cost={u.cost_usd:.2f}", *usage.spend_report(u)])

    def _tree_state(self) -> dict[str, tuple[int, int]]:
        """(mtime_ns, size) of every file in the shared tree (`gate.main_tree_files`)."""
        from prusik import gate
        state: dict[str, tuple[int, int]] = {}
        for rel in gate.main_tree_files(self.root):
            try:
                st = (self.root / rel).stat()
            except OSError:
                continue
            state[rel] = (st.st_mtime_ns, st.st_size)
        return state

    def _check_tree(self, before: dict[str, tuple[int, int]], role: str, sid: str) -> None:
        """Escalate when the spawn added, removed or rewrote a shared-tree file its phase may
        not write (fb-1303f39d14a6). The hook reads what a command's text says it writes; this
        reads the tree itself, so a write no text reading names (`git apply`, a script's
        `open()`) still halts the sprint. The files are left as they are, for a person to
        judge."""
        after = self._tree_state()
        changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        if not changed:
            return
        config = phases.load_sprint_config(self.root) or {}
        refused = [p for p in changed
                   if not phases.is_path_writable(p, config, self.phase, self.f, self.root)[0]]
        if not refused:
            return
        ledger.append("drive_tree_written", feature=self.f, phase=self.phase, role=role,
                      session=sid, paths=refused[:50])
        raise Escalation("tree_written", self.phase, role,
                         f"{role} changed {len(refused)} shared-tree file(s) that phase "
                         f"{self.phase!r} may not write; left in place for review",
                         refused[:20])

    @staticmethod
    def _fresh(path: Path, since: float) -> bool:
        """path exists and st_mtime >= math.floor(since): 1 s mtime granularity can't fail a
        file written in the spawn's first second."""
        try:
            return path.stat().st_mtime >= math.floor(since)
        except OSError:
            return False

    @staticmethod
    def _verdict(path: Path, token: str, since: float) -> bool | None:
        """None when the report is missing or older than the spawn that should have written
        it; else whether its first-line verdict is `token`. Reports are evidence: never
        deleted or rewritten here, freshness decides."""
        if not _Drive._fresh(path, since):
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return None
        from prusik import gate
        return gate._verdict_line_ok(text, token)

    def _advance(self, target: str) -> None:
        rc, tail = self._gate("advance", target, "--feature", self.f)
        if rc != 0:
            raise Escalation("advance_blocked", self.phase, None,
                             f"`gate advance {target}` rc {rc}", tail.splitlines())
        self.steps.append(f"advance {target}")

    def _summary(self, nxt: str) -> None:
        print(f"[drive] {self.phase} → {nxt}: {', '.join(self.steps)}", file=self.out)
        self.steps = []

    # ── phases ──────────────────────────────────────────────────────────────
    def _design_loop(self, *, artifact: str, gate_args: tuple[str, ...], author: str,
                     critic: str, report: str, rejected_kind: str) -> None:
        """One bounded author/critic loop. Author prompts carry gate error lines or the
        report's path, never the critique text."""
        f = self.f
        cap = int(self.cfg["design_rounds"])
        report_path = self.reports / report
        authored = 0
        feedback = ""
        rejected = False
        while True:
            if not (self.root / artifact).is_file():
                rc, tail = 1, f"{artifact} does not exist"
            else:
                rc, tail = self._gate(*gate_args)
            if rc == 0:
                self.steps.append(f"gate {gate_args[0]}")
                vrc, _ = self._gate("verdict-current", "--role", critic, "--feature", f,
                                    "--artifact", artifact)
                if vrc == 0:
                    self.steps.append(f"{critic} current")
                    return
                _, started = self._spawn(
                    critic,
                    f"review {artifact} per your role spec; write reports/{f}/{report} and "
                    f"record it with `prusik gate record-verdict --role {critic} --feature "
                    f"{f} --artifact {artifact} --verdict <APPROVED|REJECTED>`. "
                    f"`prusik gate {gate_args[0]}` output: {tail}")
                ok = self._verdict(report_path, "APPROVED", started)
                if ok is None:
                    raise Escalation("spawn_failed", self.phase, critic,
                                     f"{critic} wrote no fresh {report}", [str(report_path)])
                if ok:
                    return
                rejected = True
                feedback = (f"{critic} rejected it; read reports/{f}/{report} and address "
                            f"every finding")
            else:
                rejected = False
                feedback = f"`prusik gate {gate_args[0]}` failed:\n{tail}"
            if authored == cap:
                if rejected:
                    raise Escalation(rejected_kind, self.phase, critic,
                                     f"{authored} author rounds did not produce an approved "
                                     f"{artifact}", [str(report_path)])
                raise Escalation(rejected_kind, self.phase, author,
                                 f"{authored} author rounds did not produce an approved "
                                 f"{artifact}", tail.splitlines())
            _, started = self._spawn(author, f"produce {artifact} per your role spec"
                                             + (f"\n\n{feedback}" if feedback else ""))
            authored += 1
            if not self._fresh(self.root / artifact, started):
                raise Escalation("spawn_failed", self.phase, author,
                                 f"{author} did not write {artifact}",
                                 [str(self.root / artifact)])

    def _scoping(self) -> None:
        artifact = f"design/{self.f}/scope.md"
        self._design_loop(artifact=artifact, gate_args=("scope", artifact),
                          author="scoping", critic="scope-critic",
                          report="scope-approval.txt", rejected_kind="scope_rejected")
        self._advance("triage")

    def _planning(self) -> None:
        self._design_loop(artifact=f"design/{self.f}/plan.md", gate_args=("plan", self.f),
                          author="feature-planner", critic="plan-critic",
                          report="plan-approval.txt", rejected_kind="plan_rejected")
        self._advance("building")

    def _triage(self) -> None:
        f = self.f
        path = self.root / "decisions" / f"{f}.json"
        rc, tail = self._prusik("triage", "--feature", f)
        evidence = [str(path), *tail.splitlines()[-5:]]
        if rc != 0:
            raise Escalation("triage_rejected", "triage", None, f"triage rc {rc}", evidence)
        try:
            decision = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise Escalation("triage_rejected", "triage", None, "decision missing",
                             evidence) from None
        except (OSError, ValueError) as e:
            raise Escalation("triage_rejected", "triage", None,
                             f"decision unreadable: {e}", evidence) from e
        mode = decision.get("mode") if isinstance(decision, dict) else None
        if mode not in ("team", "solo", "reject"):
            raise Escalation("triage_rejected", "triage", None, f"unknown mode {mode}",
                             evidence)
        if mode == "team":
            self.steps.append("triage team")
            self._advance("planning")
        elif mode == "solo":
            self._advance("solo_execute")
            # The solo build runs outside drive, so capture RED here, before it starts
            # (fb-221b8b5df7e6); solo_execute's exit check refuses to advance without it.
            self._capture_red("solo_execute")
            # The solo worktree records its fork point like every builder's, so the
            # reviewing checks can measure what the solo build changed (fb-e4f46ed05574).
            self._worktree("solo")
            raise Escalation("solo_handback", "triage", None,
                             f"triage chose solo; RED baselines captured and worktrees/solo/ "
                             f"created: implement in worktrees/solo/, then "
                             f"re-run prusik drive {f} from reviewing", [str(path)])
        else:
            raise Escalation("triage_rejected", "triage", None,
                             str(decision.get("reason") or "triage rejected the sprint"),
                             [str(path)])

    def _builders(self) -> list[str]:
        plan = self.root / "design" / self.f / "plan.md"
        found = [r for r in roles.proposed_roles(plan.read_text(encoding="utf-8"))
                 if r in BUILDER_ROLES] if plan.is_file() else []
        if not found:
            raise Escalation("spawn_failed", self.phase, None, "plan names no builder role",
                             [str(plan)])
        return found

    def _missing_red_baselines(self) -> list[str]:
        from prusik import gate
        return gate.missing_red_baselines(self.root, self.f)

    def _capture_red(self, phase: str) -> None:
        """Capture every missing RED baseline while the shared tree is still pre-change
        (before any builder runs), or escalate prove_red_failed."""
        if not self._missing_red_baselines():
            self.steps.append("prove-red satisfied")
            return
        rc, tail = self._gate("prove-red", "--feature", self.f)
        still = self._missing_red_baselines()
        if rc != 0 or still:
            raise Escalation("prove_red_failed", phase, None,
                             "prove-red did not capture a RED baseline for every "
                             "prove_red criterion", [*still, *tail.splitlines()[-5:]])
        self.steps.append("prove-red")

    def _worktree(self, role: str) -> Path:
        """worktrees/<role>, created and its fork point recorded by the one creation
        function (prusik.worktrees.create), with every git call logged to drive.log."""
        try:
            return _worktrees.create(self.root, self.f, role,
                                     run=lambda argv: self._cli(argv))
        except _worktrees.WorktreeError as e:
            raise Escalation("spawn_failed", self.phase or "building", role, e.reason,
                             e.tail) from e

    def _is_solo(self) -> bool:
        """This sprint is solo: the ledger records a `phase_advance` to `solo_execute` for
        this feature after its latest `sprint_started`. plan.md's presence is not the
        signal (fb-e4f46ed05574)."""
        solo = False
        for r in ledger.read_all():
            if r.get("feature") != self.f:
                continue
            if r.get("event") == "sprint_started":
                solo = False
            elif r.get("event") == "phase_advance" and r.get("to_phase") == "solo_execute":
                solo = True
        return solo

    def _building(self) -> None:
        f = self.f
        suite = self.cfg.get("suite_command")
        if not suite:
            raise Escalation("suite_failed", "building", None,
                             "drive.suite_command is not set; drive proves the full suite "
                             "before advancing building", [".claude/sprint-config.yaml"])
        builders = self._builders()
        self._capture_red("building")
        for role in builders:
            self._worktree(role)
            if self._builder_done(role):
                self.steps.append(f"{role} already built")
                continue
            self._spawn(role, f"implement your portion of design/{f}/plan.md in "
                              f"worktrees/{role}/ per your role spec. Commit your work on "
                              f"the worktree branch. Do not run git push or merge.")
            tip = self._branch_tip(role)
            if tip:
                ledger.append("drive_builder_done", feature=f, role=role, commit=tip)
        if (self.root / ".git").exists():
            self._prove_combined(builders, str(suite))
        else:
            for role in builders:
                rc, tail = self._prusik("prove", "--kind", "tests", "--", "sh", "-c",
                                        str(suite), cwd=self.root / "worktrees" / role)
                if rc != 0:
                    raise Escalation("suite_failed", "building", role,
                                     f"full suite not proven green in worktrees/{role}",
                                     tail.splitlines())
        self.steps.append("prove suite")
        self._advance("reviewing")

    def _branch_tip(self, role: str) -> str | None:
        """The commit sprint/<f>-<role> points at, or None outside git or without the branch."""
        if not (self.root / ".git").exists():
            return None
        rc, tail = self._cli(["git", "rev-parse", "--verify", "--quiet",
                              f"refs/heads/sprint/{self.f}-{role}"])
        return tail.strip().splitlines()[-1] if rc == 0 and tail.strip() else None

    def _builder_done(self, role: str) -> bool:
        """A builder finished in this visit to building and its branch has not moved since
        (fb-1e4da436fcfc): a resume must not spawn it again over its own work. The signal is
        the branch tip drive recorded after that builder's spawn passed every check; any
        later commit, or a new visit to building (a rewind), spawns it again."""
        tip = self._branch_tip(role)
        if tip is None:
            return False
        from prusik import effort
        records = ledger.read_all()
        visits = [w["start"] for w in effort.phase_windows(records, self.f)
                  if w["phase"] == "building"]
        entered = usage._parse_ts(visits[-1]) if visits else None
        if entered is None:
            return False
        done = [r for r in records if r.get("event") == "drive_builder_done"
                and r.get("feature") == self.f and r.get("role") == role
                and (usage._parse_ts(r.get("ts")) or entered) > entered]
        return bool(done) and done[-1].get("commit") == tip

    def _prove_combined(self, builders: list[str], suite: str) -> None:
        """Prove the full suite once on every builder branch merged together, never on one
        builder's branch alone: a plan that splits code and tests across builders leaves
        each branch red on its own (fb-1e4da436fcfc). The view is a detached scratch
        worktree outside the project (regression-sentinel's recipe), so the project tree is
        never written; it is removed whatever the outcome."""
        import shutil
        import tempfile
        scratch = Path(tempfile.mkdtemp(prefix=f"prusik-build-{self.f}-"))
        view = scratch / "view"
        try:
            rc, tail = self._cli(["git", "worktree", "add", "--detach", str(view), "HEAD"])
            if rc != 0:
                raise Escalation("suite_failed", "building", None,
                                 f"could not create the combined view: `git worktree add` "
                                 f"rc {rc}", tail.splitlines())
            for role in builders:
                branch = f"sprint/{self.f}-{role}"
                rc, tail = self._cli(["git", "-C", str(view), "merge", "--no-edit", branch])
                if rc != 0:
                    raise Escalation("suite_failed", "building", role,
                                     f"{branch} does not merge with the other builders' "
                                     f"branches", tail.splitlines())
            rc, tail = self._prusik("prove", "--kind", "tests", "--", "sh", "-c", suite,
                                    cwd=view)
            if rc != 0:
                raise Escalation("suite_failed", "building", None,
                                 "full suite not proven green on the merged builder "
                                 f"branches ({', '.join(builders)})", tail.splitlines())
        finally:
            if view.exists():
                self._cli(["git", "worktree", "remove", "--force", str(view)])
            shutil.rmtree(scratch, ignore_errors=True)
            self._cli(["git", "worktree", "prune"])

    def _report_first_token(self, report: str) -> str | None:
        try:
            for line in (self.reports / report).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    return line.split(maxsplit=1)[0]
        except OSError:
            pass
        return None

    def _solo_handback(self, report: str, n: object) -> Escalation:
        return Escalation(
            "solo_handback", "reviewing", None,
            f"fix round {n} is open: reports/{self.f}/{report} FAILED. Address its findings "
            f"in worktrees/solo/ and commit there, then re-run `prusik drive {self.f}`; it "
            f"ends the round and re-reviews.",
            [str(self.reports / report), str(self.root / "worktrees" / "solo")])

    def _fix_builders(self, report: str, n: object) -> None:
        for role in self._builders():
            self._spawn(role, f"fix round {n}: address the findings in "
                              f"reports/{self.f}/{report}; patch worktrees/{role}/ only; "
                              f"commit on the worktree branch")

    def _reviewing(self) -> None:
        f = self.f
        st = fix_round.current_state(self.root)
        if st is not None and st.get("feature") == f:          # step 0: resume the round
            failing = next((rep for _, rep in REVIEWERS
                            if self._report_first_token(rep) == "FAIL"), None)
            # Solo: the person fixed it after the hand-back; end the round and re-review.
            if failing and not self._is_solo():
                self._fix_builders(failing, st.get("round"))
            rc, tail = self._gate("fix-round", "end", "--feature", f)
            if rc != 0:
                raise Escalation("advance_blocked", "reviewing", None,
                                 "`gate fix-round end` failed", tail.splitlines())
            self.steps.append("resume fix-round")
        while True:
            failed: str | None = None
            for role, report in REVIEWERS:
                rc, _ = self._gate("verdict-current", "--role", role, "--feature", f,
                                   "--artifact", "worktrees")
                if rc == 0:
                    self.steps.append(f"{role} current")
                    continue
                _, started = self._spawn(
                    role, f"review changes for {f} per your role spec; write "
                          f"reports/{f}/{report} and record it with `prusik gate "
                          f"record-verdict --role {role} --feature {f} --artifact worktrees "
                          f"--verdict <PASS|FAIL>`")
                ok = self._verdict(self.reports / report, "PASS", started)
                if ok is None:
                    raise Escalation("spawn_failed", "reviewing", role,
                                     f"{role} wrote no fresh {report}",
                                     [str(self.reports / report)])
                if not ok:
                    failed = report
                    break
            if failed is None:
                break
            rc, tail = self._gate("fix-round", "start", "--feature", f)
            if rc == 2:
                raise Escalation("fix_round_cap", "reviewing", None,
                                 "fix-round cap reached", [str(self.reports / failed)])
            if rc != 0:
                raise Escalation("advance_blocked", "reviewing", None,
                                 "`gate fix-round start` failed", tail.splitlines())
            rnd = (fix_round.current_state(self.root) or {}).get("round")
            self.steps.append(f"fix round {rnd}")
            if self._is_solo():
                raise self._solo_handback(failed, rnd)
            self._fix_builders(failed, rnd)
            rc, tail = self._gate("fix-round", "end", "--feature", f)
            if rc != 0:
                raise Escalation("advance_blocked", "reviewing", None,
                                 "`gate fix-round end` failed", tail.splitlines())
        self._advance("integrating")

    def _minutes_since_start(self) -> int:
        start = None
        for r in ledger.read_all():
            if r.get("event") == "sprint_started" and r.get("feature") == self.f:
                start = r.get("ts")
        t = usage._parse_ts(start)
        if t is None:
            return 0
        return max(0, int((datetime.now(timezone.utc) - t).total_seconds() // 60))

    def _integrating(self) -> None:
        f = self.f
        mode = "git" if (self.root / ".git").exists() else "non-git"
        self._spawn("integrator",
                    f"integrate feature {f} per your role spec; the project is in {mode} "
                    f"mode. Do not push. Then write design/{f}/retro.md with ## What "
                    f"happened, ## What surprised, ## Updates to CLAUDE.md, including the "
                    f"`prusik effort` token totals and the main-thread share for {f}.")
        rc, tail = self._gate("sprint-complete", "--feature", f, "--duration-min",
                              str(self._minutes_since_start()))
        if rc != 0:
            raise Escalation("advance_blocked", "integrating", None,
                             "`gate sprint-complete` failed", tail.splitlines())
        self.steps.append("sprint-complete")

    # ── run ─────────────────────────────────────────────────────────────────
    def _preflight(self) -> int | None:
        """Return an rc when the sprint is not drivable, else None."""
        state = phases.current_sprint_state(self.root)
        if not state or not state.get("feature"):
            print("[drive] no active sprint; start one with /sprint-run", file=self.out)
            return 2
        if state.get("feature") != self.f:
            print(f"[drive] the active sprint is {state.get('feature')!r}, not {self.f!r}",
                  file=self.out)
            return 2
        phase = state.get("phase")
        if phase not in DRIVEN:
            print(f"[drive] sprint {self.f} is in {phase!r}; drive takes over at scoping, "
                  f"triage, planning, building, reviewing or integrating", file=self.out)
            return 2
        if phase == "scoping" and state.get("lane") == "trivial":
            print(f"[drive] sprint {self.f} is on the trivial lane: write "
                  f"design/{self.f}/trivial.md, implement in worktrees/solo/, then re-run "
                  f"drive from reviewing", file=self.out)
            return 2
        sprint_cfg = phases.load_sprint_config(self.root) or {}
        legacy = pricing.legacy_budget_keys(sprint_cfg)
        if legacy:
            for msg in legacy:
                print(f"[drive] {msg}", file=self.out)
            return 2
        dcfg = sprint_cfg.get("drive") or {}
        rounds = dcfg.get("design_rounds", _DEFAULTS["design_rounds"])
        if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
            print(f"[drive] drive.design_rounds must be a positive integer (got {rounds})",
                  file=self.out)
            return 2
        for key, required in (("budget_usd", True), ("session_budget_usd", False)):
            v = dcfg.get(key)
            if v is None and not required:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(v) or v <= 0:
                print(f"[drive] drive.{key} must be a positive dollar amount"
                      + (" (required: drive spends against a cap, never open-ended)"
                         if v is None else f" (got {v})"), file=self.out)
                return 2
        self.phase = str(phase)
        return None

    def _setup(self) -> None:
        cfg = (phases.load_sprint_config(self.root) or {}).get("drive") or {}
        self.cfg.update({k: v for k, v in cfg.items() if k in _DEFAULTS})
        auth = str(self.cfg["auth"])
        try:
            self.env = self.adapter.spawn_env(auth, os.environ)
            method, source = self.adapter.auth_method(self.env)
        except (ValueError, SpawnError) as e:
            raise Escalation("auth_mismatch", self.phase, None, str(e), []) from e
        print(f"[drive] auth: {method} ({source})", file=self.out)
        if method != auth:
            raise Escalation("auth_mismatch", self.phase, None,
                             f"drive.auth is {auth!r} but the session would use {method!r} "
                             f"(source {source}); unset ANTHROPIC_API_KEY or set drive.auth",
                             [f"method={method}", f"source={source}",
                              "env=ANTHROPIC_API_KEY"])
        self.session = self.adapter.current_session_id()
        self.holder = self.session or f"drive-{uuid.uuid4()}"
        ok, holder, _ = main_writer.check_and_touch(self.root, self.holder, feature=self.f,
                                                    phase=self.phase)
        if not ok:
            raise Escalation("writer_busy", self.phase, None,
                             f"session {((holder or {}).get('session') or '?')[:12]} holds "
                             f"the single-writer lease", [])
        main_writer.set_driver(self.root, self.holder, self.f)

    def run(self) -> int:
        rc = self._preflight()
        if rc is not None:
            return rc
        (self.reports / "drive-escalation.json").unlink(missing_ok=True)
        handlers: dict[str, Callable[[], None]] = {
            "scoping": self._scoping, "triage": self._triage,
            "planning": self._planning, "building": self._building,
            "reviewing": self._reviewing, "integrating": self._integrating}
        try:
            self._setup()
            while True:
                handlers[self.phase]()
                if self.phase == "integrating":
                    print(f"[drive] complete: {self.f} ({', '.join(self.steps)})",
                          file=self.out)
                    return 0
                state = phases.current_sprint_state(self.root) or {}
                nxt = str(state.get("phase"))
                self._summary(nxt)
                if nxt not in DRIVEN:
                    raise Escalation("advance_blocked", self.phase, None,
                                     f"the sprint moved to {nxt!r}, which drive does not run",
                                     [])
                self.phase = nxt
        except Escalation as e:
            self._escalate(e)
            return 3
        finally:
            main_writer.clear_driver(self.root)
            if self.hand_off and (main_writer.load(self.root) or {}).get("session") \
                    == self.holder:
                main_writer.release(self.root)
                ledger.append("writer_lease_released", reason="drive session budget",
                              prior_holder=self.holder, feature=self.f)

    def _escalate(self, e: Escalation) -> None:
        path = self.reports / "drive-escalation.json"
        self.reports.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "kind": e.kind, "phase": e.phase, "role": e.role, "reason": e.reason,
            "evidence": e.evidence, "ts": datetime.now(timezone.utc).isoformat()}, indent=1),
            encoding="utf-8")
        ledger.append("drive_escalated", feature=self.f, kind=e.kind, phase=e.phase,
                      role=e.role, reason=e.reason)
        print(f"[drive] ESCALATION {e.kind} in {e.phase} ({e.role or 'drive'})", file=self.out)
        print(e.reason.splitlines()[0][:300] if e.reason else "", file=self.out)
        for line in e.evidence[:10]:
            print(f"  {line[:200]}", file=self.out)
        print(f"details: reports/{self.f}/drive-escalation.json", file=self.out)


def run(feature: str, *, adapter: PlatformAdapter | None = None,
        out: TextIO = sys.stdout) -> int:
    """Drive `feature`'s active sprint to sprint-complete. 0 complete, 2 not drivable,
    3 escalated."""
    return _Drive(feature, adapter or get_adapter(), out).run()
