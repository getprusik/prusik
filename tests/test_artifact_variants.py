"""Authoring-variant registry — the ONE surface for "is this flagged brief/scope/
plan token a benign authoring variant, not a defect?" (the brief/scope/plan
anti-recurrence generalization, the analog of capture_diagnose for evidence)."""

from __future__ import annotations

from tests._common import (  # noqa: F401,E402
    _capture_stdout,
    _mktmp_project,
)
from prusik import artifact_variants as av


# ---------- the completeness contract (the forcing function) ----------

def test_known_variants_matches_registry_exactly():
    """Mirror of capture_diagnose's detector↔name parity: a variant cannot be
    added without a stable name (observability + contract), nor a name orphaned."""
    registered = tuple(v.name for v in av._VARIANTS)
    assert set(registered) == set(av.KNOWN_VARIANTS)
    assert len(registered) == len(av.KNOWN_VARIANTS)        # no dupes
    # every registered variant has a human reason for its remedy
    for name in av.KNOWN_VARIANTS:
        assert name in av._REASON


def test_every_variant_is_idempotent():
    # a normalizer must strip only its own form — applying twice == once
    samples = ["+ a/b.py", "`x/y`", "path,", "Some-Thing", "**z**", "plain"]
    for v in av._VARIANTS:
        for s in samples:
            once = v.normalize(s)
            assert v.normalize(once) == once, f"{v.name} not idempotent on {s!r}"


# ---------- each benign variant ----------

def test_markdown_wrapper_variant():
    bv = av.variant_of("`api/billing`", {"api/billing"}, av.PATH)
    assert bv is not None and bv.canonical == "api/billing"
    assert "markdown_wrapper" in bv.variants


def test_diff_new_file_marker_variant():
    bv = av.variant_of("+ scripts/new.py", {"scripts/new.py"}, av.PATH)
    assert bv is not None and "diff_new_file_marker" in bv.variants


def test_trailing_punct_variant():
    bv = av.variant_of("web/checkout/,", {"web/checkout/"}, av.PATH)
    assert bv is not None and "trailing_punct" in bv.variants


def test_case_separator_variant_identifier_only():
    # 'Submission profile' ≡ 'submission-profile' for an IDENTIFIER
    bv = av.variant_of("Submission profile", {"submission-profile"}, av.IDENTIFIER)
    assert bv is not None and "case_separator" in bv.variants
    assert bv.canonical == "submission-profile"


def test_composed_variants_bridge_together():
    # markdown-wrapped AND case/separator at once → both reported
    bv = av.variant_of("`Submission Profile`", {"submission-profile"}, av.IDENTIFIER)
    assert bv is not None
    assert "markdown_wrapper" in bv.variants and "case_separator" in bv.variants


# ---------- the safety boundary: a real difference still differs ----------

def test_real_typo_is_not_a_benign_variant():
    # a genuine misspelling canonicalizes differently → no suppression
    assert av.variant_of("submision-profile", {"submission-profile"},
                         av.IDENTIFIER) is None


def test_paths_stay_case_sensitive():
    # case/separator folding is IDENTIFIER-only — a path case difference is NOT
    # waved through (could be a real case-sensitivity bug)
    assert av.variant_of("api/Billing", {"api/billing"}, av.PATH) is None


def test_literal_member_is_not_a_variant():
    # already canonical → None (no variant needed, nothing to suppress)
    assert av.variant_of("api/billing", {"api/billing"}, av.PATH) is None


def test_unrelated_token_is_not_a_variant():
    assert av.variant_of("totally/different", {"api/billing"}, av.PATH) is None


# ---------- anti-drift: the registry IS the shared knowledge ----------

def test_schema_path_parsing_routes_through_the_registry():
    """schema.extract_path_token strips the SAME markdown/punct the registry
    defines — proving the path surface and the identifier near-miss share one home
    (the cluster's drift was two divergent copies of this knowledge)."""
    from prusik import schema
    assert schema.extract_path_token("`scripts/foo.py`  — does stuff") == "scripts/foo.py"
    assert schema.extract_path_token("**api/billing/** — touched") == "api/billing/"
    assert schema.extract_path_token("web/checkout/, related") == "web/checkout/"


def test_brief_near_miss_uses_the_registry():
    """brief_lint near-miss suppression now flows through variant_of, so it inherits
    EVERY variant (not just the old case/separator copy) — a markdown-wrapped prose
    reference to a known sentinel is suppressed, a real typo still flags."""
    from prusik.brief_lint import _near_misses
    known = {"submission-profile"}
    assert _near_misses({"Submission profile"}, known) == []      # case/sep — benign
    assert _near_misses({"`submission-profile`"}, known) == []    # wrapped — benign
    misses = _near_misses({"submision-profile"}, known)           # typo — flagged
    assert misses and misses[0][0] == "submision-profile"


def test_suppression_is_recorded_for_measurability():
    # a suppressed benign variant is collected (the sink the lint logs to the ledger
    # as `artifact_benign_variant` — recurrence fuel, like capture_non_evidence)
    from prusik.brief_lint import _near_misses
    sink: list = []
    _near_misses({"Submission profile"}, {"submission-profile"}, suppressed=sink)
    assert len(sink) == 1
    cand, bv = sink[0]
    assert cand == "Submission profile"
    assert bv.canonical == "submission-profile"
