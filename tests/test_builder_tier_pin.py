"""Builder roles keep their tier class at every stakes level; the roles that judge the work
still move with stakes, and a plan's `## Model tiers` item can still lift a builder.

moat-finding: fb-2f3eb3b58869
"""

from __future__ import annotations

import pytest

from prusik import model_tier
from tests.test_model_tier import (_EM, _FEATURE, _decision, _plan, _profile,  # noqa: F401
                                   proj, ready)

_BUILDERS = ("backend-builder", "frontend-builder", "solo-builder", "test-writer")


def test_builder_roles_are_the_four_build_roles():
    assert model_tier.BUILDER_ROLES == frozenset(_BUILDERS)
    for r in _BUILDERS:
        assert model_tier.BASE_CLASS[r] == "bounded", r


@pytest.mark.parametrize("level", ["low", "normal", "high", None])
def test_builders_never_move_with_stakes(level):
    prof = _profile()
    for r in _BUILDERS:
        # unknown stakes (None) is not an error for a builder: it needs no triage
        assert model_tier.tier_class(r, level, prof) == ("bounded", "fixed"), (r, level)


def test_judges_still_move_with_stakes():
    prof = _profile()
    for r in ("regression-sentinel", "conventions-enforcer"):
        assert model_tier.tier_class(r, "high", prof) == ("high_stakes", "stakes"), r
    assert model_tier.tier_class("plan-critic", "low", prof) == ("bounded", "stakes")
    with pytest.raises(model_tier.TierError):
        model_tier.tier_class("regression-sentinel", None, prof)


def test_high_stakes_sprint_resolves_builders_bounded_and_judges_high(ready):
    # the observed-writes shape: L, P1, team, hook + migration risks -> high stakes
    _decision(ready, size="L", risks=("hook path and migration ordering",))
    res = model_tier.resolve(_FEATURE)
    assert res.stakes.level == "high"
    for r in _BUILDERS:
        t = res.roles[r]
        assert (t.tier_class, t.model, t.source) == ("bounded", "sonnet", "fixed"), r
    assert res.roles["regression-sentinel"].tier_class == "high_stakes"


def test_a_builder_resolves_before_triage(ready):
    # no decision file: a stakes-sensitive role would fail closed; a builder does not need it
    (ready / "decisions" / f"{_FEATURE}.json").unlink(missing_ok=True)
    res = model_tier.resolve(_FEATURE, role="backend-builder")
    assert res.roles["backend-builder"].model == "sonnet"
    with pytest.raises(model_tier.TierError):
        model_tier.resolve(_FEATURE, role="regression-sentinel")


def test_a_plan_override_still_lifts_a_builder(ready):
    _plan(ready, [f"- backend-builder: high_stakes {_EM} rewrites the hook hot path"])
    t = model_tier.resolve(_FEATURE, role="backend-builder").roles["backend-builder"]
    assert (t.tier_class, t.model, t.source, t.reason) == (
        "high_stakes", "opus", "override", "rewrites the hook hot path")
