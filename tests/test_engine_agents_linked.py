"""The engine repo registers the packaged role library through a link (fb-d0717082827b).

`/sprint-start` dispatches roles with `Agent(subagent_type=<role>)`, which Claude Code resolves
from `.claude/agents/`. The engine had none, so every manual dispatch failed and the operator
hand-rolled a costlier, unrestricted stand-in. The engine's `.claude/agents` is a relative
symlink to `prusik/templates/.claude/agents`: the shipped specs stay the one source of truth.

moat-finding: fb-d0717082827b
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from prusik import agents_doctor

ROOT = Path(__file__).resolve().parent.parent
TARGET = Path("..") / "prusik" / "templates" / ".claude" / "agents"

pytestmark = pytest.mark.skipif(not (ROOT / ".git").exists(),
                                reason="engine root is not a git checkout (installed wheel)")


def _link_problem(root: Path) -> str | None:
    link = root / ".claude" / "agents"
    if not link.is_symlink():
        return f"{link} is not a symlink"
    if Path(os.readlink(link)) != TARGET:
        return f"{link} points at {os.readlink(link)}, not {TARGET}"
    if link.resolve() != (root / "prusik" / "templates" / ".claude" / "agents").resolve():
        return f"{link} does not resolve to the packaged library"
    return None


def _names(d: Path) -> set[str]:
    return {p.name for p in d.glob("*.md")}


def test_agents_dir_is_relative_link_to_packaged_library():
    assert _link_problem(ROOT) is None, _link_problem(ROOT)


def test_link_lists_the_same_specs():
    packaged = _names(ROOT / "prusik" / "templates" / ".claude" / "agents")
    assert packaged
    assert _names(ROOT / ".claude" / "agents") == packaged


def test_agents_doctor_passes_on_engine_root(capsys):
    assert agents_doctor.doctor(ROOT) == 0, capsys.readouterr().out


def test_copied_directory_is_rejected(tmp_path):
    # Drift class: a real directory of copies resolves today and silently diverges later.
    src = ROOT / "prusik" / "templates" / ".claude" / "agents"
    (tmp_path / "prusik" / "templates" / ".claude").mkdir(parents=True)
    shutil.copytree(src, tmp_path / "prusik" / "templates" / ".claude" / "agents")
    shutil.copytree(src, tmp_path / ".claude" / "agents")
    assert _link_problem(tmp_path) == f"{tmp_path / '.claude' / 'agents'} is not a symlink"
    # and the correct link on the same tree passes
    shutil.rmtree(tmp_path / ".claude" / "agents")
    (tmp_path / ".claude" / "agents").symlink_to(TARGET)
    assert _link_problem(tmp_path) is None
