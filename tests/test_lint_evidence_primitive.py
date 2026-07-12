"""The lint execution-evidence primitive must not read as a clean ZERO on a FAILING
run, and a scoped pytest that exits nonzero PURELY from a coverage threshold must not
be misdiagnosed as a test failure.

fb-6a573cfe59fb:
  - `ruff check src tests` exit_code=1 was captured with nonempty_primitive value=0 —
    an errored phase whose primitive looked like a clean "0 problems". The primitive now
    counts the errors-found ("Found N errors") as work-observed → >0 on a nonzero exit.
  - a behavior-only SCOPED pytest exits 1 from the package-wide `--cov-fail-under` gate
    with ZERO test failures; that is a coverage shortfall, not a test failure.

moat-finding: fb-6a573cfe59fb
"""

from __future__ import annotations

from prusik import evidence


# ── part 1: a failing lint run is work-observed (>0), never a clean-looking 0 ──

def test_failing_ruff_run_counts_errors_not_zero():
    out = (
        "src/a.py:1:1: F401 [*] `os` imported but unused\n"
        "src/b.py:3:5: F841 Local variable `x` is assigned to but never used\n"
        "Found 5 errors.\n"
        "[*] 2 fixable with the `--fix` option.\n"
    )
    # >0 → the primitive no longer masquerades as a clean zero on exit_code=1
    assert evidence.executed_count("lint", out, "ruff check src tests") == 5


def test_failing_mypy_run_counts_errors():
    out = "x.py:1: error: bad\nFound 3 errors in 2 files (checked 7 source files)\n"
    # 7 source files + 3 errors found = 10 (both are real work-observed; magnitude is
    # advisory — the point is it is >0 on a failing run)
    assert evidence.executed_count("types", out, "mypy src") == 10


def test_failing_eslint_run_counts_problems():
    out = "/p/a.js\n  1:1  error  Unexpected\n\n✖ 4 problems (4 errors, 0 warnings)\n"
    assert evidence.executed_count("lint", out, "eslint src") == 4


def test_clean_ruff_run_is_unchanged():
    # no "Found N errors" line → error-count adds 0; clean-credit still applies
    assert evidence.executed_count("lint", "All checks passed!\n", "ruff check src") == 1


def test_clean_ruff_does_not_false_count_on_word_error():
    # a passing run mentioning the word "error" in a path/comment must not be counted
    out = "All checks passed!\n"
    assert evidence.executed_count("lint", out, "ruff check src/error_handling") == 1


# ── part 2: a coverage-gate-only exit is not a test failure ──

_GREEN_WITH_COV_FAIL = (
    "tests/test_routes.py ....                                          [100%]\n\n"
    "---------- coverage: ----------\n"
    "FAIL Required test coverage of 85% not reached. Total coverage: 42.00%\n"
    "===================== 4 passed in 0.12s =====================\n"
)


def test_coverage_only_exit_is_detected():
    assert evidence.coverage_gate_only_exit(_GREEN_WITH_COV_FAIL, 1) is True


def test_real_test_failure_with_cov_marker_is_not_coverage_only():
    # ADVERSARIAL: a genuine failure must NEVER be laundered as a coverage shortfall.
    out = _GREEN_WITH_COV_FAIL.replace("4 passed", "3 passed, 1 failed")
    assert evidence.coverage_gate_only_exit(out, 1) is False


def test_clean_exit_is_not_coverage_only():
    assert evidence.coverage_gate_only_exit(_GREEN_WITH_COV_FAIL, 0) is False


def test_nothing_ran_is_not_coverage_only():
    # exit nonzero, cov marker, but ZERO tests executed → not a green subset
    out = "no tests ran\nFAIL Required test coverage of 85% not reached.\n"
    assert evidence.coverage_gate_only_exit(out, 1) is False


def test_collection_error_with_cov_marker_is_not_coverage_only():
    out = ("errors during collection\n2 passed\n"
           "FAIL Required test coverage of 85% not reached.\n")
    assert evidence.coverage_gate_only_exit(out, 1) is False


def test_plain_failure_without_cov_marker_is_not_coverage_only():
    out = "===================== 3 passed, 1 failed in 0.1s =====================\n"
    assert evidence.coverage_gate_only_exit(out, 1) is False
