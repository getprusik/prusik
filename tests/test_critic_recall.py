"""Critic-recall instrument — the unmeasured half of trust (precision = catches,
recall = MISSES). The structural fact (An adopter): a miss leaves no critic-ledger trail
(the critic passed), so recall is inferred from DOWNSTREAM CATCHES WITH PROVENANCE
and confirmed by recording. Honest by construction: recall is computed over
LABELLED misses only and reported as an UPPER BOUND while candidates pend — a clean
number can never hide an unrecorded miss (the mirror of the 0/16-looked-fine trap
that catch_quality's critic precision fix closed).
"""

from __future__ import annotations

from prusik import critic_recall as cr


def _approve(feature="f1", role="conventions-enforcer", ts="2026-06-08T02:00"):
    return {"event": "critic_verdict", "feature": feature, "role": role,
            "artifact": "code", "verdict": "approved", "content_hash": "h",
            "ts": ts}


def _reject_then_approve(feature="f1", role="scope-critic"):
    # a real critic catch (rejection → hash-changed revision → approval), so the
    # recall denominator has a numerator to divide
    return [
        {"event": "critic_verdict", "feature": feature, "role": role,
         "artifact": "brief.md", "verdict": "revise", "content_hash": "h1",
         "ts": "2026-06-08T01:00"},
        {"event": "critic_verdict", "feature": feature, "role": role,
         "artifact": "brief.md", "verdict": "approved", "content_hash": "h2",
         "ts": "2026-06-08T01:30"},
    ]


def _evidence_catch(feature="f1", ts="2026-06-08T03:00", ok=False):
    return {"event": "reviewer_execution_verified", "feature": feature,
            "ok": ok, "command": "pytest -q", "ts": ts}


def test_downstream_catch_after_approval_is_a_candidate():
    """An evidence-gate catch that POSTDATES a critic approval on the same feature
    surfaces as a candidate miss, attributing the approving role as suggested owner
    (provenance, not a charge)."""
    records = [_approve(ts="2026-06-08T02:00"),
               _evidence_catch(ts="2026-06-08T03:00")]
    cands = cr.infer_candidates(records)
    assert len(cands) == 1
    assert cands[0]["feature"] == "f1"
    assert cands[0]["downstream_catcher"] == "evidence_gate"
    assert "conventions-enforcer" in cands[0]["candidate_owners"]


def test_catch_before_any_approval_is_not_a_candidate():
    """A downstream catch that PREDATES every approval isn't a critic miss — the
    critic hadn't passed it yet."""
    records = [_evidence_catch(ts="2026-06-08T01:00"),
               _approve(ts="2026-06-08T02:00")]
    assert cr.infer_candidates(records) == []


def test_passing_evidence_is_never_a_candidate():
    records = [_approve(ts="2026-06-08T02:00"),
               _evidence_catch(ts="2026-06-08T03:00", ok=True)]
    assert cr.infer_candidates(records) == []


def test_record_miss_emits_ledger_event(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(cr.ledger, "append",
                        lambda ev, **kw: captured.update({"event": ev, **kw}))
    rc = cr.record_miss("absence", "scope-critic", feature="f1",
                        reason="plan step D3 e2e never written")
    assert rc == 0
    assert captured["event"] == "critic_miss"
    assert captured["defect_class"] == "absence"
    assert captured["owner"] == "scope-critic"


def test_record_miss_rejects_unknown_class(monkeypatch):
    monkeypatch.setattr(cr.ledger, "append", lambda *a, **k: None)
    assert cr.record_miss("not-a-class", "scope-critic") == 2


def test_recall_is_upper_bound_while_candidates_pend():
    """With a real catch but an UNCONFIRMED candidate, recall reads 100% over
    confirmed — and MUST be flagged an upper bound, never a clean all-clear."""
    records = [*_reject_then_approve(),
               _approve(ts="2026-06-08T02:00"),
               _evidence_catch(ts="2026-06-08T03:00")]
    s = cr.recall_summary(records)
    assert s["catches"] == 1 and s["misses"] == 0
    assert s["recall_pct"] == 100          # over confirmed only
    assert s["is_upper_bound"] is True      # but a candidate pends → not clean
    assert s["pending_candidates"] == 1


def test_confirmed_miss_lowers_recall_and_buckets_by_class():
    records = [*_reject_then_approve(),     # 1 critic catch
               {"event": "critic_miss", "defect_class": "absence",
                "owner": "scope-critic", "feature": "f1", "ts": "x"}]
    s = cr.recall_summary(records)
    assert s["catches"] == 1 and s["misses"] == 1
    assert s["recall_pct"] == 50            # 1 / (1 + 1)
    assert s["by_class"]["absence"] == 1
    assert s["is_upper_bound"] is False     # no pending candidates → exact


def test_taxonomy_covers_saavis_four_classes_and_flags_structural():
    keys = set(cr.ESCAPE_CLASSES)
    assert keys == {"absence", "cross_integration", "narrative_claim",
                    "unexamined_delta"}
    # cross_integration is structurally post-merge → NOT chargeable to a reviewing
    # critic (owned by the CI layer), unlike the other three
    assert cr.ESCAPE_CLASSES["cross_integration"]["pre_integration_detectable"] is False
    assert cr.ESCAPE_CLASSES["absence"]["pre_integration_detectable"] is True


def test_empty_ledger_recall_is_unmeasured_not_zero():
    s = cr.recall_summary([])
    assert s["recall_pct"] is None and s["catches"] == 0 and s["misses"] == 0
