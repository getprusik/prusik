"""Phase-gate outcome-capture (roadmap Horizon-2 C): a phase_gate block whose blocked
transition LATER SUCCEEDED = the gate enforced a missing exit-artifact / pre-sprint
requirement that was then produced (a true catch) — the direct analog of the writable
gate's advanced-past-build signal. Closes the unmeasured-phase_gate gap from v0.46.0. A
block with no later matching success stays UNRESOLVED — never a claimed catch the ledger
doesn't bear out.

moat-finding: roadmap-horizon-2c-phase-gate-outcome
"""

from __future__ import annotations

from prusik import catch_quality as cq


def _resolve(records):
    return cq.resolve_catches(cq.extract_catches(records), records)


def _verdict(records, event, **match):
    for c in _resolve(records):
        if c["event"] == event and all(c.get(k) == v for k, v in match.items()):
            return c["verdict"]
    raise AssertionError(f"no catch for {event} {match}")


def test_advance_block_enforced_when_transition_later_succeeds():
    records = [
        {"event": "advance_blocked", "feature": "f1", "from_phase": "reviewing",
         "to_phase": "integrating", "ts": "2026-06-08T01:00"},
        {"event": "phase_advance", "feature": "f1", "from_phase": "reviewing",
         "to_phase": "integrating", "ts": "2026-06-08T02:00"},        # the requirement appeared
    ]
    assert _verdict(records, "advance_blocked", feature="f1") == cq.TRUE_CATCH


def test_advance_block_unresolved_when_transition_never_succeeds():
    # blocked, but the feature never advanced to that phase → we cannot claim a catch
    records = [
        {"event": "advance_blocked", "feature": "f1", "from_phase": "reviewing",
         "to_phase": "integrating", "ts": "2026-06-08T01:00"},
    ]
    assert _verdict(records, "advance_blocked", feature="f1") == cq.UNRESOLVED


def test_advance_to_different_phase_does_not_enforce():
    # ADVERSARIAL: a later advance to a DIFFERENT phase must not credit the block
    records = [
        {"event": "advance_blocked", "feature": "f1", "from_phase": "planning",
         "to_phase": "building", "ts": "2026-06-08T01:00"},
        {"event": "phase_advance", "feature": "f1", "from_phase": "scoping",
         "to_phase": "planning", "ts": "2026-06-08T02:00"},
    ]
    assert _verdict(records, "advance_blocked", feature="f1") == cq.UNRESOLVED


def test_sprint_start_block_enforced_by_later_start():
    records = [
        {"event": "sprint_start_blocked", "feature": "f1", "unmet": ["map_freshness"],
         "ts": "2026-06-08T01:00"},
        {"event": "sprint_started", "feature": "f1", "ts": "2026-06-08T02:00"},
    ]
    assert _verdict(records, "sprint_start_blocked", feature="f1") == cq.TRUE_CATCH


def test_multiple_retries_before_success_all_count():
    records = [
        {"event": "advance_blocked", "feature": "f1", "from_phase": "reviewing",
         "to_phase": "integrating", "ts": "2026-06-08T01:00"},
        {"event": "advance_blocked", "feature": "f1", "from_phase": "reviewing",
         "to_phase": "integrating", "ts": "2026-06-08T01:30"},
        {"event": "phase_advance", "feature": "f1", "from_phase": "reviewing",
         "to_phase": "integrating", "ts": "2026-06-08T02:00"},
    ]
    enforced = cq._phase_gate_enforced(records)
    blocks = [c for c in cq.extract_catches(records) if c["event"] == "advance_blocked"]
    assert len(blocks) == 2 and all(b["id"] in enforced for b in blocks)


def test_phase_gate_now_has_precision_in_summary():
    records = [
        {"event": "advance_blocked", "feature": "f1", "from_phase": "reviewing",
         "to_phase": "integrating", "ts": "2026-06-08T01:00"},
        {"event": "phase_advance", "feature": "f1", "from_phase": "reviewing",
         "to_phase": "integrating", "ts": "2026-06-08T02:00"},
    ]
    summary = cq.summarize(_resolve(records))
    assert summary["phase_gate"]["precision"] == 1.0     # was None (unmeasured) before
    assert summary["phase_gate"][cq.TRUE_CATCH] == 1
