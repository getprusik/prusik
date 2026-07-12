"""Scope-boundary check on a real build worktree (v0.79.1, field finding #15):
a JS/TS worktree legitimately has node_modules/ + configs (prusik worktree-setup,
#10/#11). The boundary check must judge only the builder's CHANGED files, not
walk the whole tree (An adopter: 79,988 phantom violations on a ~41-file change)."""

from __future__ import annotations

import shutil
import subprocess

from tests._common import _mktmp_project  # noqa: F401,E402
from prusik import consistency


def _git(d, *a):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True)


def _scope(proj, mods):
    p = proj / "design" / "feat" / "scope.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"## Modules touched\n{mods}\n")


def test_git_worktree_only_changed_files_checked():
    proj = _mktmp_project()
    try:
        _scope(proj, "- src/")
        wt = proj / "worktrees" / "frontend-builder"
        wt.mkdir(parents=True)
        _git(wt, "init", "-q")
        _git(wt, "config", "user.email", "t@t.t")
        _git(wt, "config", "user.name", "t")
        (wt / ".gitignore").write_text("node_modules/\ndist/\n")
        (wt / "turbo.json").write_text("{}")
        (wt / "tsconfig.json").write_text("{}")
        (wt / "node_modules").mkdir()
        (wt / "node_modules" / "dep.js").write_text("x = 1\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-qm", "base")
        # the builder's actual changes:
        (wt / "src").mkdir()
        (wt / "src" / "x.ts").write_text("export const x = 1\n")     # in scope
        (wt / "rogue").mkdir()
        (wt / "rogue" / "y.ts").write_text("export const y = 1\n")   # OUT of scope
        v = " ".join(consistency.builder_writes_within_plan(proj, "feat"))
        assert "rogue/y.ts" in v                       # the real out-of-scope change
        assert "node_modules" not in v                 # gitignored dep, excluded
        assert "turbo.json" not in v and "tsconfig.json" not in v   # unchanged
        assert "src/x.ts" not in v                     # in scope
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_partial_mirror_excludes_build_dirs():
    """Non-git partial-mirror dir (Python sprint): still walk it, but build/dep
    dirs are excluded so a stray node_modules can't flag thousands of files."""
    proj = _mktmp_project()
    try:
        _scope(proj, "- src/")
        wt = proj / "worktrees" / "solo"
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "x.py").write_text("x = 1\n")                  # in scope
        (wt / "node_modules" / "dep").mkdir(parents=True)
        (wt / "node_modules" / "dep" / "i.js").write_text("x\n")     # excluded
        (wt / "rogue").mkdir()
        (wt / "rogue" / "y.py").write_text("y = 1\n")                # OUT of scope
        v = " ".join(consistency.builder_writes_within_plan(proj, "feat"))
        assert "rogue/y.py" in v
        assert "node_modules" not in v
        assert "src/x.py" not in v
    finally:
        shutil.rmtree(proj, ignore_errors=True)
