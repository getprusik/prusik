"""UI-coverage advisory — API-level e2e gives false UI-layer confidence (fb-d4c9453120cd): a feature that changes UI/markup files but has no rendered (browser)
e2e criterion is flagged, so UI-only bugs that escape every critic and the API test are
surfaced at review time. Low false-positive: a rendered e2e criterion, a .css-only
change, a non-UI change, or no criteria at all → not flagged.

moat-finding: fb-d4c9453120cd
"""

from __future__ import annotations

from pathlib import Path

from prusik import ui_coverage


def _wt_file(root: Path, rel: str) -> None:
    p = root / "worktrees" / "builder" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x\n")


def _criteria(root: Path, feature: str, body: str) -> None:
    b = root / "briefs"
    b.mkdir(parents=True, exist_ok=True)
    (b / f"{feature}.md").write_text("# feat\n")
    (b / f"{feature}.criteria.yaml").write_text(body)


_API_ONLY = ('schema_version: "1.0"\ncriteria:\n  - id: api\n'
             '    description: api works\n    verify_command: "pytest tests/api -q"\n')


def _spec(root, rel, body):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _e2e_criteria(root, feature, spec_rel):
    _criteria(root, feature, 'schema_version: "1.0"\ncriteria:\n  - id: ui\n'
              f'    description: ui\n    verify_command: "playwright test {spec_rel}"\n')


def test_ui_change_without_rendered_e2e_is_flagged(tmp_path):
    _criteria(tmp_path, "feat", _API_ONLY)
    _wt_file(tmp_path, "packages/web/src/DetailView.tsx")
    rep = ui_coverage.ui_coverage_check("feat", tmp_path)
    assert rep["flagged"] is True
    assert "packages/web/src/DetailView.tsx" in rep["ui_files"]


def test_navigating_e2e_clears_it(tmp_path):
    _e2e_criteria(tmp_path, "feat", "e2e/detail.spec.ts")
    _spec(tmp_path, "e2e/detail.spec.ts",
          "test('x', async ({page}) => { await page.goto('/detail'); })\n")
    _wt_file(tmp_path, "packages/web/src/DetailView.tsx")
    rep = ui_coverage.ui_coverage_check("feat", tmp_path)
    assert rep["has_rendered_e2e"] is True and rep["flagged"] is False


def test_api_level_playwright_is_still_flagged(tmp_path):
    """THE field escape: a Playwright e2e that only does page.request is API-level and
    never renders, so a role-gated UI stays unverified — must be flagged even though
    the runner is 'playwright'."""
    _e2e_criteria(tmp_path, "feat", "e2e/admin-api.spec.ts")
    _spec(tmp_path, "e2e/admin-api.spec.ts",
          "test('x', async ({page}) => { const r = await page.request.get('/api/admin'); })\n")
    _wt_file(tmp_path, "packages/web/src/AdminPanel.tsx")
    assert ui_coverage.ui_coverage_check("feat", tmp_path)["flagged"] is True


def test_runner_with_unreadable_spec_is_lenient(tmp_path):
    # a browser runner whose spec we can't inspect → benefit of the doubt (no false flag)
    _e2e_criteria(tmp_path, "feat", "e2e/missing.spec.ts")
    _wt_file(tmp_path, "src/Foo.tsx")
    assert ui_coverage.ui_coverage_check("feat", tmp_path)["flagged"] is False


def test_browser_smoke_marker_counts_as_covered(tmp_path):
    _criteria(tmp_path, "feat",
              'schema_version: "1.0"\ncriteria:\n  - id: ui\n'
              '    description: renders\n'
              '    verify_command: "pytest tests/behavior -m browser_smoke"\n')
    _wt_file(tmp_path, "src/Card.vue")
    assert ui_coverage.ui_coverage_check("feat", tmp_path)["flagged"] is False


def test_non_ui_change_not_flagged(tmp_path):
    _criteria(tmp_path, "feat", _API_ONLY)
    _wt_file(tmp_path, "packages/api/src/service.py")
    assert ui_coverage.ui_coverage_check("feat", tmp_path)["flagged"] is False


def test_css_only_change_not_flagged(tmp_path):
    _criteria(tmp_path, "feat", _API_ONLY)
    _wt_file(tmp_path, "packages/web/src/styles.css")
    assert ui_coverage.ui_coverage_check("feat", tmp_path)["flagged"] is False


def test_no_criteria_file_not_flagged(tmp_path):
    # UI changed but no acceptance criteria at all → a different concern, not this one
    _wt_file(tmp_path, "src/Foo.tsx")
    assert ui_coverage.ui_coverage_check("feat", tmp_path)["flagged"] is False


def test_template_under_views_is_ui():
    assert ui_coverage._is_ui_file("app/views/invoices/show.html.erb") is True
    assert ui_coverage._is_ui_file("docs/readme.html") is False     # not a view/template dir


def test_ui_extensions_recognised():
    for ext in ("a.tsx", "b.jsx", "c.vue", "d.svelte", "e.astro"):
        assert ui_coverage._is_ui_file(f"src/{ext}") is True
    assert ui_coverage._is_ui_file("src/util.ts") is False
