"""Parallel reviewers (regression-sentinel + conventions-enforcer) capture against one
shared worktree substantive hash. Whichever runs SECOND can move the hash — its capture
drops an artifact into the shared partial mirror — retroactively staling the first
reviewer's already-valid evidence. The git-worktree path excludes such artifacts via
gitignore (v0.152.0); the partial-mirror path now does too, so each reviewer's snapshot
binds to the JUDGED source only and is stable against a co-reviewer's run.

fb-92e248d6a208 (snapshot-per-reviewer binding).

moat-finding: fb-92e248d6a208
"""

from __future__ import annotations

import subprocess

import pytest

from prusik import consistency, gate


def _git(d, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "-C", str(d), *args],
        capture_output=True, text=True, check=True)


def _has_git():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_git(), reason="git not available")


def _setup(tmp_path):
    """A git ROOT with .coverage/*.log gitignored + a PARTIAL-MIRROR reviewer worktree."""
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    (root / ".gitignore").write_text(".coverage\n*.log\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    src = root / "worktrees" / "regression-sentinel" / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("def run():\n    return 1\n")
    return root


def test_gitignored_subset_identifies_artifacts(tmp_path):
    root = _setup(tmp_path)
    ig = consistency.gitignored_subset(root, ["src/app.py", ".coverage", "src/run.log"])
    assert ig == {".coverage", "src/run.log"}
    assert "src/app.py" not in ig


def test_gitignored_subset_non_git_root_keeps_all(tmp_path):
    # not a git repo → empty set (caller keeps everything; falls back to prior behavior)
    assert consistency.gitignored_subset(tmp_path, [".coverage", "src/app.py"]) == set()


def test_snapshot_stable_against_co_reviewer_artifact(tmp_path):
    root = _setup(tmp_path)
    h0 = gate._worktree_substantive_hash(root)

    # a co-reviewer's capture drops gitignored artifacts into the shared mirror
    (root / "worktrees" / "regression-sentinel" / ".coverage").write_text("cov\n")
    (root / "worktrees" / "regression-sentinel" / "src" / "run.log").write_text("noise\n")
    assert gate._worktree_substantive_hash(root) == h0     # snapshot UNCHANGED — no race

    # a REAL source change still moves the snapshot (freshness preserved)
    (root / "worktrees" / "regression-sentinel" / "src" / "app.py").write_text(
        "def run():\n    return 2\n")
    assert gate._worktree_substantive_hash(root) != h0
