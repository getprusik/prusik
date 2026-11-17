"""A critic verdict is bound to exactly the sections that critic judges.

fb-809e62a470ec: `## Modules touched` was excluded from every artifact's verdict hash,
though only plan-critic treats it as derived. scope-critic judges scope.md's module
list (its checks 2, 3, 5, 8, 9), so an APPROVED scope carried forward through any
rewrite of that list, including undoing a change the critic had forced.

moat-finding: fb-809e62a470ec
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from prusik import gate, ledger, schema

AGENTS = Path(schema.KIT_ROOT) / "templates" / ".claude" / "agents"
FEAT = "feat"


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".prusik").mkdir()
    (tmp_path / "design" / FEAT).mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ledger, "project_root", lambda: tmp_path)
    return tmp_path


def _approve(role: str, rel: str) -> argparse.Namespace:
    assert gate.record_verdict(argparse.Namespace(
        role=role, feature=FEAT, artifact=rel, verdict="APPROVED")) == 0
    return argparse.Namespace(role=role, feature=FEAT, artifact=rel)


def test_scope_modules_touched_rewrite_regates_an_approved_scope(project):
    rel = f"design/{FEAT}/scope.md"
    (project / rel).write_text("## Goal recap\nShip it.\n\n## Modules touched\n- api/a.py\n")
    cur = _approve("scope-critic", rel)
    assert gate.verdict_current(cur) == 0
    (project / rel).write_text("## Goal recap\nShip it.\n\n## Modules touched\n- api/b.py\n")
    assert gate.verdict_current(cur) == 1, \
        "an APPROVED scope must not survive a rewrite of the module list the critic judged"


def test_plan_modules_touched_churn_still_carries_the_plan_verdict(project):
    rel = f"design/{FEAT}/plan.md"
    (project / rel).write_text("## Build order\n- one\n\n## Modules touched\n- api/a.py\n")
    cur = _approve("plan-critic", rel)
    (project / rel).write_text("## Build order\n- one\n\n## Modules touched\n- api/a.py\n- b\n")
    assert gate.verdict_current(cur) == 0
    (project / rel).write_text("## Build order\n- two\n\n## Modules touched\n- api/a.py\n- b\n")
    assert gate.verdict_current(cur) == 1


def test_a_schemaless_artifact_hashes_every_section(tmp_path):
    doc = tmp_path / "CLAUDE.md"
    doc.write_text("## Modules touched\n- a\n")
    h = schema.substantive_hash(doc)
    doc.write_text("## Modules touched\n- b\n")
    assert schema.substantive_hash(doc) != h


@pytest.mark.parametrize("role,kind", sorted(gate._CRITIC_SCHEMA.items()))
def test_critic_spec_agrees_with_its_schema_on_unjudged_sections(role, kind):
    """The schema's `critic_judged: false` and the critic spec must state the same thing:
    each unjudged section is named in the spec as excluded from the hash, and a section
    the spec's numbered checks quote by heading is never declared unjudged (a check that
    calls the section non-gating is the spec saying it does NOT judge it)."""
    spec = (AGENTS / f"{role}.md").read_text()
    unjudged = schema.unjudged_sections(kind)
    for section in unjudged:
        assert re.search(rf"{re.escape(section)}`? churn is excluded from the\s+hash", spec), \
            f"{role}.md must state that {section} is excluded from its verdict hash"
    checks = "\n".join(line for line in spec.splitlines()
                       if re.match(r"\s*\d+\.\s+\*\*", line) and "non-gating" not in line)
    judged = set(re.findall(r"`(## [^`]+)`", checks))
    assert not judged & unjudged, \
        f"{role}.md judges {sorted(judged & unjudged)} but {kind}-schema excludes it from the hash"
    if role == "scope-critic":
        assert "## Modules touched" in judged and "## Modules touched" not in unjudged
