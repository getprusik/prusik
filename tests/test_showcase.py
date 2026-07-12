"""Showcase — composed trust dossier, the 3 claims, roster, CLI (v0.48.0)."""

from __future__ import annotations

import json

from tests._common import (  # noqa: F401,E402
    _capture_stdout,
    _mktmp_project,
    _write_ledger,
)
from prusik import showcase


def _full_journey(feature="feat"):
    """A journey with intent sign-off, an upfront critic catch + rewind,
    an evidence pass, and completion — exercises all 7 beats."""
    return [
        {"ts": "2026-06-01T00:00:00+00:00", "event": "sprint_started",
         "feature": feature},
        {"ts": "2026-06-01T00:00:05+00:00", "event": "critic_verdict",
         "role": "brief-critic", "verdict": "PASS", "artifact": "brief",
         "feature": feature},
        # scope-critic rejects → an UPFRONT catch (classified by role)
        {"ts": "2026-06-01T00:00:10+00:00", "event": "critic_verdict",
         "role": "scope-critic", "verdict": "REVISE", "artifact": "scope",
         "feature": feature},
        {"ts": "2026-06-01T00:01:00+00:00", "event": "phase_advance",
         "from_phase": "scoping", "to_phase": "planning", "feature": feature},
        {"ts": "2026-06-01T00:02:00+00:00", "event": "phase_advance",
         "from_phase": "planning", "to_phase": "building", "feature": feature},
        {"ts": "2026-06-01T00:03:00+00:00", "event": "phase_advance",
         "from_phase": "building", "to_phase": "reviewing", "feature": feature},
        # evidence gate passes
        {"ts": "2026-06-01T00:03:30+00:00", "event": "reviewer_execution_verified",
         "ok": True, "command": "pytest -q", "feature": feature},
        # a rewind back to planning, then forward again (self-correction)
        {"ts": "2026-06-01T00:03:40+00:00", "event": "phase_rewind",
         "from_phase": "reviewing", "to_phase": "planning", "feature": feature},
        {"ts": "2026-06-01T00:04:00+00:00", "event": "phase_advance",
         "from_phase": "planning", "to_phase": "building", "feature": feature},
        {"ts": "2026-06-01T00:05:00+00:00", "event": "phase_advance",
         "from_phase": "building", "to_phase": "reviewing", "feature": feature},
        {"ts": "2026-06-01T00:06:00+00:00", "event": "sprint_complete",
         "feature": feature, "actual": {"tokens": 50000, "duration_min": 6}},
    ]


# ---------- composition ----------

def test_dossier_composes_all_beats():
    recs = _full_journey()
    d = showcase.dossier("feat", recs)
    assert d["intent"]["brief_critic"] == "PASS"
    assert any(a["role"] == "scope-critic" for a in d["adversarial"])
    assert d["self_correction"]["rewinds"] == 1
    assert any(e["ok"] for e in d["evidence"])
    assert d["objective"]["complete"] is True


def test_scope_critic_reject_is_an_upfront_flag():
    recs = _full_journey()
    d = showcase.dossier("feat", recs)
    # scope-critic REVISE → an upfront adversarial flag (by role, not phase)
    assert d["metrics"]["adversarial_flags"]["upfront"] >= 1
    assert d["trust_check"]["caught_upfront"] is True
    # and it shows in the labeled ledger as still-open (unlabeled), honestly
    assert d["metrics"]["labeled"]["open"] >= 1


def test_trust_check_three_claims_evidenced():
    d = showcase.dossier("feat", _full_journey())
    tc = d["trust_check"]
    assert tc["loop_in_place"] is True       # critics fired + rewind
    assert tc["caught_upfront"] is True       # scope catch in scoping
    assert tc["reached_outcome"] is True      # completed


def test_caught_upfront_is_none_when_no_catches():
    # a clean journey with no critic rejects → "caught_upfront" must be None
    # (not enough evidence to assert), never a false claim.
    recs = [
        {"ts": "2026-06-01T00:00:00+00:00", "event": "sprint_started",
         "feature": "clean"},
        {"ts": "2026-06-01T00:01:00+00:00", "event": "phase_advance",
         "from_phase": "scoping", "to_phase": "building", "feature": "clean"},
        {"ts": "2026-06-01T00:02:00+00:00", "event": "sprint_complete",
         "feature": "clean"},
    ]
    d = showcase.dossier("clean", recs)
    assert d["trust_check"]["caught_upfront"] is None
    assert d["metrics"]["adversarial_flags"]["upfront"] == 0


def test_intent_enrichment_reads_brief_when_present():
    tmp = _mktmp_project()
    (tmp / "briefs").mkdir(parents=True, exist_ok=True)
    (tmp / "briefs" / "feat.md").write_text(
        "## Goal\nMake the thing fast\n\n## Success criteria\np95 under 200ms\n")
    _write_ledger(tmp, _full_journey())
    d = showcase.dossier("feat", showcase.ledger.read_all())
    assert "fast" in d["intent"]["goal"]
    assert "200ms" in d["intent"]["success"]


def test_intent_lead_collapses_wraps_and_stops_at_subheading():
    # Reproduces the adopter brief shape: a goal wrapped across lines, and a Success
    # section with a nested `### A` sub-heading. The dossier must show clean
    # single lines, not leaked newlines or the sub-heading.
    tmp = _mktmp_project()
    (tmp / "briefs").mkdir(parents=True, exist_ok=True)
    (tmp / "briefs" / "feat.md").write_text(
        "## Goal\nReplace the cold dump with a warm\nwelcome screen\n\n"
        "## Success criteria\nSibling criteria.yaml declares 9 criteria.\n\n"
        "### A\nfirst criterion detail\n")
    _write_ledger(tmp, _full_journey())
    d = showcase.dossier("feat", showcase.ledger.read_all())
    assert d["intent"]["goal"] == "Replace the cold dump with a warm welcome screen"
    assert d["intent"]["success"] == "Sibling criteria.yaml declares 9 criteria."
    assert "###" not in d["intent"]["success"]
    assert "\n" not in d["intent"]["goal"]


def test_journey_features_lists_only_journeys():
    recs = _full_journey() + [
        {"ts": "2026-06-01T09:00:00+00:00", "event": "serve_brief_authored",
         "slug": "x"},
    ]
    assert showcase.journey_features(recs) == ["feat"]


# ---------- CLI ----------

def test_cli_roster_empty():
    _mktmp_project()
    out = _capture_stdout(lambda: showcase.run())
    assert "no journeys" in out


def test_cli_unknown_feature_is_an_error():
    tmp = _mktmp_project()
    _write_ledger(tmp, _full_journey())
    rc = showcase.run(feature="nope")
    assert rc == 1


def test_cli_dossier_renders_beats():
    tmp = _mktmp_project()
    _write_ledger(tmp, _full_journey())
    out = _capture_stdout(lambda: showcase.run(feature="feat"))
    for beat in ("INTENT", "PROGRESS", "ADVERSARIAL", "SELF-CORRECTION",
                 "EVIDENCE", "METRICS", "OBJECTIVE", "trust check"):
        assert beat in out


def test_cli_json_dossier_shape():
    tmp = _mktmp_project()
    _write_ledger(tmp, _full_journey())
    out = _capture_stdout(lambda: showcase.run(feature="feat", json_output=True))
    d = json.loads(out)
    assert d["feature"] == "feat"
    assert set(d["trust_check"]) == {"loop_in_place", "caught_upfront",
                                     "reached_outcome"}


def test_cli_roster_lists_feature():
    tmp = _mktmp_project()
    _write_ledger(tmp, _full_journey())
    out = _capture_stdout(lambda: showcase.run())
    assert "feat" in out
    assert "journeys" in out
