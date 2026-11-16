"""A git or patch call that rewrites work-tree files names that whole tree, so the writable check
fails closed where the tree is not writable as a whole. Real git and patch are the oracle: a write
case must change a file on disk, and a read case must leave every file alone.

moat-finding: fb-1303f39d14a6
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from prusik import gate, phases
from prusik.platform_adapter import ToolEvent

_ID = ["-c", "user.email=t@t", "-c", "user.name=t"]


def _git(repo, *args):
    subprocess.run(["git", *_ID, *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """a.txt at `base` on main; branch `other` changes it; a stash holds another change; an
    untracked file; ../p.diff is the `other` change as a patch."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("base\n")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "other")
    (root / "a.txt").write_text("other\n")
    _git(root, "commit", "-qam", "other")
    _git(root, "checkout", "-q", "main")
    (tmp_path / "p.diff").write_text(subprocess.run(
        ["git", "diff", "main", "other"], cwd=root, capture_output=True, text=True,
        check=True).stdout)
    (root / "a.txt").write_text("stashed\n")
    _git(root, "stash", "-q")
    (root / "loose.txt").write_text("untracked\n")
    return root


def _files(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def _run(cmd, repo):
    subprocess.run(["/bin/bash", "-c", cmd], cwd=repo, capture_output=True, check=False)


_WRITES = [
    "git diff main other | git apply",
    "git apply ../p.diff",
    "git apply --index ../p.diff",
    "git checkout other -- a.txt",
    "git checkout other",
    "git restore --source other a.txt",
    "git merge -q other",
    "git reset --hard other",
    "git stash pop",
    "git stash apply",
    "git clean -f",
    "git cherry-pick other",
    "git rm -q a.txt",
    "git mv a.txt b.txt",
    "git switch -q other",
    "patch -p1 < ../p.diff",
    "git -c core.pager=cat apply ../p.diff",
]
_READS = [
    "git apply --check ../p.diff",
    "git apply --stat ../p.diff",
    "git apply --cached ../p.diff",
    "git stash list",
    "git stash show",
    "git reset other",
    "git reset --soft other",
    "git clean -n",
    "git clean -fdn",
    "git restore --staged a.txt",
    "git rm -q --cached a.txt",
    "git checkout",
    "git status",
    "git diff main other",
    "git log --oneline",
    "patch --dry-run -p1 < ../p.diff",
]


@pytest.mark.parametrize("cmd", _WRITES)
def test_every_tree_write_real_git_performs_is_named(cmd, repo):
    before = _files(repo)
    _run(cmd, repo)
    assert _files(repo) != before, f"oracle: {cmd!r} changed no file"
    assert gate._bash_write_targets(cmd) == ["**"]


@pytest.mark.parametrize("cmd", _READS)
def test_a_form_that_leaves_every_file_alone_names_nothing(cmd, repo):
    before = _files(repo)
    _run(cmd, repo)
    assert _files(repo) == before, f"oracle: {cmd!r} changed a file"
    assert gate._bash_write_targets(cmd) == []


@pytest.mark.parametrize("cmd, tree", [
    ("git -C worktrees/backend-builder apply p.diff", "worktrees/backend-builder/**"),
    ("cd worktrees/backend-builder/src && git reset --hard HEAD", "worktrees/backend-builder/**"),
    ("cd reports/f && git reset --hard HEAD", "**"),
    ("cd worktrees && git stash pop", "**"),
    ("git -C /tmp/prusik-review-f merge --no-edit sprint/f-b", "/tmp/prusik-review-f/**"),
    ("patch -d worktrees/solo -p1 < x.diff", "worktrees/solo/**"),
    ("git -C $D apply p.diff", "$D/**"),
])
def test_the_tree_is_where_the_call_runs(cmd, tree):
    assert gate._bash_write_targets(cmd) == [tree]


def test_the_prefilter_parses_a_git_write_and_skips_a_git_read():
    assert gate._may_write("git diff a b | git apply")
    assert gate._may_write("g''it st''ash pop")
    assert not gate._may_write("git status && git log --oneline && git diff")


# ---- the gate itself -------------------------------------------------------------------

_CFG = {"phases": [
    {"name": "building", "writable": ["worktrees/*/**"]},
    {"name": "reviewing", "writable": ["reports/{feature}/**"]},
    {"name": "integrating", "writable": ["**"]},
]}


@pytest.fixture
def gated(tmp_path, monkeypatch):
    blocks = []
    monkeypatch.setattr(phases, "project_root", lambda: tmp_path)
    monkeypatch.setattr(gate.ledger, "project_root", lambda: tmp_path)
    monkeypatch.setattr(gate.ledger, "append", lambda *a, **k: blocks.append(k))

    def decide(cmd, phase):
        return gate._gate_decision(ToolEvent(tool="Bash", command=cmd), _CFG, phase, "f")
    decide.blocks = blocks
    return decide


@pytest.mark.parametrize("cmd", [
    "git diff main...sprint/f-backend-builder | git apply",
    "git checkout sprint/f-backend-builder -- prusik tests",
    "git stash pop",
    "cd reports/f && git merge sprint/f-backend-builder",
    "patch -p1 < /tmp/x.diff",
])
def test_reviewing_refuses_a_write_to_the_shared_tree(gated, cmd):
    assert gated(cmd, "reviewing") is not None
    assert gated.blocks[-1]["gate_class"] == gate.gate_class.WRITABLE_SCOPE


@pytest.mark.parametrize("cmd, phase", [
    ("git -C worktrees/backend-builder apply /tmp/x.diff", "building"),
    ("cd worktrees/backend-builder && git reset --hard HEAD", "building"),
    ("git worktree add --detach /tmp/prusik-review-f HEAD && "
     "git -C /tmp/prusik-review-f merge --no-edit sprint/f-b", "reviewing"),
    ("git merge --no-edit sprint/f-backend-builder", "integrating"),
    ("git diff main...sprint/f-b > reports/f/diff.txt && git log", "reviewing"),
])
def test_a_tree_the_phase_may_write_stays_open(gated, cmd, phase):
    assert gated(cmd, phase) is None
