"""fb-d4d401114c19 — the product-fit gate rejected a well-formed product-fit.md across
~4 sprint-start iterations: (1) markdown emphasis inside an ## Advances bullet was
parsed as a phantom bullet/citation, and (2) ## Related slugs weren't extracted from
prose-rich, markdown-wrapped bullets. Same brittleness CLASS the scope/brief parsers
already fixed (fb-6a4075fb15fe et al.) but on a NEW artifact — product_fit._bullets was
a naive reimplementation that never routed through schema.extract_list_items (the one
extractor that requires a space after a `*` marker, so emphasis isn't a bullet).

moat-finding: fb-d4d401114c19
"""

from __future__ import annotations

import shutil

from prusik import product_fit as pf, schema
from tests._common import _mktmp_project


def _charter(tmp, pillars="- P5 — CI credit is observed-green\n- P1 — proof not opinion"):
    (tmp / "design").mkdir(parents=True, exist_ok=True)
    (tmp / "design" / "product.md").write_text(
        f"## North star\nShip receipts.\n\n## Pillars\n{pillars}\n\n## Glossary\n- receipt\n")


def _fit(tmp, feature, advances, related="- none", concepts="- receipt [canonical]"):
    d = tmp / "design" / feature
    d.mkdir(parents=True, exist_ok=True)
    (d / "product-fit.md").write_text(
        f"## Advances\n{advances}\n\n## Related\n{related}\n\n## Concepts\n{concepts}\n")


def test_bullets_delegates_to_the_shared_extractor():
    # the fix: one source of truth — a leading emphasis span is prose, not a bullet
    assert pf._bullets is not None
    body = "- P5 — real bullet\n*emphasis* opening a wrapped line\n- P1 — another"
    assert pf._bullets(body) == schema.extract_list_items(body)
    # the phantom-bullet the naive parser produced ('emphasis* opening…') is gone
    assert not any(b.startswith("emphasis") for b in pf._bullets(body))


def test_advances_bullet_with_inline_emphasis_passes():
    """field mode 1: 'P5 — … *wiring* and only *ran* post-merge' must be ONE bullet
    that resolves to pillar P5 — not fragmented at the emphasis marks into a
    non-pillar citation."""
    tmp = _mktmp_project()
    try:
        _charter(tmp)
        _fit(tmp, "feat", advances="- P5 — credited by CI *wiring* and only *ran* post-merge")
        ok, errs = pf.check("feat", root=tmp)
        assert ok, f"emphasis in Advances prose must not fail form: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_related_slug_extracted_from_prose_and_markdown():
    """field mode 2: a Related bullet with markdown + prose after a delimiter must
    resolve to the bare slug (internal hyphens preserved), not feed the whole bullet
    into the brief path."""
    tmp = _mktmp_project()
    try:
        _charter(tmp)
        (tmp / "briefs").mkdir(exist_ok=True)
        (tmp / "briefs" / "beta-ready-rebase.md").write_text("## Goal\nx\n")
        _fit(tmp, "feat", advances="- P5 — advances CI credit",
             related="- **beta-ready-rebase** — the direct source. Its post-mortem seeded this.")
        ok, errs = pf.check("feat", root=tmp)
        assert ok, f"markdown+prose Related bullet must resolve to the slug: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_a_genuinely_missing_brief_still_blocks_with_a_helpful_error():
    """Tolerance must not swallow a real error: an unknown slug still fails, and the
    message now names the fix (bare slug / prose after delimiter) instead of echoing
    the mangled string."""
    tmp = _mktmp_project()
    try:
        _charter(tmp)
        _fit(tmp, "feat", advances="- P5 — advances CI credit",
             related="- nonexistent-feature — some prose")
        ok, errs = pf.check("feat", root=tmp)
        assert not ok
        r = next(e for e in errs if "Related" in e)
        assert "nonexistent-feature" in r and "bare feature slug" in r.lower()
    finally:
        shutil.rmtree(tmp)
