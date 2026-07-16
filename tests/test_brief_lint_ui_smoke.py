"""fb-ec9bdfc54ad3 — brief-lint shifts the browser-smoke check LEFT for UI briefs.

A brief that describes a UI surface but whose success criteria carry no browser-driven
verify command used to be FAILed only by the LLM brief-critic — a full round-trip for a
mechanically-detectable condition. brief-lint now flags it deterministically, instantly.

Adversarial cases matter here: a `-m 'not browser_smoke'` SKIP must NOT count as a
browser run (else the warning would be suppressed by an exclusion — the opposite of
its purpose), and a non-UI brief must NOT warn (no false positive that trains authors
to ignore it).

moat-finding: fb-ec9bdfc54ad3
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from prusik import brief_lint as bl

_UI_BRIEF = "## Goal\nRender the dashboard template (base.html) with HTMX and Alpine."
_NONUI_BRIEF = "## Goal\nRefactor the invoice calculator and add unit tests."


def _criteria(tmp_path: Path, verify_command: str) -> Path:
    p = tmp_path / "f.criteria.yaml"
    p.write_text(textwrap.dedent(f"""
        schema_version: "1.0"
        criteria:
          - id: c1
            description: it works
            verify_command: "{verify_command}"
    """))
    return p


def test_ui_brief_without_browser_smoke_warns(tmp_path):
    cp = _criteria(tmp_path, "pytest tests/unit")
    assert bl._ui_smoke_warning(_UI_BRIEF, cp, tmp_path) is not None


def test_ui_brief_with_browser_smoke_criterion_is_silent(tmp_path):
    cp = _criteria(tmp_path, "pytest -m browser_smoke")
    assert bl._ui_smoke_warning(_UI_BRIEF, cp, tmp_path) is None


def test_ui_brief_with_browser_tool_is_silent(tmp_path):
    cp = _criteria(tmp_path, "playwright test e2e/")
    assert bl._ui_smoke_warning(_UI_BRIEF, cp, tmp_path) is None


def test_not_browser_smoke_skip_still_warns(tmp_path):
    # the adversarial case: EXCLUDING the browser marker is not a browser run.
    cp = _criteria(tmp_path, "pytest -m 'not browser_smoke'")
    assert bl._ui_smoke_warning(_UI_BRIEF, cp, tmp_path) is not None


def test_non_ui_brief_never_warns(tmp_path):
    cp = _criteria(tmp_path, "pytest tests/unit")
    assert bl._ui_smoke_warning(_NONUI_BRIEF, cp, tmp_path) is None


def test_ui_brief_with_no_criteria_file_warns(tmp_path):
    # a UI brief with no criteria file at all certainly lacks a browser smoke.
    assert bl._ui_smoke_warning(_UI_BRIEF, tmp_path / "missing.criteria.yaml",
                                tmp_path) is not None


def test_warning_is_advisory_not_a_lint_failure(tmp_path, capsys):
    # non-blocking: the ui-smoke warning must not make brief-lint exit nonzero on an
    # otherwise-valid brief (it's shift-left feedback, not a gate).
    root = tmp_path
    (root / "briefs").mkdir()
    (root / "briefs" / "f.md").write_text(
        "## Type\nnew_feature\n"
        "## Goal\nRender the dashboard base.html template for the user.\n"
        "## Problem\nThe dashboard page does not render for signed-in users today.\n"
        "## Success criteria\n- the dashboard endpoint exits 0 and returns the page.\n"
        "## Modules touched\n- src/x.py\n"
        "## Verify commands\n- pytest tests/unit\n")
    rc = bl.lint("briefs/f.md", root=root)
    out = capsys.readouterr().out
    assert "[ui-smoke-warn]" in out
    assert rc == 0                       # advisory — did not fail the lint
