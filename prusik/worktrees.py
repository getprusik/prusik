"""Sprint worktrees: the one place prusik creates them, and the fork point each one records.

A builder's change set is what differs between the commit its worktree forked from and its
working tree (fb-109c7034f558). That fork point is recorded at creation as the git ref
`refs/prusik/base/<branch>`, beside the branch it describes, and read back by the branch the
worktree has checked out. The ref lives in git, not the ledger: a `-B` re-fork moves the
branch, and the ref moves with it in the same function. The ledger's `worktree_created`
event is telemetry; nothing reads a base from it.

- Fresh, or re-forked with `-B ... HEAD` (no branch yet, or one with no commits of its own):
  the ref is overwritten with the new fork. A same-sprint retry after the shared tree
  advanced, and a later sprint reusing the feature name after integration, both measure
  from the new fork.
- Reused (the branch carries its own commits): the ref is kept. Absent (a worktree left by an
  older engine), it is set to merge-base(branch, HEAD), which before integration is the fork.
- An existing worktree directory: the ref is written only when absent, so a re-run never
  moves it. A branch whose tip is a strict ancestor of HEAD (merged or stale) is refused:
  its merge-base is its own tip, which would read as an empty change set.

A worktree with no branch (detached HEAD) or no recorded base raises
`consistency.UnmeasuredChangeSet`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

BASE_REF_PREFIX = "refs/prusik/base/"

# argv run at the project root → (rc, output). drive passes its logging runner.
Runner = Callable[[list[str]], tuple[int, str]]


class WorktreeError(Exception):
    """A sprint worktree could not be created or its fork point recorded."""

    def __init__(self, reason: str, tail: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.tail = list(tail or [])


def branch_name(feature: str, role: str) -> str:
    return f"sprint/{feature}-{role}"


def base_ref(branch: str) -> str:
    return f"{BASE_REF_PREFIX}{branch}"


def _git(d: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True,
                          check=False)


def _runner(root: Path) -> Runner:
    def run(argv: list[str]) -> tuple[int, str]:
        p = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, check=False)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    return run


def _last_line(text: str) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def is_git_worktree_root(d: Path) -> bool:
    """`d` is the top of its own git work tree (a real worktree, not a partial mirror inside
    the project's tree). Resolves no base."""
    try:
        top = _git(d, "rev-parse", "--show-toplevel")
    except OSError:
        return False
    if top.returncode != 0:
        return False
    try:
        return Path(top.stdout.strip()).resolve() == d.resolve()
    except OSError:
        return False


def _record_hint(role: str) -> str:
    return f"`prusik gate worktree-add --feature <feature> --role {role}`"


def recorded_base(worktree: Path) -> str:
    """The commit `worktree` forked from, read through its branch's base ref. Raises
    `UnmeasuredChangeSet` for a detached HEAD or a branch with no resolvable base."""
    from prusik.consistency import UnmeasuredChangeSet
    name = f"worktrees/{worktree.name}"
    head = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    branch = head.stdout.strip() if head.returncode == 0 else ""
    if not branch or branch == "HEAD":
        raise UnmeasuredChangeSet(
            f"{name} has a detached HEAD, so it has no branch and no recorded fork point; "
            f"its change set is unmeasured. Re-create it with {_record_hint(worktree.name)}.")
    ref = base_ref(branch)
    base = _git(worktree, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if base.returncode != 0 or not base.stdout.strip():
        raise UnmeasuredChangeSet(
            f"{name} (branch {branch}) has no recorded fork point ({ref}): it was not "
            f"created by prusik, so its change set is unmeasured. Record it with "
            f"{_record_hint(worktree.name)}.")
    return base.stdout.strip()


def _write_base(run: Runner, feature: str, role: str, branch: str, base: str) -> None:
    rc, tail = run(["git", "update-ref", base_ref(branch), base])
    if rc != 0:
        raise WorktreeError(f"could not record the fork point {base_ref(branch)}",
                            tail.splitlines())
    from prusik import ledger
    ledger.append("worktree_created", feature=feature, role=role, branch=branch, base=base)


def _rev(run: Runner, rev: str) -> str | None:
    rc, out = run(["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
    sha = _last_line(out) if rc == 0 else ""
    return sha or None


def _has_base(run: Runner, branch: str) -> bool:
    return _rev(run, base_ref(branch)) is not None


def _record_from_merge_base(run: Runner, feature: str, role: str, branch: str) -> None:
    """Set an absent base to merge-base(branch, HEAD), refusing a branch already contained
    in HEAD: its merge-base is its own tip and would read as an empty change set."""
    tip, head = _rev(run, f"refs/heads/{branch}"), _rev(run, "HEAD")
    if tip is None or head is None:
        raise WorktreeError(f"cannot resolve {branch} or HEAD to record its fork point")
    if tip != head and run(["git", "merge-base", "--is-ancestor", tip, head])[0] == 0:
        raise WorktreeError(
            f"{branch} is already contained in HEAD (merged or stale), so its fork point "
            f"cannot be recovered. Remove the worktree (`git worktree remove "
            f"worktrees/{role}`) and re-create it with `prusik gate worktree-add "
            f"--feature {feature} --role {role}`, which forks it afresh with -B.")
    rc, out = run(["git", "merge-base", branch, "HEAD"])
    base = _last_line(out) if rc == 0 else ""
    if not base:
        raise WorktreeError(f"`git merge-base {branch} HEAD` rc {rc}", out.splitlines())
    _write_base(run, feature, role, branch, base)


def _record_existing(wt: Path, feature: str, role: str, run: Runner) -> None:
    rc, out = run(["git", "-C", str(wt), "rev-parse", "--show-toplevel"])
    try:
        own_root = rc == 0 and Path(_last_line(out)).resolve() == wt.resolve()
    except OSError:
        own_root = False
    if not own_root:
        return                                   # a partial mirror: its walk is its change set
    rc, out = run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"])
    branch = _last_line(out) if rc == 0 else ""
    if not branch or branch == "HEAD":
        raise WorktreeError(
            f"worktrees/{role} has a detached HEAD: no branch to record a fork point for. "
            f"Remove it (`git worktree remove worktrees/{role}`) and re-create it with "
            f"`prusik gate worktree-add --feature {feature} --role {role}`.")
    if not _has_base(run, branch):
        _record_from_merge_base(run, feature, role, branch)


def create(root: Path, feature: str, role: str, run: Runner | None = None) -> Path:
    """Create (or reuse) `worktrees/<role>` for `feature` and record its fork point. In a
    project without git it is a plain directory (a partial mirror). Raises `WorktreeError`."""
    run = run or _runner(root)
    wt = root / "worktrees" / role
    git_project = (root / ".git").exists()
    if wt.exists():
        if git_project:
            _record_existing(wt, feature, role, run)
        return wt
    if not git_project:
        wt.mkdir(parents=True)
        return wt
    # A worktree dir removed without `git worktree remove` stays registered and blocks
    # `add` at that path; prune drops only registrations whose dir is gone. A branch left
    # by an earlier attempt that carries commits of its own is reused as is, so a builder's
    # work survives a retry. One with no commits beyond HEAD's history holds nothing to lose
    # and starts again from HEAD (-B).
    branch = branch_name(feature, role)
    rc, tail = run(["git", "worktree", "prune"])
    spent = True
    if rc == 0:
        exists = run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])[0]
        spent = exists != 0 or run(["git", "merge-base", "--is-ancestor", branch,
                                    "HEAD"])[0] == 0
        add = (["git", "worktree", "add", "-B", branch, f"worktrees/{role}", "HEAD"]
               if spent else ["git", "worktree", "add", f"worktrees/{role}", branch])
        rc, tail = run(add)
    if rc != 0:
        raise WorktreeError(f"`git worktree add` rc {rc}", tail.splitlines())
    if spent:
        fork = _rev(run, f"refs/heads/{branch}")
        if fork is None:
            raise WorktreeError(f"cannot resolve {branch} after `git worktree add` to "
                                f"record its fork point")
        _write_base(run, feature, role, branch, fork)       # every re-fork overwrites
    elif not _has_base(run, branch):
        _record_from_merge_base(run, feature, role, branch)
    return wt


def prune_base_refs(root: Path) -> list[str]:
    """Delete each `refs/prusik/base/<branch>` whose branch no longer exists. Returns the
    branch names whose refs were removed."""
    if not (root / ".git").exists():
        return []
    p = _git(root, "for-each-ref", "--format=%(refname)", BASE_REF_PREFIX)
    if p.returncode != 0:
        return []
    removed: list[str] = []
    for ref in p.stdout.split():
        branch = ref[len(BASE_REF_PREFIX):]
        if _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode:
            if _git(root, "update-ref", "-d", ref).returncode == 0:
                removed.append(branch)
    return sorted(removed)
