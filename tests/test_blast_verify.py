"""Blast-radius predicted→verified gate (field retro #1, v0.96.0).

The moat-loop exemplar: this replays the adopter friction (scope predicted a route's
tests would regress; the build added a guard but never updated those tests) as a
permanent regression test, so prusik cannot re-break what the adopter taught it.
See benchmarks/cases/field-ts/blast-radius-predicted-not-verified/README.md.

moat-finding: fb-32dba3592dd4
"""

from __future__ import annotations

import shutil

from tests._common import _mktmp_project  # noqa: F401
from prusik import blast_plan, consistency


def _fixture(tmp):
    """A route with a contract, an OUTSIDE test that references it, a plan that
    declares the route module, and a worktree where the builder touched the route
    but NOT the test."""
    (tmp / ".sprint").mkdir(exist_ok=True)
    (tmp / "src").mkdir(exist_ok=True)
    (tmp / "src" / "billing.py").write_text(
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/billing")\n'
        '@router.get("/checkout")\n'
        "def checkout():\n    return {}\n")
    (tmp / "tests").mkdir(exist_ok=True)
    (tmp / "tests" / "test_checkout.py").write_text(
        "def test_checkout_route():\n"
        '    assert "/billing/checkout"  # exercises the guarded route\n')
    (tmp / "design" / "feat").mkdir(parents=True, exist_ok=True)
    (tmp / "design" / "feat" / "plan.md").write_text(
        "## Modules touched\n- src/billing.py\n")
    # builder's worktree: touched src/billing.py (added a guard), NOT the test
    wt = tmp / "worktrees" / "solo" / "src"
    wt.mkdir(parents=True, exist_ok=True)
    (wt / "billing.py").write_text(
        "from fastapi import APIRouter, Depends\n"
        'router = APIRouter(prefix="/billing")\n'
        '@router.get("/checkout", dependencies=[Depends(require_feature)])\n'
        "def checkout():\n    return {}\n")


def test_sprint_changed_files_collects_worktree_writes():
    tmp = _mktmp_project()
    try:
        _fixture(tmp)
        changed = consistency.sprint_changed_files(tmp)
        assert "src/billing.py" in changed
        assert "tests/test_checkout.py" not in changed   # never written to worktree
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_prediction_recorded_and_unconsumed_test_is_flagged():
    """The core: scope predicts tests/test_checkout.py is at-risk; the build
    touched the route but not the test → blast-verify names it as unverified."""
    tmp = _mktmp_project()
    try:
        _fixture(tmp)
        pred = blast_plan.record_prediction("feat", tmp)
        assert "tests/test_checkout.py" in pred["at_risk_tests"]
        assert blast_plan._prediction_path("feat", tmp).exists()  # persisted

        v = blast_plan.verify_prediction("feat", tmp)
        assert v["unverified"] == ["tests/test_checkout.py"]      # the foreseen gap
        adv = blast_plan.verification_advisory("feat", tmp)
        assert adv and "tests/test_checkout.py" in adv and "NOT updated" in adv
        # strict mode is the hard gate
        assert blast_plan.verify_run("feat", tmp, strict=True) == 1
        assert blast_plan.verify_run("feat", tmp, strict=False) == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_touching_the_predicted_test_clears_the_flag():
    """Positive control: once the build updates the predicted-regressing test, the
    prediction is consumed → no unverified, gate passes."""
    tmp = _mktmp_project()
    try:
        _fixture(tmp)
        # builder now ALSO updates the at-risk test in the worktree
        wt_tests = tmp / "worktrees" / "solo" / "tests"
        wt_tests.mkdir(parents=True, exist_ok=True)
        (wt_tests / "test_checkout.py").write_text(
            "def test_checkout_route_free_tier_rejected():\n"
            "    pass  # updated for the new guard\n")
        v = blast_plan.verify_prediction("feat", tmp)
        assert v["unverified"] == []
        assert blast_plan.verification_advisory("feat", tmp) is None
        assert blast_plan.verify_run("feat", tmp, strict=True) == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
