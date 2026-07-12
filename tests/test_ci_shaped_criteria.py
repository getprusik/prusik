"""CI-shaped success criteria (fb-c80cb5c55771): a criterion whose real verify
can only run in CI (browser-e2e needing a live HTTPS stack + browsers) closes on a
GREEN CI CHECK, not a faked local run. `verify_in: ci` selects a `ci_verify_command`
(a status check that exits 0 only when the required CI run is green). Fail-closed:
a red or missing check still FAILS — never a skip.

moat-finding: fb-c80cb5c55771
"""

from __future__ import annotations

import os

from prusik import gate, schema


def _project(tmp_path, criteria_yaml):
    os.environ["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    (tmp_path / ".sprint").mkdir()
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "feat.md").write_text("# feat\n")
    cp = schema.criteria_path_for_brief(tmp_path / "briefs" / "feat.md")
    cp.write_text(criteria_yaml)
    return tmp_path


def test_ci_green_check_closes_the_criterion(tmp_path):
    _project(tmp_path, 'criteria:\n  - id: e2e\n    verify_in: ci\n'
             '    ci_verify_command: "exit 0"\n    verify_command: "playwright test"\n'
             '    expected_exit: 0\n')
    ok, results = gate._run_success_criteria("feat", tmp_path)
    assert ok and results[0]["passed"] is True       # green CI check → met (real evidence)


def test_ci_red_check_fails_closed(tmp_path):
    """A red CI check must NOT close the sprint — and the local browser command is
    never run (it would false-fail on the dev host)."""
    _project(tmp_path, 'criteria:\n  - id: e2e\n    verify_in: ci\n'
             '    ci_verify_command: "exit 1"\n    verify_command: "playwright test"\n')
    ok, results = gate._run_success_criteria("feat", tmp_path)
    assert not ok and results[0]["passed"] is False


def test_ci_shaped_without_command_fails_with_guidance(tmp_path):
    """No ci_verify_command → can't prove CI is green → FAIL (never a silent skip)."""
    _project(tmp_path, 'criteria:\n  - id: e2e\n    verify_in: ci\n'
             '    verify_command: "playwright test"\n')
    ok, results = gate._run_success_criteria("feat", tmp_path)
    assert not ok and results[0]["passed"] is False
    out = (tmp_path / results[0]["output_path"]).read_text()
    assert "ci_verify_command missing" in out and "real CI evidence" in out


def test_normal_local_criterion_unaffected(tmp_path):
    _project(tmp_path, 'criteria:\n  - id: backend\n    verify_command: "exit 0"\n'
             '    expected_exit: 0\n')
    ok, results = gate._run_success_criteria("feat", tmp_path)
    assert ok and results[0]["passed"] is True
