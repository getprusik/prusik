"""fb-d3adb6e68c43 — `gate capture` printed its 'ran at PROJECT ROOT — measured main
instead' warning even when the command itself embeds `cd worktrees/<role> && …` and
correctly stamps the worktree hash. That's a FALSE alarm that costs reviewer time
re-verifying against the evidence file. The warning is suppressed when the command
self-navigates into a worktree.

moat-finding: fb-d3adb6e68c43
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from prusik import gate


def _wt_project():
    tmp = Path(tempfile.mkdtemp(prefix="prusik-selfcd-"))
    (tmp / "worktrees" / "solo").mkdir(parents=True)   # worktree-mode sprint
    return tmp


def test_self_cd_detection():
    ok = gate._cmd_self_navigates_to_worktree
    assert ok("cd worktrees/solo && pnpm contracts:check")
    assert ok("cd ./worktrees/backend && pytest")
    assert ok('cd "worktrees/test-writer" && npx vitest run')
    assert not ok("pytest tests/")                     # no cd
    assert not ok("cd packages/frontend && tsc")       # cd, but not into a worktree


def test_warning_suppressed_when_command_self_cds_into_worktree():
    tmp = _wt_project()
    try:
        root = tmp
        # capture ran at ROOT (exec_dir == root) and worktrees exist → would warn,
        # BUT the command cd's into the worktree itself, so it's a false alarm.
        assert gate._wrong_tree_warning(root, root,
                                        "cd worktrees/solo && pnpm lint") is None
        # a command that does NOT self-cd still gets the real warning
        w = gate._wrong_tree_warning(root, root, "pnpm lint")
        assert w and "PROJECT ROOT" in w
    finally:
        shutil.rmtree(tmp)
