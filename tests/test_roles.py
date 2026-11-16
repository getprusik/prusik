"""Proposed-role staffing check (v0.76.0, finding #12) — flag a planned role with
no shipped agent before the build (it would fall back to a manual stand-in)."""

from __future__ import annotations

import shutil

from tests._common import _mktmp_project  # noqa: F401,E402
from prusik import roles


_PLAN = """## Proposed roles

- **backend-builder-repo** → `src/repo.py`
- **frontend-builder-css** → `static/styles.css`
- **test-writer** → `tests/test_x.py`
"""


def _agents(tmp, names):
    d = tmp / ".claude" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / f"{n}.md").write_text(f"---\nname: {n}\n---\n")


def test_proposed_roles_parsed():
    assert roles.proposed_roles(_PLAN) == [
        "backend-builder-repo", "frontend-builder-css", "test-writer"]


def test_unstaffed_role_flagged():
    tmp = _mktmp_project()
    try:
        _agents(tmp, ["backend-builder", "test-writer"])   # no frontend-builder
        missing = roles.unstaffed_roles(_PLAN, tmp)
        assert missing == ["frontend-builder-css"]         # base maps to nothing
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_all_staffed_when_agent_present():
    tmp = _mktmp_project()
    try:
        _agents(tmp, ["backend-builder", "frontend-builder", "test-writer"])
        assert roles.unstaffed_roles(_PLAN, tmp) == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_advisory_none_when_staffed_and_string_when_not():
    tmp = _mktmp_project()
    try:
        (tmp / "design" / "feat").mkdir(parents=True)
        (tmp / "design" / "feat" / "plan.md").write_text(_PLAN)
        _agents(tmp, ["backend-builder", "test-writer"])
        adv = roles.advisory("feat", tmp)
        assert adv is not None and "frontend-builder-css" in adv
        _agents(tmp, ["frontend-builder"])
        assert roles.advisory("feat", tmp) is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_frontend_builder_agent_is_shipped():
    """Finding #12: the template library must actually ship frontend-builder."""
    from pathlib import Path
    import prusik
    agents = Path(prusik.__file__).parent / "templates" / ".claude" / "agents"
    assert (agents / "frontend-builder.md").exists()


def test_proposed_roles_reads_the_template_arrow_form():
    """The template and feature-planner prescribe `- role → modules` (no bold); drive's
    builder dispatch read only the bold form and escalated "plan names no builder role"
    on a plan that followed the spec (observed-writes, 2026-11-16)."""
    plan = (
        "## Proposed roles\n"
        "- backend-builder → `prusik/a.py` (steps 1-3)\n"
        "- test-writer -> `tests/test_a.py`\n"
        "- `integrator` → release bookkeeping\n"
        "- **frontend-builder** owns `worktrees/frontend-builder/`\n"
        "- solo-builder owns every module above\n"
    )
    assert roles.proposed_roles(plan) == [
        "backend-builder", "test-writer", "integrator", "frontend-builder", "solo-builder"]


def test_prose_bullets_under_proposed_roles_are_not_roles():
    plan = (
        "## Proposed roles\n"
        "- backend-builder → `prusik/a.py`\n"
        "- the builder stops and escalates if step 1 fails\n"
        "- tests run in the worktree, not the main tree\n"
        "- see Build order for sequencing: steps 1-9\n"
        "- note: the builder runs the suite\n"
    )
    assert roles.proposed_roles(plan) == ["backend-builder"]
