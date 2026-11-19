"""A reused `worktrees/<role>` must be checked out on this feature's branch.

fb-58581f22d51e: `worktrees.create` returned an existing worktree after only recording a fork
point, so a worktree left on another feature's (merged) branch would have been built on, and
the kept `sprint/<feature>-<role>` work skipped. It now refuses, naming both branches, and
leaves the worktree, its branch and its fork-point ref untouched.

moat-finding: fb-58581f22d51e
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from prusik import gate
from prusik import worktrees as wtmod

ROLE = "backend-builder"


def _git(d: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".sprint").mkdir(parents=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text(".sprint/\nworktrees/\n")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _other_feature_worktree(root: Path) -> tuple[Path, str, str]:
    """worktrees/<ROLE> created for feature `old`, with a commit of its own."""
    wt = wtmod.create(root, "old", ROLE)
    (wt / "a.py").write_text("A = 1\n")
    _git(wt, "add", "a.py")
    _git(wt, "commit", "-q", "-m", "old work")
    branch = wtmod.branch_name("old", ROLE)
    return wt, branch, _git(root, "rev-parse", wtmod.base_ref(branch))


def test_a_worktree_on_another_features_branch_is_refused_and_left_untouched(repo):
    wt, old_branch, old_base = _other_feature_worktree(repo)
    tip = _git(wt, "rev-parse", "HEAD")
    with pytest.raises(wtmod.WorktreeError) as ei:
        wtmod.create(repo, "new", ROLE)
    reason = ei.value.reason
    assert old_branch in reason and wtmod.branch_name("new", ROLE) in reason
    assert f"git worktree remove worktrees/{ROLE}" in reason
    # Adversarial: nothing about the existing worktree moved.
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD") == old_branch
    assert _git(wt, "rev-parse", "HEAD") == tip
    assert _git(repo, "rev-parse", wtmod.base_ref(old_branch)) == old_base
    new_ref = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                              wtmod.base_ref(wtmod.branch_name("new", ROLE))],
                             capture_output=True, text=True)
    assert new_ref.returncode != 0


def test_a_worktree_on_this_features_branch_is_reused_with_its_base_unchanged(repo):
    wt, branch, base = _other_feature_worktree(repo)
    assert wtmod.create(repo, "old", ROLE) == wt
    assert _git(repo, "rev-parse", wtmod.base_ref(branch)) == base
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD") == branch


def test_gate_worktree_add_exits_2_naming_both_branches(repo, capsys):
    _, old_branch, _ = _other_feature_worktree(repo)
    rc = gate.worktree_add(SimpleNamespace(feature="new", role=ROLE))
    err = capsys.readouterr().err
    assert rc == 2
    assert old_branch in err and wtmod.branch_name("new", ROLE) in err
