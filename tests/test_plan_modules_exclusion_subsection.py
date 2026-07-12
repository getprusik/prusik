"""`## Modules touched` may carry an exclusion/commentary subsection (e.g.
"### NOT in the touch-list (deliberate)"). parse_sections splits on `## ` only, so
those bullets used to be extracted as DECLARED modules → a false "plan.md adds
modules not in scope.md" (first the excluded backtick-paths, then the bullet-leading
word "The"). Declared modules must come only from the genuine list.

moat-finding: fb-085135ece453
"""

from __future__ import annotations

from prusik import consistency, schema

_PLAN = """## Modules touched
- `domain/company.py` — add entity_type field
- `api/entities.py` — new route

### NOT in the touch-list (deliberate)
- `domain/ledger.py` — intentionally unchanged
- `scripts/seed_and_render.py` — out of scope
- The reporting layer is deliberately left for a later sprint

## Test plan
- unit tests
"""

_SCOPE = """## Modules touched
- `domain/company.py`
- `api/entities.py`
"""


def test_excluded_subsection_not_treated_as_declared(tmp_path):
    body = schema.parse_sections(_PLAN)["## Modules touched"]
    items = schema.extract_list_items(schema.strip_non_declaration_subsections(body))
    mods = {schema.extract_module_token(i)[0] for i in items}
    assert mods == {"domain/company.py", "api/entities.py"}
    # the excluded real paths AND the prose bullet's "The" are gone
    assert "domain/ledger.py" not in mods
    assert "scripts/seed_and_render.py" not in mods
    assert "The" not in mods


def test_plan_within_scope_no_false_violation(tmp_path):
    feat = "entity-type-support"
    d = tmp_path / "design" / feat
    d.mkdir(parents=True)
    (d / "plan.md").write_text(_PLAN)
    (d / "scope.md").write_text(_SCOPE)
    # before the fix this returned a "plan.md adds modules not in scope.md" violation
    assert consistency.plan_within_scope(tmp_path, feat) == []


def test_grouping_subsection_is_preserved():
    """A non-exclusion subsection (### Backend) still contributes its modules."""
    body = schema.parse_sections(
        "## Modules touched\n### Backend\n- `api/x.py`\n### Frontend\n- `web/y.ts`\n"
    )["## Modules touched"]
    mods = {schema.extract_module_token(i)[0]
            for i in schema.extract_list_items(
                schema.strip_non_declaration_subsections(body))}
    assert mods == {"api/x.py", "web/y.ts"}
