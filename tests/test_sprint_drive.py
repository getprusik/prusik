"""`prusik drive <feature>`: the deterministic sprint driver (fb-13faaeb53496).

moat-finding: fb-1303f39d14a6

Every test injects a FakeAdapter, so no real `claude` runs. `_gate` is replaced by a recorder
where a real gate run would need a real suite; the planning, tier and lease paths run for real.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from prusik import drive, fix_round, ledger, main_writer, phases, usage
from prusik import platform_adapter as pa

F = "feat"
PLAN = """# Plan — feat

## Modules touched
- `src/a.py`

## Test plan
tests.

## Risks
- none

## Proposed roles
- **backend-builder** owns worktrees/backend-builder/.
"""
CRITERIA = {"schema_version": "1.0", "criteria": [{
    "id": "c1", "description": "d", "verify_command": "false", "kind": "tests",
    "prove_red": True}]}
_TEMPLATE = Path(drive.__file__).parent / "templates" / ".claude" / "sprint-config.yaml"


class FakeAdapter(pa.ClaudeCodeAdapter):
    def __init__(self, events: list, *, auth=("claude.ai", "login")):
        self.events, self.auth = events, auth
        self.requests: list[pa.SpawnRequest] = []
        self.scripts: dict = {}
        self.results: dict = {}
        self.probe = None

    def spawn(self, req):
        self.requests.append(req)
        self.events.append(("spawn", req.role))
        if self.probe:
            self.probe(req)
        script = self.scripts.get(req.role)
        if script:
            script(req)
        res = self.results.get(req.role)
        return res(req) if res else pa.SpawnResult(req.session_id, 0, False, "ok", (), False)

    def auth_method(self, env):
        return self.auth

    def current_session_id(self):
        return "holder-1"


@pytest.fixture
def proj(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    for d in ("design/feat", "reports/feat", "briefs", ".claude", ".sprint", "worktrees"):
        (root / d).mkdir(parents=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    (root / "design/feat/plan.md").write_text(PLAN)
    (root / "briefs/feat.md").write_text("# b\n")
    (root / "briefs/feat.criteria.yaml").write_text(yaml.safe_dump(CRITERIA))
    set_config(root, suite="true")
    set_phase(root, "building")
    ledger.append("sprint_started", feature=F)
    return root


def set_config(root, *, suite=None, drive_block=True, auth="claude.ai"):
    cfg = yaml.safe_load(_TEMPLATE.read_text())
    cfg.pop("drive", None)
    if drive_block:
        cfg["drive"] = {"auth": auth, "spawn_timeout_min": 7}
        if suite:
            cfg["drive"]["suite_command"] = suite
    (root / ".claude" / "sprint-config.yaml").write_text(yaml.safe_dump(cfg))


def set_phase(root, phase, feature=F):
    phases.set_sprint_state({"feature": feature, "phase": phase}, root)


class Rig:
    """Recorder for `_gate` and `_prusik` (tier resolve stays real), plus the adapter."""

    def __init__(self, root, monkeypatch):
        self.root, self.events = root, []
        self.rcs: dict[str, list[int]] = {}
        self.tails: dict[str, str] = {}
        self.adapter = FakeAdapter(self.events)
        rig = self
        orig_prusik = drive._Drive._prusik

        def fake_gate(self, *a):
            rig.events.append(("gate", *a))
            key = a[0] if a[0] != "fix-round" else f"fix-round {a[1]}"
            queue = rig.rcs.get(key)
            rc = queue.pop(0) if queue else rig.default_rc(a)
            if rc == 0:
                rig.effect(a)
            return rc, rig.tails.get(key, "")

        def fake_prusik(self, *a, cwd=None):
            if a[0] == "prove":
                rig.events.append(("prove", str(cwd)))
                queue = rig.rcs.get("prove")
                return (queue.pop(0) if queue else 0), ""
            return orig_prusik(self, *a, cwd=cwd)

        monkeypatch.setattr(drive._Drive, "_gate", fake_gate)
        monkeypatch.setattr(drive._Drive, "_prusik", fake_prusik)

    @staticmethod
    def default_rc(a):
        return 1 if a[0] == "verdict-current" else 0

    def effect(self, a):
        if a[0] == "advance":
            set_phase(self.root, a[1])
        elif a[0] == "prove-red":
            ledger.append("criterion_red_baseline", feature=F, id="c1",
                          verify_command="false", exit_code=1)
        elif a[0] == "fix-round" and a[1] == "start":
            fix_round.start(F, self.root)
        elif a[0] == "fix-round" and a[1] == "end":
            fix_round.end(F, self.root)

    def run(self, feature=F):
        out = io.StringIO()
        rc = drive.run(feature, adapter=self.adapter, out=out)
        self.out = out.getvalue()
        return rc

    def escalation(self):
        return json.loads((self.root / "reports/feat/drive-escalation.json").read_text())


def write_report(root, name, text):
    def script(req):
        (root / "reports/feat" / name).write_text(text)
    return script


@pytest.fixture
def rig(proj, monkeypatch):
    r = Rig(proj, monkeypatch)
    root = proj
    r.adapter.scripts = {
        "plan-critic": write_report(root, "plan-approval.txt", "APPROVED\n"),
        "regression-sentinel": write_report(root, "regression.txt", "PASS\n"),
        "conventions-enforcer": write_report(root, "conventions.txt", "PASS\n"),
    }
    return r


def escalated_events():
    return [e for e in ledger.read_all() if e["event"] == "drive_escalated"]


# ── planning ──────────────────────────────────────────────────────────────────────────────

def _backdate(path, seconds=3600):
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_planning_gate_plan_failure_respawns_the_planner_before_the_critic(proj):
    set_phase(proj, "planning")
    (proj / "design/feat/plan.md").write_text("# not a plan\n")
    _backdate(proj / "design/feat/plan.md")
    adapter = FakeAdapter([])
    out = io.StringIO()
    assert drive.run(F, adapter=adapter, out=out) == 3          # real `gate plan`
    assert [r.role for r in adapter.requests] == ["feature-planner"], "no critic spawn"
    assert "gate plan" in adapter.requests[0].prompt
    esc = json.loads((proj / "reports/feat/drive-escalation.json").read_text())
    assert esc["kind"] == "spawn_failed" and esc["phase"] == "planning" and esc["evidence"]
    ev = escalated_events()
    assert len(ev) == 1 and ev[0]["kind"] == "spawn_failed" and ev[0]["feature"] == F
    assert "gate plan" in (proj / "reports/feat/drive.log").read_text()


def test_planning_rejection_escalates_and_never_advances(rig, proj):
    set_phase(proj, "planning")
    rig.adapter.scripts["plan-critic"] = write_report(proj, "plan-approval.txt",
                                                      "REJECTED\n- PC-1 [must-fix] x\n")
    rig.adapter.scripts["feature-planner"] = lambda req: (
        proj / "design/feat/plan.md").write_text(PLAN)
    assert rig.run() == 3
    assert rig.escalation()["kind"] == "plan_rejected"
    assert not [e for e in rig.events if e[:2] == ("gate", "advance")]


def test_planning_approved_then_advances_to_building_and_on(rig, proj):
    set_phase(proj, "planning")
    rig.rcs["verdict-current"] = [1]
    rig.run()
    assert rig.events[:3] == [("gate", "plan", F), ("gate", "verdict-current", "--role",
                                                    "plan-critic", "--feature", F, "--artifact",
                                                    f"design/{F}/plan.md"),
                              ("spawn", "plan-critic")]
    assert ("gate", "advance", "building", "--feature", F) in rig.events


# ── building ──────────────────────────────────────────────────────────────────────────────

def test_building_order_prove_red_then_builders_then_suite_then_advance(rig):
    rig.run()
    seq = [e for e in rig.events if e[0] in ("spawn", "prove") or e[:2] == ("gate", "prove-red")
           or e[:2] == ("gate", "advance")]
    first = [e[:2] if e[0] == "gate" else e[0] for e in seq][:4]
    assert first == [("gate", "prove-red"), "spawn", "prove", ("gate", "advance")]
    assert seq[0] == ("gate", "prove-red", "--feature", F)
    assert seq[2][1].endswith("worktrees/backend-builder")


def test_building_skips_prove_red_when_every_baseline_exists(rig):
    ledger.append("criterion_red_baseline", feature=F, id="c1", verify_command="false",
                  exit_code=1)
    rig.run()
    assert ("gate", "prove-red", "--feature", F) not in rig.events
    assert ("spawn", "backend-builder") in rig.events


def test_building_prove_red_that_captures_nothing_escalates(rig):
    rig.effect = lambda a: None                         # prove-red "succeeds" but records none
    assert rig.run() == 3
    assert rig.escalation()["kind"] == "prove_red_failed" and ("spawn", "backend-builder") not in rig.events


def test_building_suite_failure_escalates_and_does_not_advance(rig):
    rig.rcs["prove"] = [1]
    assert rig.run() == 3
    assert rig.escalation()["kind"] == "suite_failed"
    assert not [e for e in rig.events if e[:3] == ("gate", "advance", "reviewing")]


def test_no_drive_block_defaults_auth_and_building_needs_a_suite_command(rig, proj):
    set_config(proj, drive_block=False)
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "suite_failed" and "drive.suite_command" in esc["reason"]
    assert rig.adapter.requests == [], "no builder is spawned before the suite is known"
    assert "[drive] auth: claude.ai (login)" in rig.out


def test_building_resume_reuses_an_existing_worktree(rig, proj, monkeypatch):
    (proj / ".git").mkdir()
    (proj / "worktrees/backend-builder").mkdir()
    called = []
    orig = drive._Drive._cli

    def cli(self, argv, cwd=None):
        called.append(argv)
        return (1, "unexpected git") if argv[0] == "git" else orig(self, argv, cwd)
    monkeypatch.setattr(drive._Drive, "_cli", cli)
    rig.run()
    assert ("spawn", "backend-builder") in rig.events
    assert not [c for c in called if c[:3] == ["git", "worktree", "add"]
                and "worktrees/backend-builder" in c], "no `git worktree add` on resume"


def test_building_creates_the_worktree_for_a_new_builder(rig, proj, monkeypatch):
    (proj / ".git").mkdir()
    called = []
    orig = drive._Drive._cli

    def cli(self, argv, cwd=None):
        if argv[0] != "git":
            return orig(self, argv, cwd)
        called.append(argv)
        if argv[1] == "rev-parse":
            return 1, ""                          # no such branch yet
        if argv[1:3] == ["worktree", "add"] and "worktrees/backend-builder" in argv:
            (proj / "worktrees/backend-builder").mkdir()
        return 0, ""
    monkeypatch.setattr(drive._Drive, "_cli", cli)
    rig.run()
    adds = [c for c in called if c[:3] == ["git", "worktree", "add"] and "--detach" not in c]
    assert adds == [["git", "worktree", "add", "-B", "sprint/feat-backend-builder",
                     "worktrees/backend-builder", "HEAD"]]


def test_plan_without_a_builder_role_escalates(rig, proj):
    (proj / "design/feat/plan.md").write_text(PLAN.replace("**backend-builder**", "**scribe**"))
    assert rig.run() == 3
    assert "no builder role" in rig.escalation()["reason"]


# ── reviewing ─────────────────────────────────────────────────────────────────────────────

def test_reviewing_fail_runs_a_fix_round_then_rejudges_and_advances(rig, proj):
    set_phase(proj, "reviewing")
    verdicts = iter(["FAIL\n- bug\n", "PASS\n"])
    rig.adapter.scripts["regression-sentinel"] = lambda req: (
        proj / "reports/feat/regression.txt").write_text(next(verdicts))
    rig.run()
    names = [e[1:3] if e[0] == "gate" else e for e in rig.events if e[0] == "spawn"
             or e[:2] in (("gate", "fix-round"), ("gate", "advance"))]
    assert names == [("spawn", "regression-sentinel"), ("fix-round", "start"),
                     ("spawn", "backend-builder"), ("fix-round", "end"),
                     ("spawn", "regression-sentinel"), ("spawn", "conventions-enforcer"),
                     ("advance", "integrating"), ("spawn", "integrator")]
    fix_req = [r for r in rig.adapter.requests if r.role == "backend-builder"][0]
    assert "fix round 1" in fix_req.prompt and "regression.txt" in fix_req.prompt


def test_fix_round_cap_escalates(rig, proj):
    set_phase(proj, "reviewing")
    rig.adapter.scripts["regression-sentinel"] = write_report(proj, "regression.txt", "FAIL\n")
    rig.rcs["fix-round start"] = [2]
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "fix_round_cap" and esc["evidence"][0].endswith("regression.txt")


def test_reviewing_a_stale_pass_is_not_this_spawns_verdict(rig, proj):
    set_phase(proj, "reviewing")
    report = proj / "reports/feat/regression.txt"
    report.write_text("PASS\n")
    old = time.time() - 3600
    os.utime(report, (old, old))
    rig.adapter.scripts["regression-sentinel"] = lambda req: None     # writes nothing
    assert rig.run() == 3
    assert rig.escalation()["kind"] == "spawn_failed"
    assert report.read_text() == "PASS\n", "drive never rewrites a report"


def test_reviewing_resumes_an_active_fix_round_before_starting_another(rig, proj):
    set_phase(proj, "reviewing")
    fix_round.start(F, proj)
    (proj / "reports/feat/regression.txt").write_text("FAIL\n- x\n")
    rig.run()
    gates = [e[1:3] for e in rig.events if e[:2] == ("gate", "fix-round")]
    assert gates[0] == ("fix-round", "end"), "end the resumed round before any start"
    assert [e for e in rig.events if e[0] == "spawn"][0] == ("spawn", "backend-builder")
    assert rig.events.index(("spawn", "backend-builder")) < rig.events.index(
        ("gate", "fix-round", "end", "--feature", F))
    assert not any(e[2:3] == ("start",) for e in rig.events if e[:2] == ("gate", "fix-round"))


def test_resume_with_a_marker_owned_by_another_feature_is_left_alone(rig, proj):
    set_phase(proj, "reviewing")
    fix_round._marker_path(proj).write_text(json.dumps({"feature": "other", "round": 1}))
    rig.run()
    assert not any(e[:3] == ("gate", "fix-round", "end") for e in rig.events), \
        "a marker owned by another feature is left to fix-round start's reaping"
    first = [e for e in rig.events if e[0] == "spawn"][0]
    assert first == ("spawn", "regression-sentinel"), "no builder resume for a foreign marker"


def test_missing_report_after_a_reviewer_spawn_escalates(rig, proj):
    set_phase(proj, "reviewing")
    rig.adapter.scripts.pop("conventions-enforcer")
    assert rig.run() == 3 and rig.escalation()["kind"] == "spawn_failed"


# ── escalation kinds from the spawn ───────────────────────────────────────────────────────

def test_permission_denial_escalates_naming_the_tool(rig):
    rig.adapter.results["backend-builder"] = lambda req: pa.SpawnResult(
        req.session_id, 0, False, "ok", ({"tool_name": "Bash"},), False)
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "permission_denied" and "Bash" in esc["reason"]
    assert esc["role"] == "backend-builder"


def test_error_and_timeout_results_escalate_spawn_failed(rig):
    rig.adapter.results["backend-builder"] = lambda req: pa.SpawnResult(
        req.session_id, -1, True, "timed out after 420s", (), True)
    assert rig.run() == 3 and rig.escalation()["kind"] == "spawn_failed"


def test_tier_resolve_failure_escalates_without_spawning(rig, monkeypatch):
    orig = drive._Drive._prusik
    monkeypatch.setattr(drive._Drive, "_prusik", lambda self, *a, cwd=None: (
        (1, "tier broke") if a[0] == "tier" else orig(self, *a, cwd=cwd)))
    assert rig.run() == 3
    assert rig.escalation()["kind"] == "tier_unresolved" and rig.adapter.requests == []


def test_the_tier_model_reaches_the_spawn(rig, monkeypatch):
    monkeypatch.setattr(drive._Drive, "_model", lambda self, role: "opus")
    rig.run()
    assert rig.adapter.requests[0].model == "opus"
    assert rig.adapter.requests[0].timeout_s == 7 * 60, "drive.spawn_timeout_min"


def test_model_off_means_no_model(rig):
    rig.run()                                            # real `tier resolve`: tiering is off
    assert rig.adapter.requests[0].model is None


# ── auth and lease ────────────────────────────────────────────────────────────────────────

def test_auth_mismatch_fails_closed_before_any_spawn(rig):
    rig.adapter.auth = ("api_key", "ANTHROPIC_API_KEY")
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "auth_mismatch" and rig.adapter.requests == []
    assert "api_key" in esc["reason"] and "ANTHROPIC_API_KEY" in esc["reason"]
    assert "[drive] auth: api_key (ANTHROPIC_API_KEY)" in rig.out


def test_a_live_foreign_lease_is_writer_busy(rig, proj):
    main_writer.check_and_touch(proj, "somebody-else", feature="x", phase="building")
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "writer_busy" and "somebody-e" in esc["reason"]
    assert rig.adapter.requests == []


@pytest.mark.parametrize("setup,needle", [
    ("nosprint", "no active sprint"),
    ("other", "not 'feat'"),
    ("briefing", "drive takes over at scoping"),
])
def test_not_drivable_exits_2(rig, proj, setup, needle):
    if setup == "nosprint":
        (proj / ".sprint/state.json").unlink()
    elif setup == "other":
        set_phase(proj, "building", feature="other")
    else:
        set_phase(proj, "briefing")
    assert rig.run() == 2
    assert needle in rig.out
    assert rig.adapter.requests == [] and not (proj / "reports/feat/drive-escalation.json").exists()


# ── shared-tree writes no command text names (fb-1303f39d14a6) ───────────────────────────

def _git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _git_project(root):
    """proj as a git repo with src/a.py committed, the shape drive runs in."""
    (root / "src").mkdir(exist_ok=True)
    (root / "src/a.py").write_text("x = 1\n")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "src/a.py")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")


def test_a_reviewer_git_apply_into_the_root_halts_drive_and_leaves_the_files(rig, proj):
    """The field shape: regression-sentinel ran `git diff ... | git apply` in the project root
    during reviewing. Real git writes the files; drive halts and keeps them for a person."""
    _git_project(proj)
    set_phase(proj, "reviewing")
    patch = proj.parent / "builder.diff"
    patch.write_text("--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")

    def sentinel(req):
        (proj / "reports/feat/regression.txt").write_text("PASS\n")
        subprocess.run(["git", "apply", str(patch)], cwd=proj, check=True)
    rig.adapter.scripts["regression-sentinel"] = sentinel
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "tree_written" and esc["role"] == "regression-sentinel"
    assert esc["evidence"] == ["src/a.py"]
    assert (proj / "src/a.py").read_text() == "x = 2\n"          # left in place
    assert phases.current_sprint_state(proj)["phase"] == "reviewing"
    assert ("spawn", "conventions-enforcer") not in rig.events
    written = [e for e in ledger.read_all() if e["event"] == "drive_tree_written"]
    assert written and written[-1]["paths"] == ["src/a.py"]


@pytest.mark.parametrize("change", ["write", "create", "delete"])
def test_any_change_to_an_unwritable_root_file_halts_drive(rig, proj, change):
    (proj / "src").mkdir()
    (proj / "src/a.py").write_text("x = 1\n")
    set_phase(proj, "reviewing")

    def sentinel(req):
        (proj / "reports/feat/regression.txt").write_text("PASS\n")
        if change == "write":
            (proj / "src/a.py").write_text("x = 22\n")
        elif change == "create":
            (proj / "src/new.py").write_text("y = 1\n")
        else:
            (proj / "src/a.py").unlink()
    rig.adapter.scripts["regression-sentinel"] = sentinel
    assert rig.run() == 3
    assert rig.escalation()["evidence"] == ["src/new.py" if change == "create" else "src/a.py"]


def test_a_builder_root_write_in_building_halts_before_the_suite(rig, proj):
    (proj / "src").mkdir()
    (proj / "src/a.py").write_text("x = 1\n")
    rig.adapter.scripts["backend-builder"] = (
        lambda req: (proj / "src/a.py").write_text("x = 3\n"))
    assert rig.run() == 3
    assert rig.escalation()["kind"] == "tree_written"
    assert not [e for e in rig.events if e[0] == "prove"]


def test_writes_the_phase_allows_never_halt_drive(rig, proj):
    """What must survive: a builder's own worktree, reports/, and the integrator's root
    merge all stay silent, in git mode too."""
    _git_project(proj)

    def builder(req):
        wt = proj / "worktrees/backend-builder"
        wt.mkdir(parents=True, exist_ok=True)
        (wt / "b.py").write_text("b = 1\n")
        (proj / "reports/feat/build.md").write_text("built\n")

    def integrator(req):
        (proj / "src/a.py").write_text("x = 9\n")
        (proj / "src/merged.py").write_text("m = 1\n")
    rig.adapter.scripts["backend-builder"] = builder
    rig.adapter.scripts["integrator"] = integrator
    assert rig.run() == 0, rig.out
    assert not [e for e in ledger.read_all() if e["event"] == "drive_tree_written"]


# ── bounded output, bookkeeping ───────────────────────────────────────────────────────────

def test_clean_run_from_reviewing_completes_and_clears_the_stale_escalation(rig, proj):
    set_phase(proj, "reviewing")
    (proj / "reports/feat/drive-escalation.json").write_text("{}")
    assert rig.run() == 0
    assert not (proj / "reports/feat/drive-escalation.json").exists()
    lines = rig.out.strip().splitlines()
    assert len(lines) <= 20 and lines[0] == "[drive] auth: claude.ai (login)"
    assert lines[-1].startswith("[drive] complete: feat")
    done = [e for e in rig.events if e[:2] == ("gate", "sprint-complete")]
    assert len(done) == 1 and done[0][2:5] == ("--feature", F, "--duration-min")
    assert ("spawn", "integrator") in rig.events


def test_escalation_output_is_bounded_and_names_the_json(rig, proj):
    set_phase(proj, "planning")
    rig.rcs["plan"] = [2]
    rig.tails["plan"] = "\n".join(f"error line {i}" for i in range(40))
    _backdate(proj / "design/feat/plan.md")
    assert rig.run() == 3
    lines = rig.out.strip().splitlines()
    assert len(lines) <= 20
    assert lines[-1] == "details: reports/feat/drive-escalation.json"
    assert lines[1].startswith("[drive] ESCALATION spawn_failed in planning")


def test_each_spawn_tags_the_registry_and_delegates_the_lease(rig, proj):
    seen = []

    def probe(req):
        reg = json.loads(usage.registry_path().read_text())
        lease = main_writer.load(proj)
        seen.append((reg[req.session_id], lease))
    rig.adapter.probe = probe
    rig.run()
    assert seen
    for (entry, lease), req in zip(seen, rig.adapter.requests):
        assert entry["role"] == req.role and entry["feature"] == F
        assert lease["session"] == "holder-1" and req.session_id in lease["delegates"]
    assert "CLAUDE_CODE_SESSION_ID" not in rig.adapter.requests[0].env
    events = [e for e in ledger.read_all() if e["event"] in ("drive_step", "writer_delegated")]
    assert {e["event"] for e in events} == {"drive_step", "writer_delegated"}


def test_the_tick_heartbeat_refreshes_the_lease(rig, proj):
    result = {}

    def probe(req):
        lease = main_writer.load(proj)
        lease["refreshed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        main_writer._write(proj, lease)
        old = main_writer.load(proj)["refreshed_at"]
        req.on_tick()
        result["old"], result["new"] = old, main_writer.load(proj)["refreshed_at"]
        result["tick_s"] = req.tick_s
    rig.adapter.probe = probe
    rig.run()
    assert result["new"] > result["old"] and result["tick_s"] <= 300


def test_drive_owns_the_phase_while_it_runs_and_lets_go_after(rig, proj):
    # moat-finding: fb-5861062129e7
    seen = []
    rig.adapter.probe = lambda req: seen.append(main_writer.live_owner(proj, F, None))
    assert rig.run() == 0
    assert seen and all(seen)
    assert "driver" not in main_writer.load(proj)
    assert not main_writer.live_owner(proj, F, None)


def test_an_escalated_drive_lets_go_too(rig, proj):
    # moat-finding: fb-5861062129e7
    rig.rcs["prove"] = [1]
    assert rig.run() == 3
    assert not main_writer.live_owner(proj, F, None)


def test_escalation_kinds_are_the_documented_set():
    assert set(drive.ESCALATION_KINDS) == {
        "scope_rejected", "triage_rejected", "solo_handback", "plan_rejected",
        "prove_red_failed", "suite_failed", "fix_round_cap", "permission_denied",
        "spawn_failed", "tier_unresolved", "auth_mismatch", "writer_busy",
        "advance_blocked", "tree_written"}
    with pytest.raises(ValueError):
        drive.Escalation("made_up", "building", None, "x")


# ── real git: worktree recovery (observed-writes, 2026-11-16) ─────────────────────────────

def _git(root, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _real_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _bare_drive(repo, feature="feat"):
    import io
    d = drive._Drive(feature, None, io.StringIO())
    d.root = repo
    return d


def test_worktree_add_recovers_a_removed_but_registered_worktree(tmp_path):
    """A prior sprint's worktree dir deleted without `git worktree remove` stays
    registered; `git worktree add` at that path then fails rc 128."""
    import shutil
    repo = _real_repo(tmp_path)
    _git(repo, "worktree", "add", "-q", "-b", "sprint/old-backend-builder",
         "worktrees/backend-builder", "HEAD")
    shutil.rmtree(repo / "worktrees/backend-builder")
    wt = _bare_drive(repo)._worktree("backend-builder")
    assert (wt / "a.txt").is_file()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "sprint/feat-backend-builder"


def test_worktree_retry_reuses_the_branch_and_keeps_its_commits(tmp_path):
    """A branch left by an earlier attempt is reused, never reset: a builder commit on it
    survives the retry."""
    import shutil
    repo = _real_repo(tmp_path)
    _git(repo, "worktree", "add", "-q", "-b", "sprint/feat-backend-builder",
         "worktrees/backend-builder", "HEAD")
    (repo / "worktrees/backend-builder/b.txt").write_text("builder work\n")
    _git(repo / "worktrees/backend-builder", "add", "b.txt")
    _git(repo / "worktrees/backend-builder", "commit", "-q", "-m", "builder")
    work = _git(repo, "rev-parse", "sprint/feat-backend-builder")
    shutil.rmtree(repo / "worktrees/backend-builder")
    wt = _bare_drive(repo)._worktree("backend-builder")
    assert _git(wt, "rev-parse", "HEAD") == work
    assert (wt / "b.txt").read_text() == "builder work\n"


def test_worktree_prune_keeps_a_live_worktree_registered(tmp_path):
    repo = _real_repo(tmp_path)
    _git(repo, "worktree", "add", "-q", "-b", "sprint/feat-test-writer",
         "worktrees/test-writer", "HEAD")
    _bare_drive(repo)._worktree("backend-builder")
    listed = _git(repo, "worktree", "list")
    assert "worktrees/test-writer" in listed and "worktrees/backend-builder" in listed


def test_worktree_retry_moves_an_empty_leftover_branch_to_head(tmp_path):
    """A leftover branch with no commits of its own (a failed add) restarts from HEAD, so
    the builder does not build on a base that lacks commits made since."""
    repo = _real_repo(tmp_path)
    _git(repo, "branch", "sprint/feat-backend-builder")
    (repo / "c.txt").write_text("later\n")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-q", "-m", "later")
    wt = _bare_drive(repo)._worktree("backend-builder")
    assert _git(wt, "rev-parse", "HEAD") == _git(repo, "rev-parse", "HEAD")


# ── real git: the building proof runs on the merged builder branches (fb-1e4da436fcfc) ───

def _builder_branch(repo, role, path, text, feature="feat"):
    _git(repo, "checkout", "-q", "-b", f"sprint/{feature}-{role}", "main")
    (repo / path).write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", role)
    _git(repo, "checkout", "-q", "main")


def _proving_drive(repo, monkeypatch, suite_seen):
    import subprocess

    def fake_prusik(self, *a, cwd=None):
        assert a[:2] == ("prove", "--kind")
        suite_seen.append(Path(cwd))
        p = subprocess.run(list(a[a.index("--") + 1:]), cwd=cwd, capture_output=True,
                           text=True)
        return p.returncode, p.stdout + p.stderr
    monkeypatch.setattr(drive._Drive, "_prusik", fake_prusik)
    d = _bare_drive(repo)
    d.reports.mkdir(parents=True, exist_ok=True)
    return d


def test_split_code_and_test_builders_prove_green_together(tmp_path, monkeypatch):
    repo = _real_repo(tmp_path)
    _builder_branch(repo, "backend-builder", "code.py", "X = 1\n")
    _builder_branch(repo, "test-writer", "test_code.py", "from code import X\n")
    seen = []
    d = _proving_drive(repo, monkeypatch, seen)
    d._prove_combined(["backend-builder", "test-writer"],
                      "test -f code.py && test -f test_code.py")
    (view,) = seen
    assert not view.is_relative_to(repo), "the view is outside the project tree"
    assert not view.exists() and not view.parent.exists(), "the scratch view is removed"
    assert "view" not in _git(repo, "worktree", "list")
    assert _git(repo, "status", "--porcelain") == "?? reports/", \
        "only drive's own log is written in the project tree"


def test_a_red_merged_suite_escalates_and_still_removes_the_view(tmp_path, monkeypatch):
    repo = _real_repo(tmp_path)
    _builder_branch(repo, "backend-builder", "code.py", "X = 1\n")
    seen = []
    d = _proving_drive(repo, monkeypatch, seen)
    with pytest.raises(drive.Escalation) as e:
        d._prove_combined(["backend-builder"], "test -f test_code.py")
    assert e.value.kind == "suite_failed" and "merged builder branches" in e.value.reason
    assert not seen[0].exists() and "view" not in _git(repo, "worktree", "list")


def test_conflicting_builder_branches_escalate_naming_the_branch(tmp_path, monkeypatch):
    repo = _real_repo(tmp_path)
    _builder_branch(repo, "backend-builder", "a.txt", "backend\n")
    _builder_branch(repo, "test-writer", "a.txt", "tests\n")
    seen = []
    d = _proving_drive(repo, monkeypatch, seen)
    with pytest.raises(drive.Escalation) as e:
        d._prove_combined(["backend-builder", "test-writer"], "true")
    assert e.value.role == "test-writer" and "sprint/feat-test-writer" in e.value.reason
    assert seen == [], "no suite runs on a view that did not merge"
    assert len(_git(repo, "worktree", "list").splitlines()) == 1
