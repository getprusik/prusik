"""Execution-evidence for success criteria — prove-it-fires, generalized.

A criterion may declare `kind: tests|lint|types`; the sprint-complete gate then
requires REAL WORK was observed, not just exit 0. This closes the build-time
false-clean that brief-time coherence is blind to: a verify that exits clean
while nothing actually ran. Backward compatible — a criterion with no kind keeps
plain exit-code semantics.
"""

from __future__ import annotations

import argparse
import shutil

import yaml

from prusik import gate, ledger, schema
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


def test_prove_red_rejects_vacuous_green_verify():
    """The frontier that survives execution-evidence: a verify that passes WITHOUT
    the change asserts nothing. prove-red rejects it — no red baseline is recorded."""
    tmp = _mktmp_project()
    try:
        _criteria(tmp, "feat", [{"id": "c1", "description": "d",
                                 "verify_command": "true", "prove_red": True}])
        rc = gate.prove_red(argparse.Namespace(feature="feat", id=None))
        assert rc == 1, "green-without-the-change is vacuous → rejected"
        assert not gate._red_baseline_exists(tmp, "feat", "c1", "true")
    finally:
        shutil.rmtree(tmp)


def test_prove_red_captures_red_baseline_for_load_bearing_verify():
    tmp = _mktmp_project()
    try:
        _criteria(tmp, "feat", [{"id": "c1", "description": "d",
                                 "verify_command": "false", "prove_red": True}])
        rc = gate.prove_red(argparse.Namespace(feature="feat", id=None))
        assert rc == 0, "a verify that FAILS without the change is load-bearing"
        assert gate._red_baseline_exists(tmp, "feat", "c1", "false")
    finally:
        shutil.rmtree(tmp)


def test_sprint_complete_requires_red_baseline_for_prove_red():
    """A prove_red criterion that's green NOW still fails without a captured RED
    baseline — green alone can't distinguish load-bearing from vacuous."""
    tmp = _mktmp_project()
    try:
        vc = "echo '3 passed in 0.1s'"
        _criteria(tmp, "feat", [{"id": "c1", "description": "d",
                                 "verify_command": vc, "kind": "tests",
                                 "prove_red": True}])
        ok, _ = gate._run_success_criteria("feat", tmp)
        assert not ok, "green now but no RED baseline → fails (could be vacuous)"
        ledger.append("criterion_red_baseline", feature="feat", id="c1",
                      verify_command=vc, exit_code=1)
        ok2, _ = gate._run_success_criteria("feat", tmp)
        assert ok2, "green now AND a matching RED baseline → passes (load-bearing)"
    finally:
        shutil.rmtree(tmp)


def test_prove_red_baseline_bound_to_verify_command():
    """A RED baseline captured for a DIFFERENT verify_command must not credit this
    one — otherwise you could prove one thing red and green-credit another."""
    tmp = _mktmp_project()
    try:
        vc = "echo '3 passed in 0.1s'"
        _criteria(tmp, "feat", [{"id": "c1", "description": "d",
                                 "verify_command": vc, "kind": "tests",
                                 "prove_red": True}])
        ledger.append("criterion_red_baseline", feature="feat", id="c1",
                      verify_command="a different command", exit_code=1)
        ok, _ = gate._run_success_criteria("feat", tmp)
        assert not ok, "red baseline for a different verify must not count"
    finally:
        shutil.rmtree(tmp)


def test_schema_rejects_non_bool_prove_red():
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir(exist_ok=True)
        p = tmp / "briefs" / "feat.criteria.yaml"
        p.write_text(yaml.safe_dump({"schema_version": "1.0", "criteria": [
            {"id": "c1", "description": "d", "verify_command": "echo hi",
             "prove_red": "yes"}]}))
        ok, errs = schema.validate_criteria_file(p, project_root=tmp)
        assert not ok and any("prove_red must be a boolean" in e for e in errs)
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
