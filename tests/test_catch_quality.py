"""Catch-quality ledger — derivation, resolution, auto-rules, CLI (v0.45.0)."""

from __future__ import annotations

import json

from tests._common import (  # noqa: F401,E402
    _capture_stdout,
    _mktmp_project,
    _write_ledger,
)
from prusik import catch_quality as cq


def _setup(events):
    tmp = _mktmp_project()
    _write_ledger(tmp, events)
    return tmp


# ---------- extraction ----------

def test_extract_only_qualifying_fires():
    events = [
        {"ts": "2026-06-01T00:00:01+00:00", "event": "gate_blocked",
         "phase": "building", "reason": "out of scope"},
        {"ts": "2026-06-01T00:00:02+00:00", "event": "phase_advance"},   # not a catch
        {"ts": "2026-06-01T00:00:03+00:00", "event": "critic_verdict",
         "role": "scope-critic", "verdict": "APPROVED"},                 # approved → not a catch
        {"ts": "2026-06-01T00:00:04+00:00", "event": "critic_verdict",
         "role": "plan-critic", "verdict": "REVISE", "artifact": "plan"},  # catch
        {"ts": "2026-06-01T00:00:05+00:00", "event": "reviewer_execution_verified",
         "ok": True},                                                    # passed → not a catch
        {"ts": "2026-06-01T00:00:06+00:00", "event": "reviewer_execution_verified",
         "ok": False, "command": "pytest -q"},                          # catch
    ]
    catches = cq.extract_catches(events)
    gates = sorted(c["gate"] for c in catches)
    assert gates == ["critic", "evidence_gate", "writable_gate"]


def test_catch_id_is_stable_and_unique_per_fire():
    r1 = {"ts": "2026-06-01T00:00:01+00:00", "event": "gate_blocked"}
    r2 = {"ts": "2026-06-01T00:00:02+00:00", "event": "gate_blocked"}
    assert cq.catch_id(r1) == cq.catch_id(dict(r1))   # deterministic
    assert cq.catch_id(r1) != cq.catch_id(r2)         # distinct ts → distinct id


# ---------- auto-resolution ----------

def test_evidence_gate_fire_auto_true_catch():
    events = [{"ts": "2026-06-01T00:00:06+00:00",
               "event": "reviewer_execution_verified", "ok": False}]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    assert catches[0]["verdict"] == cq.TRUE_CATCH
    assert catches[0]["source"] == "auto"


def test_block_then_disable_auto_false_block():
    events = [
        {"ts": "2026-06-01T00:00:01+00:00", "event": "gate_blocked",
         "phase": "building", "reason": "x"},
        {"ts": "2026-06-01T00:00:02+00:00", "event": "kit_disabled"},
    ]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    assert catches[0]["verdict"] == cq.FALSE_BLOCK
    assert catches[0]["source"] == "auto"


def test_enable_between_block_and_disable_breaks_inference():
    events = [
        {"ts": "2026-06-01T00:00:01+00:00", "event": "gate_blocked", "reason": "x"},
        {"ts": "2026-06-01T00:00:02+00:00", "event": "kit_enabled"},
        {"ts": "2026-06-01T00:00:03+00:00", "event": "kit_disabled"},
    ]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    assert catches[0]["verdict"] == cq.UNRESOLVED


def test_unresolved_by_default():
    events = [{"ts": "2026-06-01T00:00:01+00:00", "event": "reviewer_binding_flagged",
               "feature": "x", "url": "/y"}]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    assert catches[0]["verdict"] == cq.UNRESOLVED
    assert catches[0]["source"] == ""


# ---------- operator labels win ----------

def test_operator_label_overrides_auto():
    fire = {"ts": "2026-06-01T00:00:06+00:00",
            "event": "reviewer_execution_verified", "ok": False}
    cid = cq.catch_id(fire)
    events = [fire, {"ts": "2026-06-01T00:00:07+00:00", "event": "catch_resolved",
                     "catch_id": cid, "verdict": cq.FALSE_BLOCK,
                     "reason": "flaky env", "source": "operator"}]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    assert catches[0]["verdict"] == cq.FALSE_BLOCK
    assert catches[0]["source"] == "operator"


def test_last_operator_label_wins():
    fire = {"ts": "2026-06-01T00:00:01+00:00", "event": "phase_rewind",
            "from_phase": "reviewing"}
    cid = cq.catch_id(fire)
    events = [
        fire,
        {"ts": "2026-06-01T00:00:02+00:00", "event": "catch_resolved",
         "catch_id": cid, "verdict": cq.FALSE_BLOCK},
        {"ts": "2026-06-01T00:00:03+00:00", "event": "catch_resolved",
         "catch_id": cid, "verdict": cq.TRUE_CATCH},
    ]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    assert catches[0]["verdict"] == cq.TRUE_CATCH


# ---------- summary / precision ----------

def test_summary_precision_excludes_unresolved():
    events = [
        {"ts": "2026-06-01T00:00:01+00:00", "event": "gate_blocked", "reason": "a"},
        {"ts": "2026-06-01T00:00:02+00:00", "event": "gate_blocked", "reason": "b"},
        {"ts": "2026-06-01T00:00:03+00:00", "event": "gate_blocked", "reason": "c"},
    ]
    cid_a = cq.catch_id(events[0])
    cid_b = cq.catch_id(events[1])
    events += [
        {"ts": "2026-06-01T00:00:04+00:00", "event": "catch_resolved",
         "catch_id": cid_a, "verdict": cq.TRUE_CATCH},
        {"ts": "2026-06-01T00:00:05+00:00", "event": "catch_resolved",
         "catch_id": cid_b, "verdict": cq.FALSE_BLOCK},
    ]
    summary = cq.summarize(cq.resolve_catches(cq.extract_catches(events), events))
    g = summary["writable_gate"]
    assert g["fired"] == 3 and g["true_catch"] == 1 and g["false_block"] == 1
    assert g["unresolved"] == 1
    assert g["precision"] == 0.5   # 1 true / (1 true + 1 false); unresolved excluded


def test_precision_none_when_nothing_resolved():
    events = [{"ts": "2026-06-01T00:00:01+00:00", "event": "phase_rewind"}]
    summary = cq.summarize(cq.resolve_catches(cq.extract_catches(events), events))
    assert summary["rewind"]["precision"] is None


# ---------- CLI / resolve ----------

def test_resolve_writes_event_and_round_trips():
    fire = {"ts": "2026-06-01T00:00:01+00:00", "event": "gate_blocked", "reason": "x"}
    _setup([fire])
    cid = cq.catch_id(fire)
    rc = cq.resolve(cid, cq.TRUE_CATCH, reason="caught a real out-of-scope edit")
    assert rc == 0
    from prusik import ledger
    catches = cq.resolve_catches(cq.extract_catches(ledger.read_all()),
                                 ledger.read_all())
    target = next(c for c in catches if c["id"] == cid)
    assert target["verdict"] == cq.TRUE_CATCH and target["source"] == "operator"


def test_resolve_rejects_unknown_id():
    _setup([{"ts": "2026-06-01T00:00:01+00:00", "event": "gate_blocked"}])
    assert cq.resolve("deadbeef0000", cq.TRUE_CATCH) == 1


def test_resolve_rejects_bad_verdict():
    fire = {"ts": "2026-06-01T00:00:01+00:00", "event": "gate_blocked"}
    _setup([fire])
    assert cq.resolve(cq.catch_id(fire), "maybe") == 2


def test_run_json_shape():
    _setup([
        {"ts": "2026-06-01T00:00:01+00:00", "event": "reviewer_execution_verified",
         "ok": False, "command": "pytest"},
        {"ts": "2026-06-01T00:00:02+00:00", "event": "gate_blocked", "reason": "x"},
    ])
    out = _capture_stdout(lambda: cq.run(json_output=True))
    data = json.loads(out)
    assert data["total"] == 2
    assert "evidence_gate" in data["by_gate"]
    assert data["by_gate"]["evidence_gate"]["true_catch"] == 1


def test_run_empty_ledger():
    _setup([])
    out = _capture_stdout(lambda: cq.run(json_output=False))
    assert "no gate/critic fires" in out


def test_disable_emits_kit_disabled_event():
    """The routed-around signal depends on `prusik disable` emitting it."""
    tmp = _mktmp_project()
    (tmp / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp / ".claude" / "settings.json").write_text("{}\n")
    from prusik import toggle, ledger
    toggle.disable()
    assert any(r.get("event") == "kit_disabled" for r in ledger.read_all())


# ---------- writable-gate outcome capture (v0.46.0) ----------
# Derived from the append-only ledger alone — no sidecar, no hot-path I/O.

def _build_block(ts, feature, phase="building"):
    return {"ts": ts, "event": "gate_blocked", "feature": feature,
            "phase": phase, "target": "src/foo.py", "reason": "outside worktree"}


def test_build_block_true_catch_when_feature_advances_past_build():
    events = [
        _build_block("2026-06-01T00:00:01+00:00", "f"),
        {"ts": "2026-06-01T00:01:00+00:00", "event": "phase_advance",
         "feature": "f", "to_phase": "reviewing"},
    ]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    c = next(x for x in catches if x["gate"] == "writable_gate")
    assert c["verdict"] == cq.TRUE_CATCH and c["source"] == "auto"


def test_solo_execute_block_resolved_by_sprint_complete():
    events = [
        _build_block("2026-06-01T00:00:01+00:00", "f", phase="solo_execute"),
        {"ts": "2026-06-01T00:05:00+00:00", "event": "sprint_complete",
         "feature": "f"},
    ]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    assert catches[0]["verdict"] == cq.TRUE_CATCH


def test_build_block_unresolved_when_feature_never_advances():
    events = [_build_block("2026-06-01T00:00:01+00:00", "f")]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    assert catches[0]["verdict"] == cq.UNRESOLVED


def test_reviewing_phase_block_not_auto_resolved():
    """The build-phase rule is scoped: a reviewing-phase block (where non-
    worktree writes can be legitimate) is NOT auto-credited."""
    events = [
        _build_block("2026-06-01T00:00:01+00:00", "f", phase="reviewing"),
        {"ts": "2026-06-01T00:01:00+00:00", "event": "phase_advance",
         "feature": "f", "to_phase": "integrating"},
    ]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    assert catches[0]["verdict"] == cq.UNRESOLVED


def test_operator_label_overrides_build_phase_auto():
    blk = _build_block("2026-06-01T00:00:01+00:00", "f")
    cid = cq.catch_id(blk)
    events = [
        blk,
        {"ts": "2026-06-01T00:01:00+00:00", "event": "phase_advance",
         "feature": "f", "to_phase": "reviewing"},
        {"ts": "2026-06-01T00:02:00+00:00", "event": "catch_resolved",
         "catch_id": cid, "verdict": cq.FALSE_BLOCK, "source": "operator"},
    ]
    catches = cq.resolve_catches(cq.extract_catches(events), events)
    c = next(x for x in catches if x["id"] == cid)
    assert c["verdict"] == cq.FALSE_BLOCK and c["source"] == "operator"
