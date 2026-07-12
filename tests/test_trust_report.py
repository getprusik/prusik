"""The adopter TRUST REPORT (Horizon-2 D) composes per-repo ROI from the adopter's own
ledger: a fidelity probe + verification catches (with precision) + prevention activity
(counts only — a preventive control's value is prevention, not precision) + throughput.
Honest by construction: '–' when the ledger doesn't evidence a claim.

moat-finding: roadmap-horizon-2d-trust-report
"""

from __future__ import annotations

from prusik import trust_report


def _records():
    return [
        {"event": "sprint_started", "feature": "f1"},
        {"event": "sprint_started", "feature": "f2"},
        {"event": "sprint_complete", "feature": "f1"},
        {"event": "reviewer_execution_verified", "ok": False, "feature": "f1",
         "phase": "reviewing", "ts": "2026-06-08T01:00"},               # evidence-gate catch
        {"event": "critic_verdict", "verdict": "reject", "feature": "f1",
         "ts": "2026-06-08T01:01"},                                     # critic catch
        {"event": "gate_blocked", "feature": "f1", "tool": "Write",
         "ts": "2026-06-08T01:02"},                                     # prevention
        {"event": "gate_blocked", "feature": "f1", "tool": "Bash",
         "ts": "2026-06-08T01:03"},                                     # prevention
    ]


def test_compose_buckets_verification_prevention_throughput():
    rep = trust_report.compose(_records(), config=None)
    assert rep["fidelity"] is None                                      # no config probe
    assert "evidence_gate" in rep["verification"]
    assert "critic" in rep["verification"]
    assert "writable_gate" in rep["prevention"]
    assert rep["prevention"]["writable_gate"]["fired"] == 2
    assert rep["throughput"] == {"sprints_completed": 1, "sprints_started": 2}
    assert rep["total_fires"] == 4                                      # 2 blocks + ev + critic


def test_empty_ledger_is_all_honest_dashes():
    rep = trust_report.compose([], config=None)
    assert rep["verification"] == {} and rep["prevention"] == {}
    assert rep["throughput"] == {"sprints_completed": 0, "sprints_started": 0}
    txt = trust_report.render_text(rep)
    assert "– (no evidence-gate" in txt
    assert "– (no writable" in txt


def test_text_separates_precision_from_preventive_counts():
    txt = trust_report.render_text(trust_report.compose(_records(), config=None))
    assert "true-catch" in txt                                         # verification = precision
    assert "blocked" in txt                                            # prevention = count
    # the preventive caveat is stated, so a count is never mis-read as precision
    assert "not precision" in txt


def test_recall_section_composed_with_detectors_and_upper_bound():
    """The dossier's recall half: critic catches vs confirmed misses, an UPPER-BOUND
    recall while candidates pend, and the out-of-diff detectors with EARNED precision."""
    records = _records() + [
        # a narrative detector flag that a later proof confirms → derived true-catch
        {"event": "narrative_flagged", "feature": "f1", "ts": "2026-06-08T02:00"},
        {"event": "known_failure_baseline", "feature": "f1", "proven": True,
         "test": "t", "ts": "2026-06-08T02:01"},
    ]
    rep = trust_report.compose(records, config=None)
    rec = rep["recall"]
    assert "narrative_detector" in rec["detectors"]
    assert rec["detectors"]["narrative_detector"]["precision"] == 1.0   # derived, earned
    txt = trust_report.render_text(rep)
    assert "3. RECALL" in txt and "narrative_detector" in txt


def test_verdict_is_computed_and_present():
    # no config → fidelity unprobed → verdict says so (computed, not marketing)
    rep = trust_report.compose(_records(), config=None)
    assert "verdict" in rep
    assert "prusik init" in rep["verdict"]
    assert "VERDICT" in trust_report.render_text(rep)


def test_html_is_self_contained_and_offline(tmp_path):
    h = trust_report.render_html(trust_report.compose(_records(), config=None))
    assert "<!doctype html>" in h.lower()
    assert "trust report" in h.lower()
    assert "http://" not in h and "https://" not in h                  # no external refs
    assert "writable_gate" in h                          # technical id kept (muted .raw)
    assert "Stay-in-scope guard" in h                    # plain-English gate label
    assert "what it might miss" in h.lower()             # recall section, plain-language
    assert "verdict" in h.lower()
