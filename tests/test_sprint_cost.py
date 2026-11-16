"""Three sprint-cost terms, each closed by a system-computed signal:

  - the stop hook stays silent while the reviewer that owns every missing exit artifact is in
    flight (fb-0fbea010311c);
  - scope-critic's tier follows scope.md's size and the stakes computed before triage
    (fb-9aaa814f7b3b);
  - leaving planning with a session grown past 40K prints a plan-boundary advisory
    (fb-9dac611d867c).
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from prusik import gate, ledger, model_tier, phases, usage
from tests._common import _copy_sprint_config, _mktmp_project

_REPO = Path(__file__).resolve().parent.parent
_REFERENCE = _REPO / "tests" / "fixtures" / "model_profiles" / "reference.yaml"
_F = "feat"


@pytest.fixture
def project():
    tmp = _mktmp_project()
    _copy_sprint_config(tmp)
    (tmp / ".sprint").mkdir(exist_ok=True)
    yield tmp
    shutil.rmtree(tmp)


def _ts(minute: int) -> str:
    return f"2026-11-14T10:{minute:02d}:00+00:00"


def _epoch(minute: int) -> float:
    from datetime import datetime
    return datetime.fromisoformat(_ts(minute)).timestamp()


def _ledger(root: Path, events) -> None:
    with open(root / ".sprint" / "ledger.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _entered(minute=0, event="sprint_started"):
    return {"ts": _ts(minute), "event": event, "feature": _F}


def _spawn(role, minute):
    return {"ts": _ts(minute), "event": "tier_resolved", "feature": _F,
            "roles": {role: {"tier_class": "bounded", "model": "sonnet", "source": "scope"}}}


def _stop(phase="scoping") -> int:
    phases.set_sprint_state({"phase": phase, "feature": _F})
    saved = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"stop_hook_active": False}))
    out = io.StringIO()
    try:
        with redirect_stdout(out):
            rc = gate.stop()
    finally:
        sys.stdin = saved
    return rc


def _scope_md(root: Path) -> None:
    path = root / "design" / _F / "scope.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Size\nS\n")


def _report(root: Path, name: str, text: str, minute: int) -> Path:
    path = root / "reports" / _F / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (_epoch(minute), _epoch(minute)))
    return path


def _reported(root: Path) -> bool:
    return (root / ".sprint" / gate._STOP_REPORTED_FILE).exists()


# ---------- fb-0fbea010311c: the stop hook and an in-flight reviewer ----------

def _scoped(root: Path, monkeypatch) -> None:
    """scope.md accepted by the schema check, so only scope-approval.txt is missing."""
    from prusik import schema
    monkeypatch.setattr(schema, "validate_scope", lambda *a, **k: (True, []))
    _scope_md(root)
    assert gate._unsatisfied_exit_items(
        phases.get_phase_spec(phases.load_sprint_config(), "scoping"), _F) == [
        ("reports/feat/scope-approval.txt", "does not exist")]


def test_waiting_on_the_owning_reviewer_is_silent(project, monkeypatch):
    # moat-finding: fb-0fbea010311c
    _scoped(project, monkeypatch)
    _ledger(project, [_entered(0), _spawn("scope-critic", 5)])
    assert _stop() == 0
    assert not _reported(project)


def test_no_spawn_recorded_still_blocks(project, monkeypatch):
    # moat-finding: fb-0fbea010311c
    _scoped(project, monkeypatch)
    _ledger(project, [_entered(0)])
    assert _stop() == 2


def test_a_spawn_from_before_the_latest_phase_entry_still_blocks(project, monkeypatch):
    # moat-finding: fb-0fbea010311c
    _scoped(project, monkeypatch)
    _ledger(project, [_entered(0), _spawn("scope-critic", 5),
                      _entered(10, "phase_rewind")])
    assert _stop() == 2


def test_a_landed_fail_report_is_not_in_flight(project, monkeypatch):
    # moat-finding: fb-0fbea010311c
    _scoped(project, monkeypatch)
    _report(project, "scope-approval.txt", "REJECTED\n", 8)
    _ledger(project, [_entered(0), _spawn("scope-critic", 5)])
    assert _stop() == 2


def test_a_stale_fail_report_from_before_the_spawn_is_in_flight(project, monkeypatch):
    # moat-finding: fb-0fbea010311c
    _scoped(project, monkeypatch)
    _report(project, "scope-approval.txt", "REJECTED\n", 2)
    _ledger(project, [_entered(0), _spawn("scope-critic", 5)])
    assert _stop() == 0


def test_an_unowned_missing_artifact_still_blocks(project):
    # moat-finding: fb-0fbea010311c
    _ledger(project, [_entered(0), _spawn("scope-critic", 5)])    # scope.md missing too
    assert _stop() == 2


def test_a_different_roles_spawn_does_not_count(project, monkeypatch):
    # moat-finding: fb-0fbea010311c
    _scoped(project, monkeypatch)
    _ledger(project, [_entered(0), _spawn("plan-critic", 5)])
    assert _stop() == 2


def _both_reviewers_missing(project, monkeypatch, spawns):
    _ledger(project, [_entered(0, "phase_advance"), *spawns])
    return _stop("reviewing")


def test_reviewing_silent_when_both_owners_are_in_flight(project, monkeypatch):
    # moat-finding: fb-0fbea010311c
    rc = _both_reviewers_missing(project, monkeypatch, [
        _spawn("regression-sentinel", 3), _spawn("conventions-enforcer", 4)])
    assert rc == 0


def test_reviewing_blocks_when_only_one_owner_is_in_flight(project, monkeypatch):
    # moat-finding: fb-0fbea010311c
    rc = _both_reviewers_missing(project, monkeypatch, [_spawn("regression-sentinel", 3)])
    assert rc == 2


def test_a_stale_evidence_file_with_the_verdict_missing_is_in_flight(project, monkeypatch):
    # moat-finding: fb-0fbea010311c
    _report(project, "regression.evidence.json", "{}", 1)       # before the spawn
    rc = _both_reviewers_missing(project, monkeypatch, [
        _spawn("regression-sentinel", 3), _spawn("conventions-enforcer", 4)])
    assert rc == 0


def test_artifact_owner_pairs_evidence_with_its_verdict():
    # moat-finding: fb-0fbea010311c
    assert gate._artifact_owner("reports/f/regression.evidence.json") == "regression-sentinel"
    assert gate._artifact_owner("reports/f/conventions.txt") == "conventions-enforcer"
    assert gate._artifact_owner("design/f/scope.md") is None


def test_in_flight_is_false_for_no_feature_or_no_items():
    # moat-finding: fb-0fbea010311c
    assert gate._in_flight(None, [("reports/f/scope-approval.txt", "x")]) is False
    assert gate._in_flight("f", []) is False


# ---------- fb-5861062129e7: a live owner holds the phase ----------

def _lease(root: Path, *, driver=None, delegates=(), age_min=0) -> None:
    from datetime import datetime, timedelta, timezone
    from prusik import main_writer
    at = (datetime.now(timezone.utc) - timedelta(minutes=age_min)).isoformat()
    lease = {"session": "main-1", "feature": _F, "phase": "scoping", "acquired_at": at,
             "refreshed_at": at, "delegates": list(delegates)}
    if driver is not None:
        lease["driver"] = driver
    main_writer._write(root, lease)


def _stop_as(session, phase="scoping") -> int:
    phases.set_sprint_state({"phase": phase, "feature": _F})
    saved = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"stop_hook_active": False, "session_id": session,
                                        "transcript_path": "/nonexistent/t.jsonl"}))
    try:
        with redirect_stdout(io.StringIO()):
            return gate.stop()
    finally:
        sys.stdin = saved


def _dead_pid() -> int:
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


@pytest.mark.parametrize("phase", ["scoping", "planning", "reviewing", "integrating"])
def test_a_running_drive_owns_every_exit_artifact(project, phase):
    # moat-finding: fb-5861062129e7
    # Field shape 2: drive owns the phase in the background; nothing the hook demands exists,
    # scope.md included, and the only tier resolved predates the sprint (field shape 1).
    _ledger(project, [_spawn("scoping", 0), _entered(1)])
    _lease(project, driver={"pid": os.getpid(), "feature": _F})
    assert _stop_as("main-1", phase) == 0
    assert not _reported(project)


def test_a_role_drive_delegated_to_stops_freely(project):
    # moat-finding: fb-5861062129e7
    _ledger(project, [_entered(0)])
    _lease(project, delegates=["role-7"])
    assert _stop_as("role-7") == 0
    assert _stop_as("someone-else") == 2


@pytest.mark.parametrize("lease", [
    {"driver": "dead"},                                            # drive exited or crashed
    {"driver": {"feature": "other"}},                              # drives another feature
    {"driver": {}, "age_min": 31},                                 # the lease went stale
    {"delegates": ["role-7"], "age_min": 31},                      # a stale delegation
    {},                                                            # nobody owns it
])
def test_without_a_live_owner_the_hook_still_blocks(project, lease):
    # moat-finding: fb-5861062129e7
    _ledger(project, [_entered(0)])
    kw = dict(lease)
    if kw.get("driver") == "dead":
        kw["driver"] = {"pid": _dead_pid(), "feature": _F}
    elif "driver" in kw:
        kw["driver"] = {"pid": os.getpid(), "feature": _F, **kw["driver"]}
    _lease(project, **kw)
    assert _stop_as("role-7" if kw.get("delegates") else "main-1") == 2


# ---------- fb-9aaa814f7b3b: scope-critic's tier from scope.md ----------

@pytest.fixture
def tiered(project):
    import yaml
    cfg_path = project / ".claude" / "sprint-config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["model_tiering"] = {"enabled": True}
    cfg_path.write_text(yaml.safe_dump(cfg))
    shutil.copy(_REFERENCE, project / ".claude" / "model-profile.yaml")
    return project


def _scope_inputs(root: Path, *, size="S", priority="P1", risks=()):
    (root / "design" / _F).mkdir(parents=True, exist_ok=True)
    (root / "briefs").mkdir(exist_ok=True)
    (root / "design" / _F / "scope.md").write_text(
        "## Domains\n- backend\n\n## Size\n" + size + "\n\n## Risks\n"
        + "".join(f"- {r}\n" for r in risks))
    (root / "briefs" / f"{_F}.md").write_text(f"## Type\nnew_feature\n\n## Priority\n{priority}\n")


def _scope_critic():
    return model_tier.resolve(_F, "scope-critic").roles["scope-critic"]


def test_small_low_stakes_scope_is_bounded_from_scope_md(tiered):
    # moat-finding: fb-9aaa814f7b3b
    _scope_inputs(tiered)
    t = _scope_critic()
    assert (t.tier_class, t.source, t.model) == ("bounded", "scope", "sonnet")
    assert "size S" in t.reason


def test_a_large_scope_is_high_stakes(tiered):
    # moat-finding: fb-9aaa814f7b3b
    _scope_inputs(tiered, size="L")
    t = _scope_critic()
    assert (t.tier_class, t.source, t.model) == ("high_stakes", "scope", "opus")


def test_a_small_scope_with_high_stakes_is_high_stakes(tiered):
    # moat-finding: fb-9aaa814f7b3b
    _scope_inputs(tiered, size="S", priority="P0", risks=["migration", "security"])
    t = _scope_critic()
    assert t.tier_class == "high_stakes"
    assert "stakes high" in t.reason and "team mode unknown before triage: +0" in t.reason


def test_missing_scope_md_fails_closed_naming_it(tiered, capsys):
    # moat-finding: fb-9aaa814f7b3b
    assert model_tier.run_resolve(_F, "scope-critic", False) == 2
    assert f"design/{_F}/scope.md" in capsys.readouterr().err


def test_trivial_lane_needs_no_scope_md(tiered):
    # moat-finding: fb-9aaa814f7b3b
    (tiered / "briefs").mkdir()
    (tiered / "briefs" / f"{_F}.md").write_text("## Type\nbug_fix\n\n## Priority\nP1\n")
    _ledger(tiered, [{"ts": _ts(0), "event": "sprint_started", "feature": _F,
                      "lane": "trivial"}])
    t = model_tier.resolve(_F).roles["scope-critic"]
    assert (t.tier_class, t.source) == ("high_stakes", "fixed")


def test_a_plan_override_wins_over_the_scope_rule(tiered):
    # moat-finding: fb-9aaa814f7b3b
    _scope_inputs(tiered)
    (tiered / "design" / _F / "plan.md").write_text(
        "## Model tiers\n- scope-critic: mechanical — pinned by the plan\n")
    t = _scope_critic()
    assert (t.tier_class, t.source) == ("mechanical", "override")


def test_a_scope_critic_resolve_never_loads_decision_stakes(tiered, capsys):
    # moat-finding: fb-9aaa814f7b3b
    _scope_inputs(tiered)
    (tiered / "decisions").mkdir()
    (tiered / "decisions" / f"{_F}.json").write_text("{not json")
    assert model_tier.run_resolve(_F, "scope-critic", False) == 0
    assert capsys.readouterr().out.strip() == "model=sonnet"
    (ev,) = [e for e in ledger.read_all() if e["event"] == "tier_resolved"]
    assert ev["stakes"] is None
    assert ev["roles"]["scope-critic"]["source"] == "scope"
    assert "size S" in ev["roles"]["scope-critic"]["reason"]


def test_tier_class_refuses_scope_critic():
    # moat-finding: fb-9aaa814f7b3b
    profile = model_tier.Profile(tiers={}, stakes_low=1, stakes_high=4, risk_weights={},
                                 roles={})
    with pytest.raises(model_tier.TierError, match="resolved from scope.md"):
        model_tier.tier_class("scope-critic", "normal", profile)


# ---------- fb-9dac611d867c: the plan boundary ----------

def _stub_context(monkeypatch, growth, *, measured=True):
    ctx = (usage.SessionContext(applies=True, measured=True, first=50_000,
                                current=50_000 + growth)
           if measured else usage.SessionContext(applies=True, measured=False,
                                                 reason="no transcript"))
    monkeypatch.setattr(usage, "session_context", lambda: ctx)


def _advance(project, current, target, *, rewind=False):
    phases.set_sprint_state({"phase": current, "feature": _F})
    saved = gate._unsatisfied_exit_artifacts
    gate._unsatisfied_exit_artifacts = lambda *a, **k: []
    try:
        out = io.StringIO()
        with redirect_stdout(out):
            rc = gate.advance(argparse.Namespace(phase=target, feature=_F,
                                                 allow_rewind=rewind))
    finally:
        gate._unsatisfied_exit_artifacts = saved
    return rc, out.getvalue()


def _events(name):
    return [e for e in ledger.read_all() if e["event"] == name]


def test_leaving_planning_past_40k_prints_the_boundary_line(project, monkeypatch):
    # moat-finding: fb-9dac611d867c
    _stub_context(monkeypatch, 50_000)
    rc, out = _advance(project, "planning", "solo_execute")
    assert rc == 0
    assert f"design/{_F}/plan.md" in out and "/compact" in out
    (ev,) = _events("plan_boundary_advised")
    assert (ev["feature"], ev["growth"], ev["context"]) == (_F, 50_000, 100_000)
    assert _events("session_size_warned") == []


def test_the_boundary_line_replaces_the_generic_one_even_past_120k(project, monkeypatch):
    # moat-finding: fb-9dac611d867c
    _stub_context(monkeypatch, 150_000)
    rc, out = _advance(project, "planning", "solo_execute")
    assert rc == 0
    assert "plan.md is approved" in out and "above its first turn, and every turn" not in out
    assert _events("session_size_warned") == [] and len(_events("plan_boundary_advised")) == 1


def test_growth_under_40k_prints_no_boundary_line(project, monkeypatch):
    # moat-finding: fb-9dac611d867c
    _stub_context(monkeypatch, 30_000)
    rc, out = _advance(project, "planning", "solo_execute")
    assert rc == 0 and "/compact" not in out
    assert _events("plan_boundary_advised") == []


def test_an_unmeasured_session_falls_back_to_the_generic_line(project, monkeypatch):
    # moat-finding: fb-9dac611d867c
    _stub_context(monkeypatch, 0, measured=False)
    rc, out = _advance(project, "planning", "solo_execute")
    assert rc == 0 and "/compact" not in out
    assert "session context UNMEASURED: no transcript" in out
    assert _events("plan_boundary_advised") == []


def test_other_transitions_keep_the_generic_line(project, monkeypatch):
    # moat-finding: fb-9dac611d867c
    _stub_context(monkeypatch, 150_000)
    rc, out = _advance(project, "solo_execute", "reviewing")
    assert rc == 0 and "above its first turn, and every turn" in out and "/compact" not in out
    rc, out = _advance(project, "solo_execute", "planning", rewind=True)
    assert rc == 0 and "/compact" not in out
    assert _events("plan_boundary_advised") == []
