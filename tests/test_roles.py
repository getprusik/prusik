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
