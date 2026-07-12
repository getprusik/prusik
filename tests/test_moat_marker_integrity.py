"""CHANGELOG↔marker drift guard: when a release note CLAIMS `moat-finding: <id>`,
a scannable marker for that id MUST exist in a real test/benchmark — otherwise the
moat-coverage metric silently under-reports (it scans test markers, not CHANGELOG
prose). This exact drift bit fb-32b3a89cc1d5: its CHANGELOG entry cited
`moat-finding: fb-32b3a89cc1d5`, but the id sat only in a test docstring, never in
the scannable `moat-finding:` form — so the metric read the defect as uncovered
despite a genuine regression test (corrected in v0.169.0).

The check is one-directional by design: every CHANGELOG `moat-finding:` claim must
be backed by a test marker (a claim is a promise). The reverse is allowed — a test
may carry a marker for a finding closed via `Closes fb-…` alone, with no inline
`moat-finding:` line in the changelog.

This dogfoods the moat discipline the same way test_injection self-verifies the
gates: the close-out standard ("a finding is closed by `Closes fb-…` + a
`moat-finding:` regression test") can no longer drift unnoticed.

moat-finding: fb-32b3a89cc1d5
"""

from __future__ import annotations

import re
from pathlib import Path

# The SAME marker syntax the moat-coverage metric scans for
# (hq/feedback_spine.py::_MOAT_MARKER) — kept identical so this guard's notion of
# "scannable" matches exactly what the metric credits.
_MARKER = re.compile(r"moat-finding:\s*([A-Za-z0-9-]+)")

_REPO = Path(__file__).resolve().parents[1]
# The metric scans these roots; the guard must agree with it.
_SCAN_ROOTS = ("tests", "benchmarks/cases")
_SCAN_SUFFIXES = (".py", ".md", ".txt")


def _scan_test_markers(repo: Path) -> set[str]:
    """Every `moat-finding:` id present in a scannable test/benchmark file —
    mirrors hq.feedback_spine._scan_moat_markers (which is HQ-only, not importable
    from the shipped surface, so the tiny scan is reproduced here as the spec)."""
    found: set[str] = set()
    for sub in _SCAN_ROOTS:
        base = repo / sub
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if f.is_file() and f.suffix in _SCAN_SUFFIXES:
                found.update(_MARKER.findall(
                    f.read_text(encoding="utf-8", errors="ignore")))
    return found


def _changelog_marker_ids(changelog_text: str) -> set[str]:
    return set(_MARKER.findall(changelog_text))


def _cited_but_unmarked(changelog_text: str, test_markers: set[str]) -> list[str]:
    """The drift set: ids a CHANGELOG `moat-finding:` claim names that no scannable
    test marker backs. Empty == honest."""
    return sorted(_changelog_marker_ids(changelog_text) - test_markers)


def test_no_changelog_marker_drift():
    """Every `moat-finding: <id>` claimed in CHANGELOG.md is backed by a real,
    scannable marker in tests/ or benchmarks/cases/."""
    changelog = (_REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    drift = _cited_but_unmarked(changelog, _scan_test_markers(_REPO))
    assert not drift, (
        "CHANGELOG cites `moat-finding:` for ids with NO scannable test marker "
        f"(the metric will under-report them as uncovered): {drift}. "
        "Add the marker to the test that guards each finding, or correct the "
        "CHANGELOG if the claim was premature.")


def test_guard_catches_planted_drift():
    """ADVERSARIAL: the guard must FAIL on a CHANGELOG that claims a marker no test
    carries — otherwise it would pass vacuously and never catch the very drift it
    exists to prevent."""
    fake_changelog = "Fixed it. `moat-finding: fb-deadbeef00` 999 passed."
    # a marker set WITHOUT the planted id
    assert _cited_but_unmarked(fake_changelog, {"fb-aaaa11112222"}) == [
        "fb-deadbeef00"]
    # and PASSES once the backing marker exists
    assert _cited_but_unmarked(fake_changelog, {"fb-deadbeef00"}) == []


def test_scan_finds_this_files_own_marker():
    """Sanity: the scanner actually reads this directory — this file's own
    `moat-finding: fb-32b3a89cc1d5` marker is discoverable."""
    assert "fb-32b3a89cc1d5" in _scan_test_markers(_REPO)
