"""auto_solo triage rules with no size/domain constraint (e.g. `{type: test}`) fire on
type alone, so a size-L/XL or multi-domain sprint could auto-route SOLO and be
under-resourced. We don't override the route (solo may be right for a cohesive system)
— we FLAG it for operator confirmation.

moat-finding: fb-9d0bc5d0d58e
"""

from __future__ import annotations

from prusik.triage import decide

_CFG = {"triage": {"heuristics": {
    "auto_solo_if": [{"type": "test"}, {"size": "S", "domains_count": 1}],
    "auto_team_if": [{"size": "L"}], "else": "solo"}}}


def test_small_test_sprint_solo_no_flag():
    mode, reason = decide({"size": "S", "domains": ["backend"]}, {"type": "test"}, _CFG)
    assert mode == "solo" and "⚠" not in reason


def test_large_multidomain_test_sprint_still_solo_but_flagged():
    mode, reason = decide({"size": "L", "domains": ["backend", "frontend", "infra"]},
                          {"type": "test"}, _CFG)
    assert mode == "solo"                              # route unchanged (cohesive may be right)
    assert "⚠" in reason and "size=L" in reason and "domains=3" in reason


def test_multidomain_test_sprint_flagged():
    _, reason = decide({"size": "M", "domains": ["a", "b"]}, {"type": "test"}, _CFG)
    assert "⚠" in reason                              # >=2 domains alone trips the flag


def test_size_constrained_rule_not_flagged():
    """A rule that already carries a size/domain constraint isn't the unconstrained
    case — no spurious flag."""
    _, reason = decide({"size": "S", "domains": ["backend"]}, {"type": "chore"}, _CFG)
    assert "⚠" not in reason
