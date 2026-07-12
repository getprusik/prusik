"""TS/turbo evidence adapter (v0.53.0) — executed_count + prove_verdict against
an adopter's REAL captured output (prusik's first serious TS adopter; forcing-function
finding #1 + #1.5).

Where a real log is small enough it is READ from benchmarks/cases/field-ts/ and
asserted against the true count — so a fixture can never drift from reality by
transcription (the failure mode adopter caught: a guessed 523 vs the real 2614).
The two giant raw logs (full 1.3MB vitest-multi, 615KB tsc error dump) are kept
local; their shapes are represented inline."""

from __future__ import annotations

from pathlib import Path

from prusik import evidence

_CORPUS = Path(__file__).resolve().parent.parent / "benchmarks" / "cases" / "field-ts"


def _log(name: str) -> str:
    return (_CORPUS / name).read_text()


# ---------- tests: vitest, anchored on Tests line (exclude Test Files) ----------

def test_vitest_single_excludes_test_files():
    # real 01 log: "Test Files 48 passed / Tests 730 passed" → 730, not 778
    assert evidence.executed_count("tests", _log("01-vitest-single.log")) == 730


def test_vitest_multi_package_sums_only_tests_lines():
    # the 6 verbatim summary lines (full 1.3MB log verified separately → 1655).
    # 1752 = 1655 real + 97 Test Files (7+48+42) across 3 packages.
    multi = (
        "packages/shared test:    Test Files  7 passed (7)\n"
        "packages/shared test:         Tests  66 passed (66)\n"
        "packages/frontend test:  Test Files  48 passed (48)\n"
        "packages/frontend test:       Tests  730 passed (730)\n"
        "packages/backend test:   Test Files  42 passed | 2 skipped (44)\n"
        "packages/backend test:        Tests  859 passed | 71 skipped (930)\n"
    )
    assert evidence.executed_count("tests", multi) == 1655   # 66 + 730 + 859


def test_pytest_single_line_unaffected():
    assert evidence.executed_count("tests", "5 passed, 2 failed in 1.2s\n") == 7


def test_contracts_script_is_zero_under_tests_kind():
    # 09: a script banner, no tests run → 0 (kind mismatch, not a gap)
    assert evidence.executed_count("tests", _log("09-contracts-check.log")) == 0


# ---------- types/lint: real files-checked count, never a loose marker ----------

def test_tsc_extended_diagnostics_real_files_count():
    # 05 log: tsc --extendedDiagnostics emits "Files:  2614" (every loaded file,
    # incl. lib.d.ts / node_modules .d.ts — a "did it run" signal, NOT "2614
    # project files"). ≥1 is all the zero-scope guard needs.
    assert evidence.executed_count("types", _log("05-tsc-extdiag-files2614.log")) == 2614
    assert evidence.prove_verdict("types", 0, 2614)[0] is True


def test_tsc_multi_package_files_summed_not_first_match():
    # under `-r` each package emits its own "Files: N"; SUM them, consistent with
    # the tests extractor (field finding: 257+283+1940+2614 = 5094, not 257).
    multi = ("packages/config type-check:   Files:  257\n"
             "packages/shared type-check:   Files:  283\n"
             "packages/backend type-check:  Files:  1940\n"
             "packages/frontend type-check: Files:  2614\n")
    assert evidence.executed_count("types", multi) == 5094


def test_eslint_json_counts_filepaths():
    out = ('[{"filePath":"/a.ts","messages":[]},'
           '{"filePath":"/b.ts","messages":[]}]')        # real adopter: 188 (07 log)
    assert evidence.executed_count("lint", out) == 2
    assert evidence.prove_verdict("lint", 0, 2)[0] is True


# ---------- the false-clean (finding #1.5): silent runs must score 0 ----------

def test_silent_tsc_is_zero_scope_even_with_clean_in_text():
    # 04 log is a silent tsc run; the captured text even contains prove's own
    # message ("a clean run that checked NOTHING"). The OLD loose markers
    # ("clean"/"Checked ") scored that as 1 → false PROVEN. Must be 0.
    assert evidence.executed_count("types", _log("04-tsc-silent.log")) == 0
    proven, reason = evidence.prove_verdict("types", 0, 0)
    assert proven is False
    assert "extendedDiagnostics" in reason


def test_silent_eslint_is_zero_scope():
    assert evidence.executed_count("lint", _log("06-eslint-silent.log")) == 0
    assert evidence.prove_verdict("lint", 0, 0)[0] is False


def test_loose_clean_words_never_score():
    # the bug in isolation: incidental "clean"/"checked"/"Found"/"problems"
    # must NOT be read as a scope signal.
    for noise in ("everything looks clean", "Checked the config", "Found it",
                  "0 problems here", "all clean and tidy"):
        assert evidence.executed_count("types", noise) == 0, noise
        assert evidence.executed_count("lint", noise) == 0, noise


def test_mypy_source_files_still_counted():
    # regression: the legit mypy count signal survives the marker removal
    out = "Success: no issues found in 42 source files\n"
    assert evidence.executed_count("types", out) == 42


# ---------- turbo cache: banner is never evidence ----------

def test_turbo_cache_replay_is_not_evidence():
    # 08 log: cache hit → tool never ran; prove sees only turbo's banner.
    assert evidence.executed_count("lint", _log("08-turbo-lint-cache.log")) == 0
    assert evidence.prove_verdict("lint", 0, 0)[0] is False


# ---------- real errors: exit≠0 is never proven ----------

def test_real_errors_exit_nonzero_not_proven():
    assert evidence.prove_verdict("types", 1, 0)[0] is False
