"""Self-learning loop scaffolding — classification, the preventive caveat, the
fuel guard (loop stays open on N=1), CLI (v0.51.0)."""

from __future__ import annotations

import json

from tests._common import (  # noqa: F401,E402
    _capture_stdout,
    _mktmp_project,
    _write_ledger,
)
from prusik import calibration as cal
from prusik import catch_quality as cq


def _fire(ts, event, **kw):
    return {"ts": ts, "event": event, **kw}


def _label(fire, verdict):
    return {"event": "catch_resolved", "catch_id": cq.catch_id(fire),
            "verdict": verdict}


# ---------- classification ----------

def test_classify_preventive_detector_unknown():
    assert cal.classify("writable_gate") == "preventive"
    assert cal.classify("rewind") == "preventive"
    assert cal.classify("binding_detector") == "detector"
    assert cal.classify("evidence_gate") == "detector"
    assert cal.classify("something_new") == "unknown"


# ---------- the preventive caveat (the key honesty property) ----------

def test_preventive_control_never_recommended_to_loosen():
    # writable_gate blocks in REVIEWING phase (no auto-true rule), labeled mostly
    # false → low precision. A DETECTOR would be flagged "review"; a PREVENTIVE
    # control must NOT be — its value is invisible prevention.
    fires = [_fire(f"2026-06-01T00:0{i}:00+00:00", "gate_blocked",
                   phase="reviewing", feature="f") for i in range(4)]
    recs = list(fires)
    recs.append(_label(fires[0], cq.TRUE_CATCH))
    for fr in fires[1:]:
        recs.append(_label(fr, cq.FALSE_BLOCK))     # 1 true / 3 false = 25%
    sig = cal.calibration_signals(recs)["writable_gate"]
    assert sig["kind"] == "preventive"
    assert sig["recommendation"] == "keep"          # NOT "review", despite 25%
    assert "Never loosen" in sig["why"]


def test_noisy_detector_with_fuel_is_flagged_review():
    # binding_detector, ≥10 labeled, low precision → "review"
    fires = [_fire(f"2026-06-01T00:{i:02d}:00+00:00", "reviewer_binding_flagged",
                   feature="f") for i in range(12)]
    recs = list(fires)
    recs.append(_label(fires[0], cq.TRUE_CATCH))
    recs.append(_label(fires[1], cq.TRUE_CATCH))
    for fr in fires[2:]:
        recs.append(_label(fr, cq.FALSE_BLOCK))     # 2 true / 10 false ≈ 17%
    sig = cal.calibration_signals(recs)["binding_detector"]
    assert sig["kind"] == "detector"
    assert sig["recommendation"] == "review"


def test_thin_detector_is_insufficient_not_acted_on():
    fires = [_fire(f"2026-06-01T00:0{i}:00+00:00", "reviewer_binding_flagged",
                   feature="f") for i in range(3)]
    recs = list(fires) + [_label(fr, cq.FALSE_BLOCK) for fr in fires]
    sig = cal.calibration_signals(recs)["binding_detector"]
    assert sig["recommendation"] == "insufficient"


# ---------- the fuel guard: loop stays OPEN on N=1 ----------

def test_loop_open_on_single_codebase_by_design():
    fires = [_fire(f"2026-06-01T00:{i:02d}:00+00:00", "reviewer_binding_flagged",
                   feature="f") for i in range(12)]
    recs = list(fires) + [_label(fr, cq.FALSE_BLOCK) for fr in fires]
    signals = cal.calibration_signals(recs)
    assert cal.is_fueled(signals, codebases=1) is False     # N=1 → never fueled
    assert cal.is_fueled(signals, codebases=2) is True      # N≥2 + ≥10 labeled


def test_two_codebases_but_no_detector_data_still_unfueled():
    # writable_gate only (preventive, not a detector) → no detector fuel
    fires = [_fire(f"2026-06-01T00:0{i}:00+00:00", "gate_blocked",
                   phase="reviewing", feature="f") for i in range(4)]
    recs = list(fires) + [_label(fr, cq.FALSE_BLOCK) for fr in fires]
    signals = cal.calibration_signals(recs)
    assert cal.is_fueled(signals, codebases=2) is False


# ---------- CLI ----------

def test_cli_empty_ledger():
    _mktmp_project()
    out = _capture_stdout(lambda: cal.run())
    assert "nothing to calibrate" in out


def test_cli_advisory_banner_and_open_loop():
    tmp = _mktmp_project()
    fires = [_fire(f"2026-06-01T00:0{i}:00+00:00", "gate_blocked",
                   phase="reviewing", feature="f") for i in range(2)]
    _write_ledger(tmp, fires + [_label(fires[0], cq.FALSE_BLOCK)])
    out = _capture_stdout(lambda: cal.run())
    assert "loop is OPEN" in out
    assert "never auto-applied" in out


def test_cli_json_marks_loop_open_and_unfueled():
    tmp = _mktmp_project()
    fires = [_fire(f"2026-06-01T00:0{i}:00+00:00", "gate_blocked",
                   phase="reviewing", feature="f") for i in range(2)]
    _write_ledger(tmp, fires + [_label(fires[0], cq.FALSE_BLOCK)])
    out = _capture_stdout(lambda: cal.run(json_output=True))
    data = json.loads(out)
    assert data["loop"] == "open"
    assert data["fueled"] is False


# ---------- the actuator: cross-adopter suggestion (the brain) ----------

def _sig(precision, resolved):
    """A minimal per-gate signal for one detector (the fields fleet_suggestions
    reads)."""
    return {"precision": precision, "resolved": resolved}


def _adopter(detector, precision, resolved):
    return {detector: _sig(precision, resolved)}


# The loop's AUTO-promote set is EMPTY by design (recall detectors are human-gated,
# not auto-promoted — fb-f737a0b753bd). These logic tests register a SYNTHETIC
# high-volume detector to prove the cross-validation machinery is correct FOR WHEN
# one exists; a separate test pins that the real set is empty (the loop is dormant).
_SYN = "synthetic_high_volume_detector"


def test_auto_promote_set_is_empty_by_design():
    # even stellar cross-fleet precision on a real recall detector yields NO auto
    # promotion — it is GATEABLE (human decides), not AUTO_PROMOTABLE (loop decides).
    assert cal.AUTO_PROMOTABLE == {}
    strong = [_adopter("absence_detector", 1.0, 50),
              _adopter("absence_detector", 1.0, 50)]
    assert cal.fleet_suggestions(strong, codebases=2) == []


def test_promotion_needs_two_codebases_cross_validated(monkeypatch):
    monkeypatch.setitem(cal.AUTO_PROMOTABLE, _SYN, _SYN)
    # One codebase proving a detector is NOT enough — overfit, what N≥2 guards against.
    assert cal.fleet_suggestions([_adopter(_SYN, 1.0, 20)], codebases=1) == []
    # Two codebases each clearing the bar independently → promote.
    two = [_adopter(_SYN, 1.0, 20), _adopter(_SYN, 0.9, 15)]
    promote = [s for s in cal.fleet_suggestions(two, codebases=2)
               if s["action"] == "promote"]
    assert len(promote) == 1
    assert promote[0]["detector"] == _SYN and promote[0]["knob"] == "gate_on"


def test_one_proven_one_thin_is_not_cross_validated(monkeypatch):
    monkeypatch.setitem(cal.AUTO_PROMOTABLE, _SYN, _SYN)
    # codebase A proves it; codebase B has too few labeled fires → only ONE
    # independent proof → no promotion (don't let one repo carry the quorum).
    adopters = [_adopter(_SYN, 1.0, 20), _adopter(_SYN, 1.0, 3)]   # 3 < _MIN_RESOLVED
    sug = cal.fleet_suggestions(adopters, codebases=2)
    assert [s for s in sug if s["action"] == "promote"] == []


def test_noisy_across_fleet_is_review_never_auto_loosen(monkeypatch):
    monkeypatch.setitem(cal.AUTO_PROMOTABLE, _SYN, _SYN)
    adopters = [_adopter(_SYN, 0.4, 20), _adopter(_SYN, 0.5, 15)]
    review = [s for s in cal.fleet_suggestions(adopters, codebases=2)
              if s["action"] == "review"]
    assert len(review) == 1
    # a review suggestion carries NO knob/value — the loop proposes no auto-change
    assert review[0]["knob"] is None and review[0]["value"] is None


def test_reserved_detector_never_suggested_even_if_registered(monkeypatch):
    # belt-and-suspenders: delta is human-adjudicated; it is never in AUTO_PROMOTABLE,
    # so it is never iterated — strong precision can't make it a candidate.
    adopters = [_adopter("delta_detector", 1.0, 30),
                _adopter("delta_detector", 1.0, 30)]
    sug = cal.fleet_suggestions(adopters, codebases=2)
    assert all(s["detector"] != "delta_detector" for s in sug)


def test_preventive_and_already_gating_never_promoted():
    # writable_gate (preventive) + evidence_gate (already a hard gate) are not in
    # AUTO_PROMOTABLE → never suggested, however the precision reads.
    adopters = [{"writable_gate": _sig(1.0, 30), "evidence_gate": _sig(1.0, 30)},
                {"writable_gate": _sig(1.0, 30), "evidence_gate": _sig(1.0, 30)}]
    assert cal.fleet_suggestions(adopters, codebases=2) == []


# ---------- the actuator: guarded apply (the hand) ----------

def test_apply_promotes_and_is_honored_and_audited():
    tmp = _mktmp_project()
    rc = cal.apply("absence_detector", root=tmp,
                   evidence={"codebases": 2, "min_precision": 0.9})
    assert rc == 0
    # honored at runtime everywhere the detector runs
    assert cal.is_promoted("absence_detector", tmp) is True
    # overlay written (machine-owned, not the YAML)
    overlay = json.loads((tmp / cal.OVERLAY_PATH).read_text())
    assert "absence_detector" in overlay["gate_on"]
    # audited in the ledger
    from prusik import ledger
    events = [r["event"] for r in ledger.read_all()]
    assert "calibration_applied" in events


def test_apply_is_idempotent():
    tmp = _mktmp_project()
    assert cal.apply("absence_detector", root=tmp) == 0
    assert cal.apply("absence_detector", root=tmp) == 0   # already gating → no-op
    overlay = json.loads((tmp / cal.OVERLAY_PATH).read_text())
    assert overlay["gate_on"].count("absence_detector") == 1


def test_apply_refuses_reserved_detector():
    tmp = _mktmp_project()
    rc = cal.apply("delta_detector", root=tmp)
    assert rc == 2                                    # fail-closed
    assert not (tmp / cal.OVERLAY_PATH).exists()      # nothing written
    assert cal.is_promoted("delta_detector", tmp) is False


def test_apply_refuses_preventive_and_unknown():
    tmp = _mktmp_project()
    assert cal.apply("writable_gate", root=tmp) == 2   # preventive — never tunable
    assert cal.apply("evidence_gate", root=tmp) == 2   # already gating
    assert cal.apply("made_up_gate", root=tmp) == 2    # unknown
    assert not (tmp / cal.OVERLAY_PATH).exists()


def test_apply_only_tightens_no_disable_path():
    # The actuator has no API to turn a gate OFF — promotion is monotone. The
    # only knob it writes is the gate_on inclusion (tightening); loosening is a
    # deliberate hand-edit, never an apply path.
    assert not hasattr(cal, "unapply")
    assert not hasattr(cal, "disable")


def test_manual_config_gate_on_is_honored_too():
    # the operator can promote by hand in sprint-config.yaml; both channels union
    tmp = _mktmp_project()
    (tmp / ".claude").mkdir()
    (tmp / ".claude" / "sprint-config.yaml").write_text(
        "gate_on:\n  - narrative_detector\n")
    assert cal.is_promoted("narrative_detector", tmp) is True
    assert cal.is_promoted("absence_detector", tmp) is False


def test_signals_from_trust_matches_ledger_derivation():
    # the HQ-side derivation (from an export's trust block) agrees with the
    # local ledger derivation for the same numbers
    trust = {"absence_detector": {"fired": 12, "true_catch": 10,
                                  "false_block": 2, "precision": 10 / 12}}
    sig = cal.signals_from_trust(trust)["absence_detector"]
    assert sig["kind"] == "detector"
    assert sig["resolved"] == 12
    assert abs(sig["precision"] - 10 / 12) < 1e-9


# ---------- the GATEABLE vs AUTO_PROMOTABLE split (v0.196) ----------

def test_recall_detectors_are_gateable_but_not_auto_promotable():
    # the human may gate them; the loop may not auto-promote them (fb-f737a0b753bd)
    for det in ("absence_detector", "narrative_detector", "ui_coverage_detector"):
        assert det in cal.GATEABLE
        assert det not in cal.AUTO_PROMOTABLE


def test_apply_accepts_a_gateable_detector_as_human_decision():
    # `apply` is the operator's gate decision — it accepts a GATEABLE detector even
    # though the loop would not auto-suggest it (the human owns the false-block risk)
    tmp = _mktmp_project()
    assert cal.apply("narrative_detector", root=tmp) == 0
    assert cal.is_promoted("narrative_detector", tmp) is True


def test_reserved_is_neither_gateable_nor_auto_promotable():
    assert "delta_detector" not in cal.GATEABLE
    assert "delta_detector" not in cal.AUTO_PROMOTABLE
    assert "delta_detector" in cal._RESERVED


# ---------- taxonomy completeness (the forcing function) ----------

def test_every_gateable_is_a_known_detector_and_not_reserved():
    for det in cal.GATEABLE:
        assert cal.classify(det) == "detector"
        assert det not in cal._RESERVED


def test_every_auto_promotable_is_gateable_and_known():
    # anything the loop may auto-promote must also be human-gateable + a real detector
    for det in cal.AUTO_PROMOTABLE:
        assert det in cal.GATEABLE
        assert cal.classify(det) == "detector"
        assert det not in cal._RESERVED


def test_recall_detectors_are_classified_detectors():
    for det in ("absence_detector", "narrative_detector", "delta_detector",
                "ui_coverage_detector"):
        assert cal.classify(det) == "detector"
