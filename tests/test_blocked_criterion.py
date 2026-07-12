"""Blocked-on-external acceptance criterion (v0.81.0, field finding #16): a criterion that
needs operator-provided live setup (a real Stripe key) is DEFERRED, not run —
visible + justified, so the other criteria can pass cleanly without faking it."""

from __future__ import annotations

import os
import shutil

import yaml

from tests._common import _mktmp_project  # noqa: F401,E402
from prusik import gate, schema


def test_blocked_criterion_deferred_not_failed():
    tmp = _mktmp_project()
    os.environ["CLAUDE_PROJECT_DIR"] = str(tmp)
    try:
        (tmp / "briefs").mkdir(exist_ok=True)
        (tmp / "briefs" / "feat.md").write_text("# brief\n")
        (tmp / "briefs" / "feat.criteria.yaml").write_text(yaml.safe_dump({
            "schema_version": "1.0",
            "criteria": [
                {"id": "A1", "description": "runs", "verify_command": "true"},
                {"id": "A2", "description": "stripe e2e", "blocked_external": True,
                 "blocked_reason": "needs live STRIPE_SECRET_KEY + whsec_"},
            ]}))
        all_passed, results = gate._run_success_criteria("feat", tmp)
        assert all_passed is True                          # blocked ≠ failure
        a2 = next(r for r in results if r["id"] == "A2")
        assert a2.get("blocked") is True
        assert a2.get("passed") is None
        assert "success_criterion_blocked" in (tmp / ".sprint" / "ledger.jsonl").read_text()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_schema_blocked_requires_reason_but_not_verify_command():
    tmp = _mktmp_project()
    try:
        cp = tmp / "feat.criteria.yaml"
        # blocked + reason, no verify_command → valid
        cp.write_text(yaml.safe_dump({
            "schema_version": "1.0",
            "criteria": [{"id": "A1", "description": "x", "blocked_external": True,
                          "blocked_reason": "needs a live sandbox key"}]}))
        ok, errs = schema.validate_criteria_file(cp, project_root=tmp)
        assert ok, errs
        # blocked WITHOUT reason → error
        cp.write_text(yaml.safe_dump({
            "schema_version": "1.0",
            "criteria": [{"id": "A1", "description": "x", "blocked_external": True}]}))
        ok, errs = schema.validate_criteria_file(cp, project_root=tmp)
        assert not ok and any("blocked_reason" in e for e in errs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
