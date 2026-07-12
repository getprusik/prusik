"""Convergence-stall control — counting, reset-on-resume, config, and the
fail-closed gate escalation (v0.49.0)."""

from __future__ import annotations

import argparse

from tests._common import (  # noqa: F401,E402
    _capture_stderr,
    _copy_sprint_config,
    _mktmp_project,
    _write_ledger,
    gate,
    phases,
)
from prusik import convergence, ledger
from prusik import pause as _pause


def _rw(feature, ts):
    return {"ts": ts, "event": "phase_rewind", "from_phase": "reviewing",
            "to_phase": "building", "feature": feature}


# ---------- counting + reset ----------

def test_rewind_count_since_sprint_started():
    recs = [
        {"ts": "2026-06-01T00:00:00+00:00", "event": "sprint_started",
         "feature": "f"},
        _rw("f", "2026-06-01T00:01:00+00:00"),
        _rw("f", "2026-06-01T00:02:00+00:00"),
        _rw("other", "2026-06-01T00:03:00+00:00"),   # different feature
    ]
    assert convergence.rewind_count(recs, "f") == 2


def test_resume_resets_the_budget():
    recs = [
        {"ts": "2026-06-01T00:00:00+00:00", "event": "sprint_started",
         "feature": "f"},
        _rw("f", "2026-06-01T00:01:00+00:00"),
        _rw("f", "2026-06-01T00:02:00+00:00"),
        {"ts": "2026-06-01T00:03:00+00:00", "event": "pause_ended",
         "reason": "human reviewed"},
        _rw("f", "2026-06-01T00:04:00+00:00"),       # only this one counts
    ]
    assert convergence.rewind_count(recs, "f") == 1


def test_is_stall_threshold_grace_then_escalate():
    base = [{"ts": "2026-06-01T00:00:00+00:00", "event": "sprint_started",
             "feature": "f"}]
    three = base + [_rw("f", f"2026-06-01T00:0{i}:00+00:00") for i in range(1, 4)]
    assert convergence.is_stall(three, "f", 4) is False     # 3 rewinds, budget 4
    four = three + [_rw("f", "2026-06-01T00:05:00+00:00")]
    assert convergence.is_stall(four, "f", 4) is True       # 4 → next escalates


def test_limit_zero_disables():
    recs = [_rw("f", "2026-06-01T00:01:00+00:00") for _ in range(9)]
    assert convergence.is_stall(recs, "f", 0) is False


# ---------- config ----------

def test_max_rewinds_default_and_override():
    assert convergence.max_rewinds(None) == 4
    assert convergence.max_rewinds({}) == 4
    assert convergence.max_rewinds(
        {"watchdog": {"max_rewinds_before_escalation": 2}}) == 2
    assert convergence.max_rewinds(
        {"watchdog": {"max_rewinds_before_escalation": "oops"}}) == 4  # bad → default


# ---------- the fail-closed gate escalation ----------

def _seed(tmp, n_rewinds):
    _copy_sprint_config(tmp)
    phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
    events = [{"ts": "2026-06-01T00:00:00+00:00", "event": "sprint_started",
               "feature": "feat"}]
    events += [_rw("feat", f"2026-06-01T00:1{i}:00+00:00") for i in range(n_rewinds)]
    _write_ledger(tmp, events)


def test_gate_stall_pauses_and_escalates_fail_closed():
    tmp = _mktmp_project()
    _seed(tmp, n_rewinds=4)        # budget is 4 → the 5th (this) escalates
    err = _capture_stderr(lambda: _run_rewind())
    assert "CONVERGENCE STALL" in err
    # paused (run halts), event recorded, state NOT transitioned, no new rewind
    assert _pause.is_paused() is True
    events = ledger.read_all()
    stalls = [e for e in events if e["event"] == "convergence_stall"]
    assert stalls
    # discriminated from the v0.8.11 tool-output stall that shares the event
    assert stalls[-1]["kind"] == "phase_rewind"
    assert phases.current_sprint_state()["phase"] == "reviewing"   # unchanged
    assert sum(1 for e in events if e["event"] == "phase_rewind") == 4  # no +1


def test_gate_under_budget_rewinds_normally():
    tmp = _mktmp_project()
    _seed(tmp, n_rewinds=1)        # 1 rewind, budget 4 → proceeds
    rc = _run_rewind()
    assert rc == 0
    assert _pause.is_paused() is False
    assert phases.current_sprint_state()["phase"] == "building"    # transitioned
    events = ledger.read_all()
    assert sum(1 for e in events if e["event"] == "phase_rewind") == 2  # +1 recorded
    assert not any(e["event"] == "convergence_stall" for e in events)


def _run_rewind():
    return gate.advance(argparse.Namespace(
        phase="building", feature="feat", allow_rewind=True))
