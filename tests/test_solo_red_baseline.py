"""The solo lanes capture prove_red baselines before the build (fb-221b8b5df7e6).

Drive's triage→solo hand-off runs `prove-red` while the shared tree is still pre-change,
and leaving any build phase (building, solo_execute) without a baseline for every local
prove_red criterion is refused — so the gap can no longer first surface at
sprint-complete, after integration, when no honest RED capture is possible.
moat-finding: fb-221b8b5df7e6
"""

from __future__ import annotations

import argparse

import pytest
import yaml

from prusik import consistency, gate, ledger, phases
from tests.test_drive_design_phases import _fake_triage
from tests.test_sprint_drive import F, escalated_events, proj, rig, set_phase  # noqa: F401


# ── drive: the solo_execute step captures RED before the build ─────────────────────────────

def test_triage_solo_captures_red_before_the_solo_build(rig, proj, monkeypatch):
    set_phase(proj, "triage")
    _fake_triage(monkeypatch, proj, {"mode": "solo", "reason": "r"})
    rig.run()
    kinds = [e[1] if e[0] == "gate" else e[0] for e in rig.events]
    assert kinds.index("prove-red") < kinds.index("spawn"), "RED is captured before the spawn"
    assert ("spawn", "solo-builder") in rig.events
    assert [e[1] for e in rig.events if e[0] == "gate"][:2] == ["advance", "prove-red"]
    assert gate.missing_red_baselines(proj, F) == []
    assert escalated_events() == [], "the build is not handed back"


def test_triage_solo_skips_prove_red_when_baselines_exist(rig, proj, monkeypatch):
    ledger.append("criterion_red_baseline", feature=F, id="c1", verify_command="false",
                  exit_code=1)
    set_phase(proj, "triage")
    _fake_triage(monkeypatch, proj, {"mode": "solo", "reason": "r"})
    rig.run()
    assert ("spawn", "solo-builder") in rig.events
    assert ("gate", "prove-red", "--feature", F) not in rig.events
    assert escalated_events() == [], "the build is not handed back"


def test_triage_solo_prove_red_that_captures_nothing_escalates(rig, proj, monkeypatch):
    rig.effect = lambda a: set_phase(proj, a[1]) if a[0] == "advance" else None
    set_phase(proj, "triage")
    _fake_triage(monkeypatch, proj, {"mode": "solo", "reason": "r"})
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "prove_red_failed" and esc["phase"] == "solo_execute"
    assert "c1" in esc["evidence"]


# ── gate: leaving a build phase requires every baseline ───────────────────────────────────

@pytest.mark.parametrize("phase", ["building", "solo_execute"])
def test_exit_check_registered_and_names_the_fix(proj, phase):
    assert consistency.red_baselines_captured in consistency.PHASE_CHECKS[phase]
    errs = consistency.run_for_phase(phase, proj, F)
    assert any("No captured RED baseline for prove_red criteria: c1" in e for e in errs)
    assert any(f"prusik gate prove-red --feature {F}" in e for e in errs)


def test_baseline_for_a_different_verify_does_not_credit(proj):
    ledger.append("criterion_red_baseline", feature=F, id="c1",
                  verify_command="exit 1", exit_code=1)
    assert gate.missing_red_baselines(proj, F) == ["c1"]
    assert consistency.red_baselines_captured(proj, F)


def test_ci_and_non_prove_red_criteria_need_no_baseline(proj):
    (proj / "briefs/feat.criteria.yaml").write_text(yaml.safe_dump({
        "schema_version": "1.0", "criteria": [
            {"id": "c1", "description": "d", "verify_command": "false", "kind": "tests"},
            {"id": "c2", "description": "d", "verify_command": "false", "kind": "tests",
             "prove_red": True, "verify_in": "ci"}]}))
    assert consistency.red_baselines_captured(proj, F) == []


def test_solo_execute_advance_blocked_until_baseline_captured(proj, capsys):
    set_phase(proj, "solo_execute")
    (proj / "design/feat/scope.md").write_text("# Scope\n\n## Modules touched\n- `x.py`\n")
    args = argparse.Namespace(phase="reviewing", feature=F, allow_rewind=False)
    assert gate.advance(args) != 0
    assert phases.current_sprint_state(proj)["phase"] == "solo_execute"
    cap = capsys.readouterr()
    assert "No captured RED baseline" in cap.out + cap.err
    ledger.append("criterion_red_baseline", feature=F, id="c1", verify_command="false",
                  exit_code=1)
    gate.advance(args)
    assert "No captured RED baseline" not in "".join(capsys.readouterr())
