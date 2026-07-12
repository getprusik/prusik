"""Early structural-blocker (thrash) signal (fb-983dac02ac8d): multiple
fix-rounds without the reviewer reaching a PASS usually means a kit-mechanics blocker,
not a product defect — surface it before the token burn. Pure ledger derivation.

moat-finding: fb-983dac02ac8d
"""

from __future__ import annotations

from prusik import convergence as c


def _fr(feature, n=1):
    return [{"event": "fix_round_start", "feature": feature, "round": i + 1}
            for i in range(n)]


def test_under_two_rounds_is_silent():
    assert c.thrash_advisory(_fr("feat", 1), "feat") is None


def test_two_rounds_no_pass_advises_structural():
    msg = c.thrash_advisory(_fr("feat", 2), "feat")
    assert msg is not None
    assert "STRUCTURAL" in msg and "2 fix-round" in msg and "escalat" in msg.lower()


def test_pass_reached_silences_it():
    recs = _fr("feat", 3) + [{"event": "phase_advance", "feature": "feat",
                              "to_phase": "integrating"}]
    assert c.thrash_advisory(recs, "feat") is None    # reached a PASS → not thrashing


def test_convergence_stalls_strengthen_the_signal():
    recs = _fr("feat", 2) + [{"event": "convergence_stall", "feature": "feat"},
                             {"event": "convergence_stall", "feature": "feat"}]
    msg = c.thrash_advisory(recs, "feat")
    assert "2 convergence-stall(s)" in msg


def test_other_features_do_not_count():
    recs = _fr("feat", 1) + _fr("other", 3)           # only 1 round for 'feat'
    assert c.thrash_advisory(recs, "feat") is None
