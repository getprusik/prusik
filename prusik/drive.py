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

from prusik import fix_round, ledger, main_writer, phases, roles, usage
from prusik.platform_adapter import (
    AgentSpec, PlatformAdapter, SpawnError, SpawnRequest, SpawnResult, get_adapter,
)

ESCALATION_KINDS = ("scope_rejected", "triage_rejected", "solo_handback", "plan_rejected",
                    "prove_red_failed", "suite_failed", "fix_round_cap", "permission_denied",
                    "spawn_failed", "tier_unresolved", "auth_mismatch", "writer_busy",
                    "advance_blocked", "tree_written")
DRIVEN = ("scoping", "triage", "planning", "building", "reviewing", "integrating")
BUILDER_ROLES = ("backend-builder", "frontend-builder", "test-writer")
REVIEWERS = (("regression-sentinel", "regression.txt"),
             ("conventions-enforcer", "conventions.txt"))

_DEFAULTS = {"auth": "claude.ai", "spawn_timeout_min": 60, "suite_command": None,
             "design_rounds": 3}
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

    # ── cli plumbing ────────────────────────────────────────────────────────
    @property
    def reports(self) -> Path:
        return self.root / "reports" / self.f

    def _cli(self, argv: list[str], cwd: Path | None = None) -> tuple[int, str]:
        """Run a command, append its output to drive.log, return (rc, last 20 lines)."""
        p = subprocess.run(argv, cwd=str(cwd or self.root), capture_output=True, text=True,
                           check=False)
        text = (p.stdout or "") + (p.stderr or "")
        self.reports.mkdir(parents=True, exist_ok=True)
        with open(self.reports / "drive.log", "a", encoding="utf-8") as fh:
            fh.write(f"$ {' '.join(argv[:12])}{' …' if len(argv) > 12 else ''}"
                     f"  (rc {p.returncode})\n{text}\n")
        return p.returncode, "\n".join(text.strip().splitlines()[-20:])

    def _gate(self, *args: str) -> tuple[int, str]:
        return self._cli([sys.executable, "-m", "prusik", "gate", *args])

    def _prusik(self, *args: str, cwd: Path | None = None) -> tuple[int, str]:
        return self._cli([sys.executable, "-m", "prusik", *args], cwd)

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
            raise Escalation("solo_handback", "triage", None,
                             f"triage chose solo; RED baselines captured: implement in worktrees/solo/, then "
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
        wt = self.root / "worktrees" / role
        if wt.exists():
            return wt
        if (self.root / ".git").exists():
            # A worktree dir removed without `git worktree remove` stays registered and
            # blocks `add` at that path; prune drops only registrations whose dir is gone.
            # A branch left by an earlier attempt that carries commits of its own is reused
            # as is, so a builder's work survives a retry. One with no commits beyond HEAD's
            # history holds nothing to lose and starts again from HEAD (-B).
            branch = f"sprint/{self.f}-{role}"
            rc, tail = self._cli(["git", "worktree", "prune"])
            if rc == 0:
                exists, _ = self._cli(["git", "rev-parse", "--verify", "--quiet",
                                       f"refs/heads/{branch}"])
                spent = exists != 0 or self._cli(
                    ["git", "merge-base", "--is-ancestor", branch, "HEAD"])[0] == 0
                add = (["git", "worktree", "add", "-B", branch, f"worktrees/{role}", "HEAD"]
                       if spent else ["git", "worktree", "add", f"worktrees/{role}", branch])
                rc, tail = self._cli(add)
            if rc != 0:
                raise Escalation("spawn_failed", "building", role,
                                 f"`git worktree add` rc {rc}", tail.splitlines())
        else:
            wt.mkdir(parents=True)
        return wt

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
            self._spawn(role, f"implement your portion of design/{f}/plan.md in "
                              f"worktrees/{role}/ per your role spec. Commit your work on "
                              f"the worktree branch. Do not run git push or merge.")
        for role in builders:
            rc, tail = self._prusik("prove", "--kind", "tests", "--", "sh", "-c", str(suite),
                                    cwd=self.root / "worktrees" / role)
            if rc != 0:
                raise Escalation("suite_failed", "building", role,
                                 f"full suite not proven green in worktrees/{role}",
                                 tail.splitlines())
        self.steps.append("prove suite")
        self._advance("reviewing")

    def _report_first_token(self, report: str) -> str | None:
        try:
            for line in (self.reports / report).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    return line.split(maxsplit=1)[0]
        except OSError:
            pass
        return None

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
            if failing:
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
        rounds = ((phases.load_sprint_config(self.root) or {}).get("drive") or {}) \
            .get("design_rounds", _DEFAULTS["design_rounds"])
        if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
            print(f"[drive] drive.design_rounds must be a positive integer (got {rounds})",
                  file=self.out)
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
        self.holder = self.adapter.current_session_id() or f"drive-{uuid.uuid4()}"
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
