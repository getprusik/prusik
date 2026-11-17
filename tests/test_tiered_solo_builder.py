"""`prusik drive` runs the solo build and solo fix rounds on a tier-resolved `solo-builder`.

- fb-0653168f8417: the solo build ran in the calling session, so it ran on whatever model that
  session had, not on the bounded class the tier policy resolves for a builder.
- fb-95d0c641cd90: the solo worktree must record its fork point before the build starts.

The trivial lane is still built by hand: a reviewer FAIL there escalates `trivial_fix`, and no
path spawns `solo-builder` for it (the PC-9 / PC-4 cases below).

Written from design/tiered-solo-builder/plan.md (Test plan) beside the builder.

moat-finding: fb-0653168f8417
moat-finding: fb-95d0c641cd90
"""

from __future__ import annotations

import ast
import io
import re
from pathlib import Path

import pytest
import yaml

from prusik import drive, fix_round, ledger, model_tier, phases, usage
from prusik import worktrees as wtmod
from tests.test_drive_design_phases import _fake_triage
from tests.test_review_round_integrity import _git
from tests.test_sprint_drive import (
    F, escalated_events, proj, rig, set_config, set_phase, write_report,  # noqa: F401
)

_REPO = Path(__file__).resolve().parent.parent
_AGENTS = _REPO / "prusik" / "templates" / ".claude" / "agents"
SENTINEL = "haiku"          # the bounded ref in the profile below; no other tier maps to it
BUILDER_ROLES_ALL = ("backend-builder", "frontend-builder", "test-writer", "solo-builder")
_OLD_KIND = "solo" + "_handback"          # spelled in two halves: this file must not match


# ── fixtures ───────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def gproj(proj):
    """`proj` as a real git repo, so the solo worktree and its branch are real."""
    _git(proj, "init", "-q", "-b", "main")
    _git(proj, "config", "user.email", "t@example.invalid")
    _git(proj, "config", "user.name", "t")
    (proj / ".gitignore").write_text(".sprint/\nworktrees/\nreports/\ndecisions/\n")
    _git(proj, "add", ".gitignore")
    _git(proj, "commit", "-q", "-m", "init")
    return proj


def _tiering(root: Path, bounded: str = SENTINEL, *, profile: bool = True) -> None:
    """Enable model tiering; the bounded class names a model nothing else maps to."""
    cfg_path = root / ".claude" / "sprint-config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["model_tiering"] = {"enabled": True}
    cfg_path.write_text(yaml.safe_dump(cfg))
    if profile:
        (root / ".claude" / "model-profile.yaml").write_text(yaml.safe_dump({
            "schema_version": "1.0",
            "tiers": {"high_stakes": {"model": "opus"}, "bounded": {"model": bounded},
                      "mechanical": {"model": "sonnet"}},
            "stakes": {"low": -100, "high": 100}, "risk_weights": {}}))


def _commit_in_solo(root: Path, name: str = "code.py", text: str = "X = 1\n") -> None:
    wt = root / "worktrees" / "solo"
    (wt / name).write_text(text)
    _git(wt, "add", name)
    _git(wt, "commit", "-q", "-m", f"build {name}")


def _builds(root: Path):
    """A builder that commits a new file every time it is spawned."""
    count = iter(range(1000))

    def script(req):
        _commit_in_solo(root, f"code{next(count)}.py")
    return script


def _solo_spawns(rig) -> list:
    return [r for r in rig.adapter.requests if r.role == "solo-builder"]


def _seed_trivial(root: Path, phase: str = "reviewing") -> None:
    """A trivial sprint the real way: sprint_started carries lane=trivial, the state carries
    it too, and the by-hand scoping -> solo_execute -> reviewing advances are recorded."""
    ledger.append("sprint_started", feature=F, lane="trivial")
    ledger.append("phase_advance", from_phase="scoping", to_phase="solo_execute", feature=F)
    ledger.append("phase_advance", from_phase="solo_execute", to_phase="reviewing",
                  feature=F)
    phases.set_sprint_state({"feature": F, "phase": phase, "lane": "trivial"}, root)


def _fail_then_pass(root: Path):
    verdicts = iter(["FAIL\n- bug\n", "PASS\n"])
    return lambda req: (root / "reports/feat/regression.txt").write_text(next(verdicts))


def _lane_of(phase: str = "reviewing") -> str:
    d = drive._Drive(F, None, io.StringIO())
    d.phase = phase
    return d._lane()


def _triage_solo(rig, root, monkeypatch):
    set_phase(root, "triage")
    _fake_triage(monkeypatch, root, {
        "mode": "solo", "reason": "r", "scope_summary": {"size": "S", "domains": [],
                                                         "risks": []},
        "brief_meta": {"priority": "P2"}})


# ── happy path: the tier is resolved, never inherited ─────────────────────────────────────

def test_solo_triage_spawns_one_solo_builder_on_the_bounded_model(rig, gproj, monkeypatch):
    _tiering(gproj)
    rig.adapter.scripts["solo-builder"] = _builds(gproj)
    _triage_solo(rig, gproj, monkeypatch)
    rig.run()
    spawns = _solo_spawns(rig)
    assert len(spawns) == 1
    assert spawns[0].model == SENTINEL, "the profile's bounded ref, not the session's model"
    assert phases.current_sprint_state(gproj)["phase"] != "solo_execute"
    assert any(e[:3] == ("gate", "advance", "reviewing") for e in rig.events)
    assert _git(gproj / "worktrees/solo", "rev-parse", "--abbrev-ref", "HEAD") == \
        f"sprint/{F}-solo"
    assert escalated_events() == []


def test_the_tier_resolution_is_attributed_to_solo_builder(rig, gproj, monkeypatch):
    _tiering(gproj)
    _triage_solo(rig, gproj, monkeypatch)
    rig.run()
    got = [r for r in ledger.read_all()
           if r["event"] == "tier_resolved" and r.get("feature") == F
           and "solo-builder" in (r.get("roles") or {})]
    assert got, "a tier_resolved record whose roles name solo-builder"
    entry = got[-1]["roles"]["solo-builder"]
    assert entry["model"] == SENTINEL and entry["tier_class"] == "bounded"


def test_the_solo_worktree_fork_point_is_recorded_before_the_spawn(rig, gproj):
    """fb-95d0c641cd90: the build must not start on a worktree with no recorded fork."""
    set_phase(gproj, "solo_execute")
    seen = {}

    def probe(req):
        wt = gproj / "worktrees" / "solo"
        seen["exists"] = wt.exists()
        seen["base"] = wtmod.recorded_base(wt)
        seen["head"] = _git(gproj, "rev-parse", "HEAD")
    rig.adapter.probe = probe
    rig.run()
    assert seen["exists"] is True
    assert seen["base"] == seen["head"], "the base ref is the commit the worktree forked from"


# ── failures ───────────────────────────────────────────────────────────────────────────────

def test_a_missing_profile_escalates_tier_unresolved_with_zero_spawns(rig, gproj):
    _tiering(gproj, profile=False)                  # tiering on, no profile: fails closed
    set_phase(gproj, "solo_execute")
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "tier_unresolved" and esc["role"] == "solo-builder"
    assert rig.adapter.requests == []
    assert {e["kind"] for e in escalated_events()} == {"tier_unresolved"}


def test_a_failing_tier_resolve_never_falls_back_to_the_session_model(rig, proj, monkeypatch):
    set_phase(proj, "solo_execute")
    orig = drive._Drive._prusik
    monkeypatch.setattr(drive._Drive, "_prusik", lambda self, *a, cwd=None: (
        (1, "tier broke") if a[0] == "tier" else orig(self, *a, cwd=cwd)))
    assert rig.run() == 3
    assert rig.escalation()["kind"] == "tier_unresolved"
    assert rig.adapter.requests == [], "no request, so none carries the session model"


def test_no_suite_command_escalates_before_red_capture_or_any_spawn(rig, proj):
    set_config(proj, suite=None)
    set_phase(proj, "solo_execute")
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "suite_failed" and esc["phase"] == "solo_execute"
    assert rig.adapter.requests == []
    assert not any(e[:2] == ("gate", "prove-red") for e in rig.events), "before prove-red"
    assert not any(e[0] == "prove" for e in rig.events)


def test_a_red_solo_build_escalates_suite_failed_and_stays_in_solo_execute(rig, gproj):
    set_phase(gproj, "solo_execute")
    rig.adapter.scripts["solo-builder"] = _builds(gproj)
    rig.rcs["prove"] = [1]
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "suite_failed" and esc["phase"] == "solo_execute"
    assert phases.current_sprint_state(gproj)["phase"] == "solo_execute"
    assert not any(e[:3] == ("gate", "advance", "reviewing") for e in rig.events)


def test_red_build_without_git_names_the_solo_builder(rig, proj):
    """No .git: the suite is proven in worktrees/solo directly (the shared _prove_suite)."""
    set_phase(proj, "solo_execute")
    rig.rcs["prove"] = [1]
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "suite_failed" and esc["role"] == "solo-builder"
    assert esc["phase"] == "solo_execute"
    assert ("prove", str(proj / "worktrees" / "solo")) in rig.events


# ── resume ─────────────────────────────────────────────────────────────────────────────────

def _first_run_red(rig, root) -> None:
    """Run 1: the solo-builder builds and commits, the suite proves red, drive stops."""
    ledger.append("phase_advance", from_phase="triage", to_phase="solo_execute", feature=F)
    set_phase(root, "solo_execute")
    rig.adapter.scripts["solo-builder"] = _builds(root)
    rig.rcs["prove"] = [1]
    assert rig.run() == 3
    assert len(_solo_spawns(rig)) == 1
    rig.adapter.requests.clear()
    rig.events.clear()


def test_a_rerun_after_a_finished_build_does_not_respawn_but_still_proves(rig, gproj):
    _first_run_red(rig, gproj)
    rig.run()
    assert _solo_spawns(rig) == []
    assert any(e[0] == "prove" for e in rig.events), "the suite is proven again"
    assert any(e[:3] == ("gate", "advance", "reviewing") for e in rig.events)


def test_a_commit_after_the_build_spawns_the_solo_builder_again(rig, gproj):
    _first_run_red(rig, gproj)
    _commit_in_solo(gproj, "later.py")
    rig.run()
    assert len(_solo_spawns(rig)) == 1


def test_a_build_recorded_before_a_rewind_does_not_count(rig, gproj):
    _first_run_red(rig, gproj)                       # drive_builder_done for the current tip
    ledger.append("phase_advance", from_phase="solo_execute", to_phase="reviewing",
                  feature=F)
    ledger.append("phase_advance", from_phase="reviewing", to_phase="solo_execute",
                  feature=F)                         # the rewind: a new visit, branch unchanged
    rig.run()
    assert len(_solo_spawns(rig)) == 1


def test_a_builder_done_enclosed_only_by_a_building_window_is_not_a_solo_build(rig, gproj):
    """The window key is self.phase: a hard-coded "building" would count this one."""
    wtmod.create(gproj, F, "solo")
    _commit_in_solo(gproj)
    tip = _git(gproj, "rev-parse", f"refs/heads/sprint/{F}-solo")
    ledger.append("phase_advance", from_phase="planning", to_phase="building", feature=F)
    ledger.append("drive_builder_done", feature=F, role="solo-builder", commit=tip)
    set_phase(gproj, "solo_execute")                 # no solo_execute window exists
    rig.run()
    assert len(_solo_spawns(rig)) == 1


def test_positive_control_a_builder_done_inside_the_solo_window_counts(rig, gproj):
    wtmod.create(gproj, F, "solo")
    _commit_in_solo(gproj)
    tip = _git(gproj, "rev-parse", f"refs/heads/sprint/{F}-solo")
    ledger.append("phase_advance", from_phase="triage", to_phase="solo_execute", feature=F)
    ledger.append("drive_builder_done", feature=F, role="solo-builder", commit=tip)
    set_phase(gproj, "solo_execute")
    rig.run()
    assert _solo_spawns(rig) == []


# ── solo vs team fix rounds ────────────────────────────────────────────────────────────────

def _solo_reviewing(rig, root) -> None:
    ledger.append("phase_advance", from_phase="triage", to_phase="solo_execute", feature=F)
    set_phase(root, "reviewing")
    rig.adapter.scripts["regression-sentinel"] = _fail_then_pass(root)


def test_a_solo_review_fail_spawns_solo_builder_with_the_failing_report(rig, proj):
    _solo_reviewing(rig, proj)
    assert rig.run() == 0
    (req,) = _solo_spawns(rig)
    assert f"reports/{F}/regression.txt" in req.prompt and "worktrees/solo/" in req.prompt
    assert [e[2] for e in rig.events if e[:2] == ("gate", "fix-round")] == ["start", "end"]
    assert not any(e["kind"] == "trivial_fix" for e in escalated_events())


def test_a_team_review_fail_spawns_plan_builders_never_solo_builder(rig, proj):
    set_phase(proj, "reviewing")
    rig.adapter.scripts["regression-sentinel"] = _fail_then_pass(proj)
    assert rig.run() == 0
    assert ("spawn", "backend-builder") in rig.events
    assert _solo_spawns(rig) == []


def test_resuming_an_open_solo_round_spawns_solo_builder(rig, proj):
    _solo_reviewing(rig, proj)
    fix_round.start(F, proj)                         # the round was left open
    (proj / "reports/feat/regression.txt").write_text("FAIL\n- bug\n")
    rig.adapter.scripts["regression-sentinel"] = write_report(proj, "regression.txt",
                                                              "PASS\n")
    assert rig.run() == 0
    assert len(_solo_spawns(rig)) == 1, "the round is not merely ended"
    assert [e[2] for e in rig.events if e[:2] == ("gate", "fix-round")] == ["end"]


# ── lane classification ────────────────────────────────────────────────────────────────────

def test_a_trivial_sprint_is_trivial_even_with_a_solo_execute_advance(proj):
    _seed_trivial(proj)
    assert _lane_of() == "trivial"
    d = drive._Drive(F, None, io.StringIO())
    assert d._is_solo() is False


def test_a_triage_solo_sprint_is_solo(proj):
    ledger.append("phase_advance", from_phase="triage", to_phase="solo_execute", feature=F)
    assert _lane_of() == "solo"
    assert drive._Drive(F, None, io.StringIO())._is_solo() is True


def test_no_solo_advance_is_team(proj):
    assert _lane_of() == "team"


def test_a_new_non_trivial_sprint_resets_a_previous_trivial_lane(proj):
    _seed_trivial(proj)
    ledger.append("sprint_started", feature=F)
    assert _lane_of() == "team"
    ledger.append("phase_advance", from_phase="triage", to_phase="solo_execute", feature=F)
    assert _lane_of() == "solo"


def test_another_features_trivial_lane_does_not_leak(proj):
    ledger.append("sprint_started", feature="other", lane="trivial")
    assert _lane_of() == "team"


# ── trivial lane: built by hand, never by solo-builder ────────────────────────────────────

def _cap_spawns(rig, limit: int = 8) -> None:
    """A regression that loops on spawns must fail the test, not hang the suite."""
    def probe(req):
        assert len(rig.adapter.requests) <= limit, "runaway spawns"
    rig.adapter.probe = probe


def _no_builder_spawned(rig) -> None:
    assert not [r for r in rig.adapter.requests if r.role in BUILDER_ROLES_ALL]


def test_trivial_fresh_reviewer_fail_escalates_trivial_fix(rig, proj):
    _seed_trivial(proj)
    _cap_spawns(rig)
    rig.adapter.scripts["regression-sentinel"] = write_report(proj, "regression.txt",
                                                              "FAIL\n- bug\n")
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "trivial_fix" and esc["phase"] == "reviewing"
    assert "regression.txt" in " ".join(esc["evidence"]) + esc["reason"]
    assert any(e.endswith("worktrees/solo") for e in esc["evidence"])
    assert "worktrees/solo/" in esc["reason"]
    _no_builder_spawned(rig)
    assert (fix_round.current_state(proj) or {}).get("round") == 1
    assert (fix_round.current_state(proj) or {}).get("feature") == F


def test_trivial_open_round_is_ended_and_re_reviewed_without_a_builder(rig, proj):
    _seed_trivial(proj)
    _cap_spawns(rig)
    rig.adapter.scripts["regression-sentinel"] = write_report(proj, "regression.txt",
                                                              "FAIL\n- bug\n")
    assert rig.run() == 3                            # opens round 1, escalates trivial_fix
    rig.adapter.requests.clear()
    rig.events.clear()
    rig.adapter.scripts["regression-sentinel"] = write_report(proj, "regression.txt",
                                                              "PASS\n")
    assert (proj / "reports/feat/regression.txt").read_text().startswith("FAIL")
    assert rig.run() == 0
    assert [e[2] for e in rig.events if e[:2] == ("gate", "fix-round")] == ["end"]
    assert "regression-sentinel" in [r.role for r in rig.adapter.requests]
    assert not any(r.role == "solo-builder" for r in rig.adapter.requests)
    _no_builder_spawned(rig)
    assert any(e[:3] == ("gate", "advance", "integrating") for e in rig.events)


def test_trivial_fix_builders_fails_closed_with_zero_spawns(rig, proj):
    _seed_trivial(proj)
    d = drive._Drive(F, rig.adapter, io.StringIO())
    d.phase = "reviewing"
    with pytest.raises(drive.Escalation) as e:
        d._fix_builders("regression.txt", 1)
    assert e.value.kind == "trivial_fix"
    assert rig.adapter.requests == []


def test_trivial_lane_is_refused_in_solo_execute(rig, proj):
    _seed_trivial(proj, phase="solo_execute")
    assert rig.run() == 2
    assert rig.adapter.requests == []
    assert "trivial lane" in rig.out


def test_trivial_fix_never_fires_for_a_triage_solo_sprint(rig, proj):
    _solo_reviewing(rig, proj)
    rig.run()
    assert all(e["kind"] != "trivial_fix" for e in escalated_events())


# ── constants, role spec, source scan ─────────────────────────────────────────────────────

def test_constants_are_two_independent_literals():
    assert drive.SOLO_WORKTREE == "solo" and drive.SOLO_BUILDER == "solo-builder"
    tree = ast.parse((_REPO / "prusik" / "drive.py").read_text())
    value = next(n.value for n in tree.body
                 if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "SOLO_BUILDER"
                         for t in n.targets))
    assert isinstance(value, ast.Constant) and value.value == "solo-builder"


def test_the_trivial_fix_kind_replaced_the_old_one():
    assert "trivial_fix" in drive.ESCALATION_KINDS and _OLD_KIND not in drive.ESCALATION_KINDS
    assert "solo_execute" in drive.DRIVEN


def test_solo_builder_is_a_packaged_bounded_role_the_session_does_not_pick():
    assert (_AGENTS / "solo-builder.md").is_file()
    assert "solo-builder" in usage.packaged_roles()
    assert model_tier.BASE_CLASS["solo-builder"] == "bounded"
    assert "solo-builder" in model_tier._REWOUND_ROLES
    assert "solo-builder" not in model_tier.FIXED_ROLES
    assert "solo-builder" not in drive.BUILDER_ROLES


def test_solo_builder_frontmatter_model_matches_the_reference_mapping():
    text = (_AGENTS / "solo-builder.md").read_text()
    head = text.split("---")[1]
    meta = yaml.safe_load(head)
    ref = yaml.safe_load((_REPO / "tests/fixtures/model_profiles/reference.yaml").read_text())
    assert meta["name"] == "solo-builder"
    assert meta["model"] == ref["tiers"][model_tier.BASE_CLASS["solo-builder"]]["model"]
    assert "Write" in meta["tools"] and "Bash" in meta["tools"]


def _block(text: str, start: str, stop: str | None = None) -> str:
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith(start))
    if stop is None:
        return lines[i]
    j = next(n for n, ln in enumerate(lines) if n > i and ln.startswith(stop))
    return "\n".join(lines[i:j])


@pytest.mark.parametrize("start,stop", [
    ("- If you discover you genuinely need", None),                 # deviation-log sentence
    ("5. **Before you report done", "6. "),                         # prove / baseline / flake
    ("- If the gate is genuinely WRONG", None),                     # friction filing
    ("- No destructive git ops", None),
])
def test_backend_builder_rules_appear_verbatim_in_solo_builder(start, stop):
    src = _block((_AGENTS / "backend-builder.md").read_text(), start, stop)
    assert src.strip()
    assert src in (_AGENTS / "solo-builder.md").read_text(), start


def test_no_old_handback_kind_remains_in_the_engine_or_the_tests():
    pat = re.compile(re.escape(_OLD_KIND))
    hits = []
    for top in ("prusik", "tests"):
        for p in (_REPO / top).rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix in (".pyc", ".png"):
                continue
            try:
                text = p.read_text()
            except UnicodeDecodeError:
                continue
            hits += [f"{p.relative_to(_REPO)}:{n}" for n, ln in enumerate(text.splitlines(), 1)
                     if pat.search(ln)]
    assert hits == []
