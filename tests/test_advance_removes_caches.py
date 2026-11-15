"""`gate advance` deletes the worktree cache dirs that are tool output by name (fb-f0c2851ea5f9).

Reviewers run ruff/pytest/mypy in a worktree, and the scanner then blocked the advance on the
cache they left, which asked the agent to run `rm -rf` by hand. A name on both the block list and
the derived-dir list regenerates and belongs to no deliverable, so the advance removes it and
says so. `.tox`, `.cache` and `.coverage` can hold real content and still block.

moat-finding: fb-f0c2851ea5f9
"""

from __future__ import annotations

import argparse
import io
import shutil
from contextlib import redirect_stderr, redirect_stdout

from tests._common import _copy_sprint_config, _mktmp_project

from prusik import gate, ledger, phases


def _building_project():
    tmp = _mktmp_project()
    _copy_sprint_config(tmp)
    phases.set_sprint_state({"feature": "feat", "phase": "building"}, tmp)
    return tmp


def _advance():
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = gate.advance(argparse.Namespace(phase="reviewing", feature="feat",
                                             allow_rewind=False))
    return rc, out.getvalue(), err.getvalue()


def test_advance_removes_known_tool_caches_and_says_so():
    tmp = _building_project()
    try:
        wt = tmp / "worktrees" / "x"
        for name in (".ruff_cache", "__pycache__"):
            (wt / "pkg" / name).mkdir(parents=True)
            (wt / "pkg" / name / "junk").write_text("")
        (wt / "pkg" / "real.py").write_text("x = 1\n")
        rc, out, err = _advance()
        assert rc == 0, err
        assert not (wt / "pkg" / ".ruff_cache").exists()
        assert not (wt / "pkg" / "__pycache__").exists()
        assert (wt / "pkg" / "real.py").exists(), "deliverables are never removed"
        assert "[prusik-gate] removed tool caches from worktrees:" in out
        assert "worktrees/x/pkg/.ruff_cache" in out
        evts = [e for e in ledger.read_all() if e["event"] == "worktree_caches_removed"]
        assert len(evts) == 1 and evts[0]["phase"] == "building"
        assert sorted(evts[0]["paths"]) == ["worktrees/x/pkg/.ruff_cache",
                                            "worktrees/x/pkg/__pycache__"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_advance_still_blocks_on_caches_that_may_hold_real_content():
    tmp = _building_project()
    try:
        wt = tmp / "worktrees" / "x"
        (wt / ".cache").mkdir(parents=True)
        (wt / ".cache" / "keep").write_text("maybe real")
        (wt / ".ruff_cache").mkdir()
        rc, out, err = _advance()
        assert rc == 2
        assert "tool-emitted cache directories" in out + err
        assert (wt / ".cache" / "keep").exists(), "an ambiguous cache is never deleted"
        assert not (wt / ".ruff_cache").exists(), "the derived one beside it is removed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_remove_skips_dependency_trees():
    from prusik import consistency
    tmp = _mktmp_project()
    try:
        dep = tmp / "worktrees" / "x" / "node_modules" / "pkg" / "__pycache__"
        dep.mkdir(parents=True)
        assert consistency.remove_derived_worktree_caches(tmp) == []
        assert dep.exists(), "a dependency's own cache is not this sprint's output"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
