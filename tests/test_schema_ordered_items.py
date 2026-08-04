"""CommonMark ordered-list items count as list items (fb-664f701dc005).

moat-finding:fb-664f701dc005 — the shipped plan template teaches a numbered
Build order (`1. <step>`) that `extract_list_items` didn't recognize, so a
plan authored exactly from prusik's own template failed `prusik gate plan`
("build_order: needs ≥1 bullet items (got 0)"). Caught live by the
plan-critic during the overhead-accounting sprint.
"""

from prusik import schema


def test_template_shaped_numbered_build_order_validates(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("""## Goal recap
Fix the ordered-list parser gap.

## Modules touched
- prusik/schema.py  — parser fix

## Build order
1. write the failing test
2. fix the parser
3. run the suite

## Interfaces
- extract_list_items(body) -> list[str]

## Test plan
- happy path
- failure mode
- regression target

## Risks
- consumer drift

## Out of scope
- everything else

## Proposed roles
- solo → prusik/schema.py
""")
    ok, errors = schema.validate_plan(plan)
    assert ok, errors


def test_ordered_markers_counted_and_stripped():
    body = "1. first step\n12. twelfth step\n3) paren form\n"
    assert schema.extract_list_items(body) == [
        "first step", "twelfth step", "paren form"]


def test_no_space_after_marker_is_prose_not_item():
    body = "3.5x faster than before\n0.198.0 released today\n1.result\n"
    assert schema.extract_list_items(body) == []


def test_indented_numbered_lines_stay_nested():
    body = "- parent item\n  1. nested step\n  2. nested step\n"
    assert schema.extract_list_items(body) == ["parent item"]


def test_existing_bullet_forms_unchanged():
    body = "- dash\n+ new/file.py\n* star\n---\n"
    assert schema.extract_list_items(body) == [
        "dash", "+ new/file.py", "star"]
