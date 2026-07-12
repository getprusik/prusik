"""Live-dogfooding fixes (An adopter, 2026-06-06) — the feedback channel's first real
use surfaced a transport bug + two worktree-setup bugs. Regression tests lock
them in.

moat-finding markers — findings these tests lock in (C7):
  moat-finding: fb-7a24578db48e  — worktree-setup --dir nonexistent → FileNotFoundError
  moat-finding: fb-1a95785eddf3  — worktree-setup --run bare `turbo` → exit 127
"""

from __future__ import annotations

import shutil

from tests._common import _mktmp_project  # noqa: F401
from prusik import detect, feedback, worktree_setup


# ---------- the channel transport bug (feedback filed from a worktree) ----------

def test_feedback_filed_from_worktree_transports_to_root():
    """The channel's first live use: An adopter filed from inside a builder worktree,
    so the finding was stranded in worktrees/<role>/.sprint and the export (which
    reads the root) missed it. Fix: writes canonicalise to the project root, and
    load_all() also sweeps worktrees to recover already-stranded findings."""
    tmp = _mktmp_project()
    try:
        wt = tmp / "worktrees" / "backend"
        wt.mkdir(parents=True)
        # (a) filing from inside a worktree now writes to the canonical ROOT
        feedback.file_feedback(wt, "bug", "filed from the worktree")
        assert (tmp / ".sprint" / "feedback.jsonl").exists()        # not stranded
        assert feedback.canonical_root(wt) == tmp
        # (b) an ALREADY-stranded finding (legacy) is still recovered by load_all
        (wt / ".sprint").mkdir(parents=True, exist_ok=True)
        import json
        rec = feedback.build_record("bug", "legacy stranded", ts="2026-06-06T00:00:00")
        (wt / ".sprint" / "feedback.jsonl").write_text(json.dumps(rec) + "\n")
        titles = {f["title"] for f in feedback.load_all(tmp)}
        assert "filed from the worktree" in titles and "legacy stranded" in titles
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- fb-7a24578db48e: worktree-setup --dir nonexistent ----------

def test_worktree_setup_missing_dir_fails_closed_not_traceback():
    tmp = _mktmp_project()
    try:
        rc = worktree_setup.run(dir_=str(tmp / "worktrees" / "ghost"),
                                do_run=True, root=tmp)
        assert rc == 2          # clear fail-closed, not a FileNotFoundError traceback
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- fb-1a95785eddf3: bare turbo → 127 ----------

def test_turbo_build_runs_through_package_manager():
    """A turbo monorepo's build must run via the PM (pnpm exec / npx), never bare
    `turbo` — the local binary isn't on PATH in a fresh worktree (exit 127)."""
    tmp = _mktmp_project()
    try:
        (tmp / "package.json").write_text('{"name":"root","private":true}\n')
        (tmp / "turbo.json").write_text("{}\n")
        (tmp / "pnpm-workspace.yaml").write_text("packages:\n  - packages/*\n")
        (tmp / "pnpm-lock.yaml").write_text("")
        cmds = detect.worktree_setup_commands(tmp)
        build = [c for c in cmds if "turbo run build" in c]
        assert build and build[0] == "pnpm exec turbo run build"   # not bare
        assert not any(c.strip().startswith("turbo ") for c in cmds)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
