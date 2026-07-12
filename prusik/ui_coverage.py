"""UI-coverage advisory — API-level e2e gives false UI-layer confidence.

fb-d4c9453120cd: three real chunk-7 bugs escaped EVERY critic AND an API-level
e2e, caught only by a RENDERED (browser) e2e — because critics review the diff and an
API e2e never renders the page, so a bug that only appears on render is invisible to
both. When a feature changes UI/markup files but its acceptance criteria carry no
rendered (browser) e2e, the UI layer is unverified. This ADVISORY makes that gap
visible at review time (the out-of-diff recall class).

Convention-general + low false-positive — it fires ONLY when UI component/markup files
actually changed AND no criterion runs a browser e2e. Any common runner, a
browser_smoke-marked test, or a CI browser criterion counts as covered; a `.css`-only
change isn't behaviour, so it doesn't count; and it stays silent when there are no
acceptance criteria at all.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from prusik import consistency, ledger, schema

# Component/markup files whose correctness only manifests when RENDERED.
_UI_EXT = (".jsx", ".tsx", ".vue", ".svelte", ".astro")
_UI_TEMPLATE_RE = re.compile(
    r"(?:^|/)(?:templates?|views?|components?|pages?)/[^?]*"
    r"\.(?:html|erb|haml|ejs|hbs|pug)$", re.I)
# A browser e2e RUNNER (necessary, not sufficient — an adopter: a Playwright `page.request`
# e2e is runner-named but API-level and never renders).
_RUNNER_RE = re.compile(
    r"\b(?:playwright|cypress|puppeteer|selenium|wdio|testcafe|nightwatch)\b", re.I)
_BROWSER_SMOKE_RE = re.compile(r"\bbrowser_smoke\b")
# The SUFFICIENT signal (An adopter's rule): the e2e NAVIGATES/renders the page + acts —
# `page.goto` / `cy.visit` / a rendered locator — vs `page.request`, which is API-level.
_NAV_RE = re.compile(
    r"page\.goto\b|\.goto\s*\(|cy\.visit\b|browser\.url\b|"
    r"page\.(?:getByRole|getByText|getByLabel|locator|click)\b", re.I)
_E2E_SPEC_RE = re.compile(r"(?:[\w.-]+/)*[\w.-]+\.(?:spec|e2e|test|cy)\.[jt]sx?\b")


def _is_ui_file(rel: str) -> bool:
    return rel.endswith(_UI_EXT) or bool(_UI_TEMPLATE_RE.search(rel))


def changed_ui_files(root: Path) -> list[str]:
    return sorted(f for f in consistency.sprint_changed_files(root) if _is_ui_file(f))


def _read_spec(rel: str, root: Path) -> str | None:
    """Read a spec file by its relative path — at root or under any worktree mirror."""
    bases = [root]
    wt = root / "worktrees"
    if wt.is_dir():
        bases += [d for d in wt.iterdir() if d.is_dir()]
    for base in bases:
        p = base / rel
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return None
    return None


def _command_renders(cmd: str, root: Path) -> bool:
    """A criterion's e2e RENDERS the UI if a referenced spec NAVIGATES the page (the
    field rule). A `page.request`-only spec is API-level → does NOT count. Lenient only
    when a browser runner is present but no spec was readable to inspect (benefit of the
    doubt), so we never false-flag a real rendered e2e we simply couldn't read."""
    if _BROWSER_SMOKE_RE.search(cmd):                 # convention: a real browser test
        return True
    read_any = False
    for m in _E2E_SPEC_RE.finditer(cmd):
        txt = _read_spec(m.group(0), root)
        if txt is not None:
            read_any = True
            if _NAV_RE.search(txt):
                return True                            # demonstrably navigates → rendered
    # readable spec(s) but none navigated → API-level only → NOT covered (the escape)
    if read_any:
        return False
    return bool(_RUNNER_RE.search(cmd))               # runner, nothing to inspect → lenient


def has_rendered_e2e(feature: str, root: Path) -> bool:
    """True if any acceptance criterion runs a RENDERED (navigating) browser e2e —
    not merely a runner-named API-level one (fb-d4c9453120cd)."""
    cpath = schema.criteria_path_for_brief(root / "briefs" / f"{feature}.md")
    if not cpath.exists():
        return False
    for c in schema.load_criteria(cpath):
        for key in ("verify_command", "ci_verify_command"):
            vc = c.get(key)
            if isinstance(vc, str) and _command_renders(vc, root):
                return True
    return False


def ui_coverage_check(feature: str, root: Path) -> dict[str, Any]:
    cpath = schema.criteria_path_for_brief(root / "briefs" / f"{feature}.md")
    ui = changed_ui_files(root)
    has_criteria = cpath.exists() and bool(schema.load_criteria(cpath))
    rendered = has_rendered_e2e(feature, root)
    return {
        "feature": feature,
        "ui_files": ui,
        "has_criteria": has_criteria,
        "has_rendered_e2e": rendered,
        "flagged": bool(ui) and has_criteria and not rendered,
    }


def run(feature: str, json_output: bool = False, strict: bool = False) -> int:
    """`prusik ui-e2e-check <feature>` — advisory. Flags a UI-touching feature whose
    acceptance criteria carry no rendered (browser) e2e."""
    root = ledger.project_root()
    from prusik import calibration
    strict = strict or calibration.is_promoted("ui_coverage_detector", root)
    rep = ui_coverage_check(feature, root)
    if json_output:
        print(json.dumps(rep, indent=2))
        return 1 if (strict and rep["flagged"]) else 0

    if not rep["flagged"]:
        if rep["ui_files"] and rep["has_rendered_e2e"]:
            print(f"[prusik-ui] '{feature}': UI files changed and a rendered e2e "
                  f"criterion covers them.")
        else:
            print(f"[prusik-ui] '{feature}': no UI-layer coverage gap.")
        return 0

    ledger.append("ui_e2e_flagged", feature=feature, ui_files=len(rep["ui_files"]))
    print(f"[prusik-ui] '{feature}': {len(rep['ui_files'])} UI/markup file(s) changed, "
          f"but NO rendered (browser) e2e criterion verifies them:")
    for f in rep["ui_files"][:15]:
        print(f"    · {f}")
    if len(rep["ui_files"]) > 15:
        print(f"    … and {len(rep['ui_files']) - 15} more (use --json for all)")
    print("\n  An API-level e2e renders nothing, so it gives false UI-layer confidence — "
          "a bug that only appears when the page renders escapes every critic and the "
          "API test alike. Add a rendered e2e criterion (playwright / cypress / …), "
          "marked browser_smoke and verified post-integration. Advisory.")
    return 1 if strict else 0
