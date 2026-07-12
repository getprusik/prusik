"""A clean eslint (0 problems) prints nothing with the default formatter, but
eslint ERRORS on a no-match/empty scope — so a clean exit-0 eslint genuinely
linted real files. It must NOT be scored as 'nothing executed / false-clean'
(which punishes the correct outcome and forced `-f json`).

moat-finding: fb-bc87bb8520dc
"""

from __future__ import annotations

from prusik.evidence import executed_count, prove_verdict


def test_clean_eslint_default_formatter_is_proven():
    """Empty output (0 problems) + exit 0 + a real eslint invocation → credited."""
    cnt = executed_count("lint", "", command="pnpm exec eslint src")
    assert cnt == 1
    ok, why = prove_verdict("lint", exit_code=0, executed=cnt)
    assert ok, why


def test_eslint_with_warnings_still_proven():
    """Warnings (not errors) exit 0 and prove files were checked."""
    out = "/app/src/x.ts\n  3:1  warning  Unexpected console  no-console\n"
    cnt = executed_count("lint", out, command="eslint src")
    assert cnt == 1 and prove_verdict("lint", 0, cnt)[0]


def test_eslint_json_formatter_counts_files_not_just_one():
    """The explicit `-f json` path still counts every file (An adopter's workaround)."""
    out = '[{"filePath":"/a.ts","messages":[]},{"filePath":"/b.ts","messages":[]}]'
    assert executed_count("lint", out, command="eslint -f json src") == 2


def test_eslint_empty_scope_is_not_credited():
    """An explicit empty/all-ignored scope must NOT pass (it checked nothing)."""
    out = "You are linting '.', but all files matched are ignored.\n"
    assert executed_count("lint", out, command="eslint .") == 0
    assert not prove_verdict("lint", 0, 0)[0]


def test_degenerate_eslint_calls_not_credited():
    """`eslint --version` / `--help` exit 0 but lint nothing → never credited."""
    assert executed_count("lint", "v9.0.0\n", command="eslint --version") == 0
    assert executed_count("lint", "Usage: eslint ...\n", command="eslint --help") == 0


def test_echo_gaming_still_blocked():
    """A bare success string with no real linter in the command is not proof."""
    assert executed_count("lint", "All checks passed!", command="echo 'All checks passed!'") == 0


def test_ruff_clean_still_credited():
    """The original ruff path is unchanged."""
    assert executed_count("lint", "All checks passed!", command="ruff check .") == 1
    assert executed_count("lint", "No Python files found", command="ruff check .") == 0


def test_tsc_silent_clean_still_needs_a_count():
    """tsc can exit 0 silently on an empty glob → still requires a real count."""
    assert executed_count("types", "", command="tsc --noEmit") == 0
    assert not prove_verdict("types", 0, 0)[0]
