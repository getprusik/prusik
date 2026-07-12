"""Narrative-claim detector — the builder's "baseline-proven / pre-existing / flake"
prose gets the same prove-or-it-didn't-happen floor as the reviewer's (field escape
#3: a build report said "BASELINE PROVEN: fails on main" when main was green, and
nothing checked it). Proof is FEATURE-SCOPED — a claim in feature F's report is
backed only by F's own successful `baseline prove`, so another feature's proof can't
launder it. Precision-first: a backed claim and a negated/vague mention never flag.
"""

from __future__ import annotations

from pathlib import Path

from prusik import narrative_claim as nc


def _report(root: Path, role: str, body: str) -> None:
    d = root / "reports" / "feat"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"build-{role}.txt").write_text(body)


def _proven(feature: str, test: str = "t", proven: bool = True) -> dict:
    return {"event": "known_failure_baseline", "feature": feature, "test": test,
            "proven": proven, "action": "prove"}


def _check(root, records=None):
    return nc.narrative_check("feat", root, records=records or [])


def test_unbacked_baseline_proven_claim_is_flagged(tmp_path):
    """An adopter's exact escape: the report ASSERTS a baseline proof that never ran."""
    _report(tmp_path, "cd-b", "Full suite: 2 failures.\nBASELINE PROVEN: fails on main.\n")
    rep = _check(tmp_path)
    assert rep["unbacked"] is True and rep["clean"] is False
    labels = {c["claim"] for c in rep["claims"]}
    assert "baseline-proven" in labels and "fails-on-base" in labels


def test_claim_backed_by_proof_for_this_feature_is_clean(tmp_path):
    _report(tmp_path, "cd-b", "BASELINE PROVEN: 1 pre-existing failure.\n")
    assert _check(tmp_path, [_proven("feat")])["clean"] is True


def test_proof_for_another_feature_does_not_back_this_claim(tmp_path):
    """THE precision fix: feature `other` ran a real baseline prove, but `feat`'s
    report claims baseline-proven without proving anything for `feat`. Another
    feature's proof must NOT launder it."""
    _report(tmp_path, "cd-b", "baseline proven: fails on base.\n")
    rep = _check(tmp_path, [_proven("other"), _proven("yet-another")])
    assert rep["unbacked"] is True and rep["clean"] is False


def test_refused_prove_is_not_proof(tmp_path):
    """A REFUSED prove records proven=False (and writes no known-failures entry) — a
    report still asserting the proof stays flagged (the floor holds)."""
    _report(tmp_path, "cd-b", "baseline-proven: fails on base.\n")
    assert _check(tmp_path, [_proven("feat", proven=False)])["unbacked"] is True


def test_report_with_no_proof_claims_is_clean(tmp_path):
    _report(tmp_path, "cd-a", "Full suite: 1106 passed, 0 failed. All green.\n")
    rep = _check(tmp_path)
    assert rep["claims"] == [] and rep["clean"] is True


def test_negated_mention_does_not_match_affirmative_patterns(tmp_path):
    """'no pre-existing failures' is not an assertion that a proof was done — it must
    not match (avoid the false-positive that would erode the gate)."""
    _report(tmp_path, "cd-a", "No pre-existing failures; nothing to baseline.\n")
    assert _check(tmp_path)["claims"] == []


def test_proven_baselines_are_reported_for_transparency(tmp_path):
    _report(tmp_path, "cd-b", "baseline proven.\n")
    rep = _check(tmp_path, [_proven("feat", test="tests/x::a")])
    assert rep["proven_baselines"] == ["tests/x::a"]


def test_unproven_dismissal_of_a_red_is_flagged(tmp_path):
    """An adopter: the class is unproven-dismissal-of-a-red, not one phrase. A report that
    dismisses an actual failure without proof is flagged even with NO 'baseline proven'."""
    _report(tmp_path, "cd-b",
            "Suite: 3 failed.\nThese are pre-existing and unrelated to our changes.\n")
    rep = _check(tmp_path)
    assert rep["unbacked"] is True
    assert any("unproven-dismissal" in c["claim"] for c in rep["claims"])


def test_rephrased_dismissal_is_still_caught(tmp_path):
    """Gameability fix: swapping 'baseline proven' for other dismissal words doesn't
    evade it — the semantics match, not a literal string."""
    _report(tmp_path, "cd-b", "2 failed.\nKnown flake, transient, out of scope.\n")
    assert _check(tmp_path)["unbacked"] is True


def test_dismissal_in_a_green_report_does_not_flag(tmp_path):
    """Precision: a dismissal word with NO actual failure isn't dismissing anything —
    a clean report mentioning 'out of scope' work must not flag."""
    _report(tmp_path, "cd-a",
            "1106 passed, 0 failed.\nDeferred items (out of scope): admin polish.\n")
    rep = _check(tmp_path)
    assert rep["claims"] == [] and rep["clean"] is True


def test_no_reports_is_clean(tmp_path):
    assert _check(tmp_path)["clean"] is True


def test_scan_claims_catches_flaky_assertion():
    claims = nc.scan_claims("test_x is proven flaky (3P/2F).")
    assert any(c["claim"] == "proven-flaky" for c in claims)
