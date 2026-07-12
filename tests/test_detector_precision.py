"""Recall-detector precision is DERIVED, not hand-labelled (field consistency push:
'advisory-until-precision-proven' resting on a manual precision number reintroduces
the exact hand-labelling treadmill critic-capture climbed off). Two of the three have
a ledger-derivable truth trail; delta-check is the reserved human-adjudicated case.
"""

from __future__ import annotations

from pathlib import Path

from prusik import absence, catch_quality as cq

_REPO = Path(__file__).resolve().parents[1]


# ---- narrative-detector: flag → later proof for the feature = true catch ----

def test_narrative_flag_enforced_by_later_proof():
    records = [
        {"event": "narrative_flagged", "feature": "f", "ts": "2026-06-09T01:00"},
        {"event": "known_failure_baseline", "feature": "f", "proven": True,
         "test": "t", "ts": "2026-06-09T02:00"},
    ]
    catches = cq.resolve_catches(cq.extract_catches(records), records)
    nd = [c for c in catches if c["gate"] == "narrative_detector"]
    assert nd and nd[0]["verdict"] == cq.TRUE_CATCH and nd[0]["source"] == "auto"


def test_narrative_flag_unresolved_without_proof():
    records = [{"event": "narrative_flagged", "feature": "f", "ts": "t1"}]
    catches = cq.resolve_catches(cq.extract_catches(records), records)
    nd = [c for c in catches if c["gate"] == "narrative_detector"]
    assert nd and nd[0]["verdict"] == cq.UNRESOLVED


def test_narrative_proof_for_other_feature_does_not_enforce():
    records = [
        {"event": "narrative_flagged", "feature": "f", "ts": "t1"},
        {"event": "known_failure_baseline", "feature": "other", "proven": True,
         "test": "t", "ts": "t2"},
    ]
    assert cq._narrative_enforced(records) == set()


# ---- absence-detector: flag → named file later present = true catch ----

def _flag(files, ts="t1"):
    return {"event": "absence_flagged", "feature": "f", "missing_files": files, "ts": ts}


def test_absence_flag_resolved_when_named_file_appears():
    records = [_flag(["pkg/x.test.ts"])]
    cid = cq.catch_id(records[0])
    assert absence._flags_to_resolve(records, lambda f: f == "pkg/x.test.ts") == [cid]


def test_absence_flag_unresolved_while_file_absent():
    assert absence._flags_to_resolve([_flag(["pkg/x.test.ts"])], lambda f: False) == []


def test_absence_flag_not_double_resolved():
    records = [_flag(["x.ts"])]
    cid = cq.catch_id(records[0])
    records.append({"event": "catch_resolved", "catch_id": cid,
                    "verdict": "true_catch"})
    assert absence._flags_to_resolve(records, lambda f: True) == []


# ---- integrator default-disposition inversion (An adopter cross_integration win) ----

def test_integrator_defaults_to_regression_until_proven_flake():
    t = (_REPO / "prusik/templates/.claude/agents/integrator.md").read_text()
    assert "REGRESSION until proven" in t          # the inverted default
    assert "prove-flaky" in t                       # the sanctioned reclassify path
    # the corrected culprit: identical-shape every run is DETERMINISTIC, not a flake
    assert "DETERMINISTIC" in t
