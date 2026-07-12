"""The reviewer-evidence worktree hash must EXCLUDE gitignored build artifacts so a
build-/typecheck-triggering capture can't drift the hash and stale a co-reviewer's
evidence — while still moving on a real source change.

fb-b4eb142e5740 (dist-in-hash) was first fixed with a derived-dir DENYLIST; it
RECURRED as fb-086ca221468d because a tsc `tsbuildinfo` (a gitignored file the denylist
didn't name) drifted the hash, costing ~5 capture/advance cycles. The fix inverts the
denylist into a git-tracked ALLOWLIST: hash only what git considers project content
(tracked + untracked-not-ignored), so EVERY gitignored artifact is excluded by
construction, not by enumeration.

moat-finding: fb-086ca221468d
moat-finding: fb-b4eb142e5740
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


def _make_worktree(tmp_path):
    """A real git worktree at <root>/worktrees/backend, with *.tsbuildinfo gitignored."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    (origin / ".gitignore").write_text("*.tsbuildinfo\ndist/\n")
    (origin / "src.ts").write_text("export const x = 1\n")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-q", "-m", "init")
    root = tmp_path / "proj"
    root.mkdir()
    wt = root / "worktrees" / "backend"
    wt.parent.mkdir(parents=True)
    _git(origin, "worktree", "add", "-q", str(wt))
    return root, wt


def _has_git():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_git(), reason="git not available")


def test_git_project_files_excludes_gitignored_artifact(tmp_path):
    _root, wt = _make_worktree(tmp_path)
    (wt / "tsconfig.tsbuildinfo").write_text("DERIVED\n")     # gitignored
    (wt / "dist").mkdir()
    (wt / "dist" / "out.js").write_text("derived\n")          # gitignored dir
    (wt / "new.ts").write_text("export const y = 2\n")        # untracked, NOT ignored

    files = consistency.git_project_files(wt)

    assert files is not None
    assert "src.ts" in files                                   # tracked source
    assert "new.ts" in files                                   # new source counts
    assert "tsconfig.tsbuildinfo" not in files                 # gitignored → excluded
    assert "dist/out.js" not in files                          # gitignored dir → excluded


def test_partial_mirror_returns_none(tmp_path):
    # a plain dir (no git worktree) → None, so the caller falls back to the walk
    d = tmp_path / "worktrees" / "py"
    d.mkdir(parents=True)
    (d / "a.py").write_text("x = 1\n")
    assert consistency.git_project_files(d) is None


def test_hash_stable_across_tsbuildinfo_churn_but_moves_on_source(tmp_path):
    root, wt = _make_worktree(tmp_path)
    h0 = gate._worktree_substantive_hash(root)

    # a typecheck capture writes a gitignored tsbuildinfo → hash MUST NOT move
    (wt / "tsconfig.tsbuildinfo").write_text("FIRST\n")
    assert gate._worktree_substantive_hash(root) == h0
    (wt / "tsconfig.tsbuildinfo").write_text("CHANGED-BY-NEXT-CAPTURE\n")
    assert gate._worktree_substantive_hash(root) == h0          # still stable

    # a REAL source change MUST move the hash (no stale PASS surviving a code change)
    (wt / "src.ts").write_text("export const x = 999\n")
    assert gate._worktree_substantive_hash(root) != h0

    # a genuinely-new untracked source file ALSO moves it
    h1 = gate._worktree_substantive_hash(root)
    (wt / "added.ts").write_text("export const z = 3\n")
    assert gate._worktree_substantive_hash(root) != h1
