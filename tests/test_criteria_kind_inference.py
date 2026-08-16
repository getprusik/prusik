"""fb-d34fb5d5c7a5 — criterion_evidence and criterion_prove_red were dormant fleet-wide
because kind:/prove_red: are opt-in and nobody declares them, so a local verify_command
was trusted on exit code alone. Two fixes, fb-c76 philosophy (evidence by default):

(a) infer `kind:` from a test/lint/type-shaped verify_command so execution-evidence arms
    without a hand-declaration (a bare tsc augmented so a silent-clean typecheck counts);
(b) a brief-lint advisory nudging a new_feature brief whose acceptance criteria are never
    proven RED (vacuous-green).

moat-finding: fb-d34fb5d5c7a5
"""

from __future__ import annotations

import shutil

import yaml

from prusik import brief_lint, gate
from tests._common import _mktmp_project


def _criteria(tmp, feature, entries):
    (tmp / "briefs").mkdir(exist_ok=True)
    (tmp / "briefs" / f"{feature}.md").write_text("## Goal\nx\n")
    (tmp / "briefs" / f"{feature}.criteria.yaml").write_text(
        yaml.safe_dump({"schema_version": "1.0", "criteria": entries}))


# ---- (a) kind inference: unit --------------------------------------------------

def test_infer_kind_covers_reliable_tools_and_skips_opaque_wrappers():
    ik = gate._infer_kind
    assert ik("pytest tests/") == "tests"
    assert ik("cd web && npx vitest run") == "tests"
    assert ik("cd packages/api && npx tsc --noEmit") == "types"
    assert ik("mypy src") == "types"
    assert ik("ruff check .") == "lint"
    assert ik("npx eslint src") == "lint"
    # opaque wrappers alias arbitrary scripts → NOT inferred (could false-fail a clean run)
    assert ik("npm test") is None
    assert ik("pnpm run verify") is None
    # bespoke shell checks → no evidence kind, exit-code semantics preserved
    assert ik("curl -sf localhost:3000/health") is None


# ---- (a) kind inference: end-to-end through the criteria gate -------------------

def test_inferred_tests_kind_catches_a_false_clean_without_opt_in():
    """THE ADVERSARIAL CASE: a criterion with NO kind whose pytest-shaped verify exits 0
    but ran nothing. Before: passes on exit code. Now: inference arms execution-evidence
    and it FAILS — the false-clean the layer exists to catch, with zero opt-in."""
    tmp = _mktmp_project()
    try:
        _criteria(tmp, "feat", [{
            "id": "c1", "description": "unit tests pass",
            # a pytest-shaped verify (→ inferred tests) that exits 0 having run nothing —
            # the misconfigured-verify false-clean, now caught with no opt-in. Emulate a
            # pytest that collected 0 items (no 'N passed/failed' in its output).
            "verify_command": "pytest() { echo 'collected 0 items'; }; pytest tests/"}])
        ok, results = gate._run_success_criteria("feat", tmp)
        assert not ok, "inferred tests-kind must fail a 0-executed false-clean"
        assert results[0]["executed"] == 0 and results[0]["passed"] is False
    finally:
        shutil.rmtree(tmp)


def test_inferred_kind_passes_a_real_run_and_does_not_break_bespoke():
    tmp = _mktmp_project()
    try:
        _criteria(tmp, "feat", [
            {"id": "real", "description": "tests",
             "verify_command": "echo 'pytest 4 passed in 0.2s'"},        # inferred → counts 4
            {"id": "shell", "description": "health",
             "verify_command": "echo ok"}])                              # no tool → exit-code only
        ok, results = gate._run_success_criteria("feat", tmp)
        by = {r["id"]: r for r in results}
        assert ok
        assert by["real"]["executed"] == 4 and by["real"]["passed"]
        assert by["shell"]["passed"] and by["shell"]["executed"] is None
    finally:
        shutil.rmtree(tmp)


def test_bare_tsc_is_augmented_so_a_clean_typecheck_still_counts():
    """A silent-clean tsc (fb-c76ae6da2255) would infer types then record 0 → false-fail.
    The criteria path augments a bare tsc with --extendedDiagnostics, so a clean run emits
    `Files: N` and passes. Emulate tsc: prints the count ONLY with the diagnostics flag."""
    tmp = _mktmp_project()
    try:
        _criteria(tmp, "feat", [{
            "id": "types", "description": "typecheck clean",
            "verify_command":
                "tsc() { case \"$*\" in *extendedDiagnostics*) echo 'Files: 42';; esac; }; "
                "tsc --noEmit"}])
        ok, results = gate._run_success_criteria("feat", tmp)
        assert ok, "augmented tsc must emit a count and pass"
        assert results[0]["executed"] == 42 and results[0]["passed"]
    finally:
        shutil.rmtree(tmp)


# ---- (b) prove_red brief-lint advisory -----------------------------------------

def _brief(tmp, name, btype, criteria):
    (tmp / "briefs").mkdir(exist_ok=True)
    bp = tmp / "briefs" / f"{name}.md"
    bp.write_text(f"## Type\n{btype}\n\n## Goal\nx\n")
    (tmp / "briefs" / f"{name}.criteria.yaml").write_text(
        yaml.safe_dump({"schema_version": "1.0", "criteria": criteria}))
    return bp


def test_prove_red_warns_new_feature_without_red_proof():
    tmp = _mktmp_project()
    try:
        bp = _brief(tmp, "feat", "new_feature", [
            {"id": "c1", "description": "new endpoint returns 200",
             "verify_command": "pytest tests/test_new.py"}])
        w = brief_lint._prove_red_warning(bp.read_text(), tmp / "briefs" / "feat.criteria.yaml")
        assert w and "prove_red" in w and "vacuous-green" in w
    finally:
        shutil.rmtree(tmp)


def test_prove_red_silent_when_discipline_present_or_not_new_feature():
    tmp = _mktmp_project()
    try:
        cp = tmp / "briefs" / "feat.criteria.yaml"
        # already red-proven → no warning
        bp = _brief(tmp, "feat", "new_feature", [
            {"id": "c1", "description": "d", "verify_command": "pytest x", "prove_red": True}])
        assert brief_lint._prove_red_warning(bp.read_text(), cp) is None
        # not a new_feature → no warning (regression/bugfix criteria need no red proof)
        bp2 = _brief(tmp, "fix", "bug_fix", [
            {"id": "c1", "description": "d", "verify_command": "pytest x"}])
        assert brief_lint._prove_red_warning(
            bp2.read_text(), tmp / "briefs" / "fix.criteria.yaml") is None
        # only CI-verified criteria → exempt (a green required-CI check is its own evidence)
        bp3 = _brief(tmp, "ci", "new_feature", [
            {"id": "c1", "description": "d", "verify_in": "ci",
             "ci_verify_command": "gh pr checks 1 --required"}])
        assert brief_lint._prove_red_warning(
            bp3.read_text(), tmp / "briefs" / "ci.criteria.yaml") is None
    finally:
        shutil.rmtree(tmp)
