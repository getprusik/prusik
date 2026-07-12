"""Execution-evidence primitive — the anti-fabrication core.

The load-bearing idea behind prusik: an agent claiming "tests pass" proves
nothing; what matters is whether the test command ACTUALLY RAN, derived from
the tool's own output, not from anything the agent could type.

`executed_count` is that primitive. It is shared by:
  - `prusik gate capture` (the sprint-FSM reviewer evidence path), and
  - `prusik prove` (the standalone, no-FSM "prove it ran" command).

Keeping a single implementation means the ungameable count can't drift
between the two surfaces.
"""

from __future__ import annotations

import re


def executed_count(kind: str, text: str, command: str = "") -> int:
    """Derive the 'real work happened' count from the tool's OWN output —
    never from anything the agent could type. `command` (the verbatim command
    prusik RAN) is used only to attribute a count-less clean signal to the real
    tool that produced it (see _clean_lint_over_real_scope); it never invents
    scope on its own.

    kind=tests  → pytest EXECUTED count = passed + failed (NOT collected,
                  NOT skipped). An all-skip / auto-skipped / nothing-ran
                  run yields 0. This is the strong primitive; it mechanically
                  closes the auto-skip / no-collection false-clean.
    kind=lint/types → the analog of executed-tests is FILES CHECKED (a clean
                  run that checked nothing also exits 0). Real scope count from
                  mypy 'N source files', tsc '--extendedDiagnostics → Files: N',
                  or eslint '-f json' filePath count; else a clean run of a linter
                  LOUD on empty scope (ruff/eslint) ⇒ 1; silent-clean tsc ⇒ 0.
                  Stated, not overclaimed.
    Unparseable ⇒ 0 (unproven ⇒ blocked — safe-over-unsafe, on-thesis)."""
    if kind == "tests":
        # Sum executed tests ("N passed"/"N failed") from the tool's own
        # summary — pytest ("5 passed, 2 failed") and vitest ("Tests 730
        # passed"). EXCLUDE vitest's "Test Files N passed" line, which counts
        # FILES as tests (verified on an adopter: a 3-package `pnpm -r test` reported
        # 1752 = 1655 real + 97 file-lines). Line-by-line so the `pnpm -r`
        # prefix ("packages/x test:") and concurrent interleaving don't matter;
        # "skipped" never matches passed|failed, so "| 71 skipped" is correctly
        # excluded from the executed count.
        total = 0
        for line in text.splitlines():
            if "test files" in line.lower():
                continue
            for n, _w in re.findall(r"(\d+)\s+(passed|failed)\b", line):
                total += int(n)
        return total
    if kind in ("lint", "types"):
        # The ungameable analog of "tests executed" is FILES CHECKED: a clean
        # run that checked NOTHING (wrong include/glob) also exits 0 and silent
        # (true for tsc + eslint). Prefer a real scope COUNT from the tool's own
        # output before any binary completion marker.
        # Require a real scope COUNT — never a loose "looks clean" marker (the
        # old markers false-PROVED on any incidental text, e.g. a captured
        # "All checks passed"). SUM across the run, consistent with the tests
        # extractor: under `-r` each package emits its own count, so reporting
        # only the first (re.search) under-counts a monorepo (An adopter: four tsc
        # `Files:` lines 257+283+1940+2614 = 5094, not 257). The magnitude is a
        # "real work observed" signal — tsc `Files:` counts every loaded file
        # incl. shared lib.d.ts, NOT unique project files — and ≥1 is all the
        # zero-scope guard needs.
        total = 0
        for pat in (r"(\d+)\s+source files?\b",   # mypy ("… in N source files")
                    r"\bFiles:\s+(\d+)",          # tsc --extendedDiagnostics
                    r"\bChecked\s+(\d+)\s+files?\b"):  # ruff -v ("Checked N files in:")
            total += sum(int(n) for n in re.findall(pat, text))
        total += text.count('"filePath"')          # eslint -f json: one per file
        # A FAILING lint run (ruff/mypy "Found N errors", eslint "N problems") did real
        # work — it found N problems. Count that as work-observed so the primitive is
        # >0 on a nonzero exit, never a 0 that reads like a clean "0 problems"
        # (fb-6a573cfe59fb: ruff exit_code=1 captured as value=0, an errored phase
        # that looked clean). The exit-gate still blocks the failure; this only stops
        # the primitive from masquerading as a clean zero. Absent on a clean run (ruff
        # prints "All checks passed!", not "Found N errors"), so the clean path below is
        # untouched.
        total += sum(int(n) for n in re.findall(r"\bFound\s+(\d+)\s+errors?\b", text))
        total += sum(int(n) for n in re.findall(r"(\d+)\s+problems?\s*\(", text))  # eslint
        if total == 0 and _clean_lint_over_real_scope(command, text):
            return 1                                # field bridge #2 / fb-bc87bb8520dc
        return total
    return 0


def coverage_gate_only_exit(text: str, exit_code: int) -> bool:
    """True when a pytest run exited NONZERO PURELY because a coverage threshold
    (`--cov-fail-under`) was not met — every test PASSED, nothing failed or errored
    (fb-6a573cfe59fb). A behavior-only SCOPED run (a deliberate subset) trips a
    PACKAGE-WIDE coverage gate that is structurally meaningless on a subset, and the run
    exits 1 with zero test failures. That is a coverage shortfall, NOT a test failure —
    it must not be diagnosed as 'errored phase scored clean'. STRICT so it can never
    launder a real failure: requires a nonzero exit, ZERO observed failures, NO
    collection error, at least one test actually executed, AND an explicit coverage-gate
    marker in the tool's own output."""
    if exit_code == 0:
        return False
    if failed_count(text) > 0:
        return False                                # a real test failure — not coverage
    low = text.lower()
    if "errors during collection" in low or "error collecting" in low:
        return False                                # collection broke — not coverage
    if executed_count("tests", text) < 1:
        return False                                # nothing ran — not a green subset
    return bool(re.search(
        r"required test coverage of|coverage failure:|fail[_-]under|"
        r"fail required test coverage", low))


def _clean_lint_over_real_scope(command: str, text: str) -> bool:
    """A clean exit-0 run of a linter that is LOUD on an EMPTY scope reliably
    checked ≥1 file even though it printed no count — real success, not a
    false-empty. Two such linters, by DIFFERENT tells:

      ruff   — prints a terminal "All checks passed!" and warns on empty scope
               ("No Python files found"); credit when that success line is present
               (field bridge #2).
      eslint — prints NOTHING when clean (default 'stylish' formatter), but ERRORS
               (non-zero, already rejected by prove_verdict) on a no-match / empty
               scope. So a clean exit-0 eslint with no empty-scope warning DID lint
               real files — 0 problems is the GOAL, not an empty run. (fb-bc87bb8520dc: a green `eslint src` was wrongly scored false-clean,
               forcing `-f json`.) UNLIKE tsc, which can exit 0 silently on an empty
               glob and so still needs a real count.

    The discriminator is the COMMAND, not the marker text: a bare "All checks
    passed!" is indistinguishable from `echo 'All checks passed!'` by output alone
    (the v0.53.0 gaming case), so credit only when the run genuinely INVOKED the
    linter — and never a degenerate, non-linting call (--version/--help/etc.). Both
    prove and capture run the command and record it verbatim, so this is the tool's
    own signal."""
    cmd = (command or "").lower()
    if re.search(r"--version|--help|--print-config|--env-info|--init", cmd):
        return False                                # not a linting run
    low = text.lower()
    if re.search(r"\bruff\b", cmd):
        if "no python files found" in low or "no files found" in low:
            return False                            # empty scope
        return "all checks passed" in low
    if re.search(r"\beslint\b", cmd):
        # eslint errors on a no-match scope (rejected upstream by the exit gate);
        # a clean exit-0 with no EXPLICIT empty-scope warning means it linted real
        # files (empty output IS the clean signal for the default formatter).
        return not ("no files matching" in low or "all files matched" in low
                    or "are ignored" in low or "no files found" in low)
    return False


def failed_count(text: str) -> int:
    """Observed FAILED tests from the tool's own summary ("N failed") — the analog
    of executed_count for the failure side, used to bound a declared known-failure
    baseline (a capture can be tolerated only when observed failures don't EXCEED
    what's git-stash-proven pre-existing). Same line-wise, test-files-excluded
    parsing as executed_count so a `pnpm -r` prefix / interleaving don't matter."""
    total = 0
    for line in text.splitlines():
        if "test files" in line.lower():
            continue
        total += sum(int(n) for n in re.findall(r"(\d+)\s+failed\b", line))
    return total


# Skipped-test NAMES (not just a count) so the unexamined-delta detector can surface
# WHICH tests changed state — provenance, not a bare threshold (field finding #4). Best-effort
# across tools; named coverage may be partial when the suite ran terse.
_SKIP_PYTEST = (
    re.compile(r"SKIPPED\s*(?:\[\d+\])?\s+(\S+\.py(?::\d+)?)"),   # SKIPPED [1] x.py:42:
    re.compile(r"(\S+\.py::\S+)\s+SKIPPED"),                       # x.py::t SKIPPED
)
# vitest marks a skip with ↓ / ↓ … (skipped); require a test-file or a `a > b` path
_SKIP_VITEST = re.compile(
    r"[↓»]\s+(\S.*?(?:\.(?:test|spec)\.[jt]sx?\b.*?|\s>\s.+?))\s*(?:\(skipped\))?\s*$",
    re.M)


def skipped_tests(text: str) -> list[str]:
    """Named skipped tests from pytest/vitest output, de-duped, in first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for pat in _SKIP_PYTEST:
        for m in pat.finditer(text):
            t = m.group(1).strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    for m in _SKIP_VITEST.finditer(text):
        t = m.group(1).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def prove_verdict(kind: str, exit_code: int, executed: int,
                  min_executed: int = 1) -> tuple[bool, str]:
    """Decide whether a run is PROVEN (really executed + clean) and explain
    why in one human line. Proven requires BOTH exit 0 AND real work — exit 0
    with zero executed tests is the canonical false-clean and is NOT proven."""
    if exit_code != 0:
        return False, f"command exited {exit_code} (non-zero) — not clean"
    if kind == "tests":
        if executed < min_executed:
            return False, (
                f"exit 0 but only {executed} test(s) executed "
                f"(need ≥{min_executed}) — nothing actually ran "
                f"(auto-skip / no collection / wrong path). Exit 0 with no "
                f"executed tests is a false-clean.")
        return True, f"{executed} test(s) executed, exit 0"
    # lint / types: the scope signal is FILES CHECKED, not executed tests.
    if executed < 1:
        return False, (
            f"exit 0 but no files-checked signal in the {kind} output — a clean "
            f"run that checked NOTHING (wrong include/glob) also exits 0 and "
            f"silent, so this can't be proven. Re-run with a scope signal that "
            f"the tool emits itself: mypy (prints 'N source files'), "
            f"tsc --extendedDiagnostics, or any linter LOUD on an empty scope — "
            f"a clean ruff ('All checks passed!') or eslint (errors on no-match, "
            f"so a clean exit-0 counts) is credited as-is; tsc needs the count.")
    return True, f"{kind} check completed, exit 0 ({executed} checked)"
