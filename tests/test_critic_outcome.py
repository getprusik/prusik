"""Critic outcome-capture: the critic is the gate adopters value most ("it
externalizes the adversary"), but it had NO auto outcome-capture — a critic
rejection sat UNRESOLVED until a human hand-labelled it, so a real fleet could
read `critic fired=16, true_catch=0, precision=None` purely because nobody ran
`prusik catch`. This derives the catch from the ledger: a non-approved verdict
on (role, feature, artifact) that a LATER approving verdict on the SAME key
confirms — but ONLY when the content hash changed between them (the artifact was
actually revised). An approval re-stamping the same hash is an override, not a
correction, and must NOT count.

moat-finding: field-critic-outcome-capture-gap
"""

from __future__ import annotations

from prusik import catch_quality as cq


def _resolve(records):
    return cq.resolve_catches(cq.extract_catches(records), records)


def _verdict(records, **match):
    for c in _resolve(records):
        if c["event"] == "critic_verdict" and all(
                c.get(k) == v for k, v in match.items()):
            return c["verdict"]
    raise AssertionError(f"no critic catch for {match}")


def _reject(feature="f1", role="scope-critic", artifact="brief.md",
            content_hash="h1", ts="2026-06-08T01:00"):
    return {"event": "critic_verdict", "feature": feature, "role": role,
            "artifact": artifact, "verdict": "revise", "content_hash": content_hash,
            "ts": ts}


def _approve(feature="f1", role="scope-critic", artifact="brief.md",
             content_hash="h2", ts="2026-06-08T02:00"):
    return {"event": "critic_verdict", "feature": feature, "role": role,
            "artifact": artifact, "verdict": "approved", "content_hash": content_hash,
            "ts": ts}


def test_reject_then_revised_approval_is_true_catch():
    """The core signal: rejected → artifact revised (hash h1→h2) → approved."""
    records = [_reject(), _approve()]
    assert _verdict(records, feature="f1") == cq.TRUE_CATCH


def test_reject_with_no_later_approval_stays_unresolved():
    """A rejection the ledger never shows being addressed is not a claimed catch."""
    records = [_reject()]
    assert _verdict(records, feature="f1") == cq.UNRESOLVED


def test_override_same_hash_approval_does_not_count():
    """ADVERSARIAL: the critic re-stamps APPROVED on the identical content it just
    rejected (same hash) — an override, NOT a forced correction. Must stay
    UNRESOLVED, never a true catch."""
    records = [_reject(content_hash="h1"), _approve(content_hash="h1")]
    assert _verdict(records, feature="f1") == cq.UNRESOLVED
    assert cq._critic_enforced(records) == set()


def test_approval_only_creates_no_phantom_catch():
    """An approving verdict alone is not a catch candidate at all (only
    non-approvals fire), so it can never inflate the count."""
    records = [_approve()]
    catches = cq.extract_catches(records)
    assert [c for c in catches if c["gate"] == "critic"] == []


def test_multiple_revisions_before_approval_all_count():
    """The gate held across two revisions (h1, h2) until the bar was met (h3) —
    both rejects are enforced, matching the phase-gate retry semantics."""
    records = [
        _reject(content_hash="h1", ts="2026-06-08T01:00"),
        _reject(content_hash="h2", ts="2026-06-08T01:30"),
        _approve(content_hash="h3", ts="2026-06-08T02:00"),
    ]
    enforced = cq._critic_enforced(records)
    rejects = [c for c in cq.extract_catches(records) if c["gate"] == "critic"]
    assert len(rejects) == 2 and all(r["id"] in enforced for r in rejects)


def test_approval_of_other_artifact_does_not_enforce():
    """An approval for a DIFFERENT artifact in the same feature must not resolve a
    rejection of an unrelated one (key is role+feature+artifact)."""
    records = [
        _reject(artifact="brief.md", content_hash="h1"),
        _approve(artifact="plan.md", content_hash="h9"),
    ]
    # only the brief.md rejection is a catch candidate; it must stay unresolved
    assert _verdict(records, feature="f1") == cq.UNRESOLVED


def test_critic_now_has_precision_in_summary():
    """The fleet-visible payoff: critic precision is no longer None-by-default."""
    records = [_reject(), _approve()]
    summary = cq.summarize(_resolve(records))
    assert summary["critic"]["precision"] == 1.0          # was None (unmeasured)
    assert summary["critic"][cq.TRUE_CATCH] == 1


def test_operator_label_still_wins_over_auto():
    """Manual labels remain authoritative — auto-capture only fills the gap where
    no human labelled."""
    rej = _reject()
    records = [
        rej, _approve(),
        {"event": "catch_resolved", "catch_id": cq.catch_id(rej),
         "verdict": cq.FALSE_BLOCK, "reason": "operator disagreed",
         "ts": "2026-06-08T03:00"},
    ]
    assert _verdict(records, feature="f1") == cq.FALSE_BLOCK
