"""Execution-evidence for success criteria — prove-it-fires, generalized.

A criterion may declare `kind: tests|lint|types`; the sprint-complete gate then
requires REAL WORK was observed, not just exit 0. This closes the build-time
false-clean that brief-time coherence is blind to: a verify that exits clean
while nothing actually ran. Backward compatible — a criterion with no kind keeps
plain exit-code semantics.
"""

from __future__ import annotations

import shutil

import yaml

from prusik import gate, schema
from tests._common import _mktmp_project


def _criteria(tmp, feature, entries):
    (tmp / "briefs").mkdir(exist_ok=True)
    (tmp / "briefs" / f"{feature}.md").write_text("## Goal\nx\n")
    (tmp / "briefs" / f"{feature}.criteria.yaml").write_text(
        yaml.safe_dump({"schema_version": "1.0", "criteria": entries}))


def test_kind_tests_criterion_fails_on_false_clean():
    """A test-kind verify that exits 0 with zero tests executed FAILS — the
    pool-metric class: it looked like it passed, but nothing actually ran."""
    tmp = _mktmp_project()
    try:
        _criteria(tmp, "feat", [{
            "id": "c1", "description": "unit tests pass",
            "verify_command": "echo 'no tests ran'", "kind": "tests"}])
        ok, results = gate._run_success_criteria("feat", tmp)
        assert not ok, "exit 0 but 0 tests executed must fail"
        assert results[0]["executed"] == 0 and results[0]["passed"] is False
    finally:
        shutil.rmtree(tmp)


def test_kind_tests_criterion_passes_with_real_execution():
    tmp = _mktmp_project()
    try:
        _criteria(tmp, "feat", [{
            "id": "c1", "description": "unit tests pass",
            "verify_command": "echo '3 passed in 0.1s'", "kind": "tests"}])
        ok, results = gate._run_success_criteria("feat", tmp)
        assert ok and results[0]["passed"] and results[0]["executed"] == 3
    finally:
        shutil.rmtree(tmp)


def test_no_kind_keeps_exit_code_semantics():
    """Backward compatibility: a criterion without a kind passes on exit 0 alone,
    even with no execution evidence — existing criteria.yaml files are unchanged."""
    tmp = _mktmp_project()
    try:
        _criteria(tmp, "feat", [{
            "id": "c1", "description": "command succeeds",
            "verify_command": "echo hi"}])
        ok, results = gate._run_success_criteria("feat", tmp)
        assert ok and results[0]["passed"] and results[0]["executed"] is None
    finally:
        shutil.rmtree(tmp)


def test_schema_rejects_bad_kind():
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir(exist_ok=True)
        p = tmp / "briefs" / "feat.criteria.yaml"
        p.write_text(yaml.safe_dump({"schema_version": "1.0", "criteria": [
            {"id": "c1", "description": "d", "verify_command": "echo hi",
             "kind": "smoke"}]}))
        ok, errs = schema.validate_criteria_file(p, project_root=tmp)
        assert not ok and any("kind must be one of" in e for e in errs)
    finally:
        shutil.rmtree(tmp)
