"""init pre-flight git due diligence — fail-closed on a dirty tree (v0.52.0)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from tests._common import _mktmp_project  # noqa: F401,E402
from prusik import preflight
from prusik import init as kit_init


def _git(target, *args):
    subprocess.run(["git", "-C", str(target), *args],
                   capture_output=True, text=True, check=True)


def _new_git_repo() -> Path:
    d = Path(tempfile.mkdtemp(prefix="kit-pf-"))
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t.t")
    _git(d, "config", "user.name", "t")
    (d / "README.md").write_text("seed\n")
    _git(d, "add", "README.md")
    _git(d, "commit", "-qm", "seed")
    return d


# ---------- git_status detection ----------

def test_git_status_clean():
    d = _new_git_repo()
    assert preflight.git_status(d) == "clean"


def test_git_status_dirty_on_modification():
    d = _new_git_repo()
    (d / "README.md").write_text("changed\n")
    assert preflight.git_status(d) == "dirty"


def test_git_status_dirty_on_untracked():
    d = _new_git_repo()
    (d / "new.txt").write_text("x\n")
    assert preflight.git_status(d) == "dirty"


def test_git_status_non_repo():
    d = Path(tempfile.mkdtemp(prefix="kit-pf-nogit-"))
    assert preflight.git_status(d) == "no-git"


# ---------- init_guard: the fail-closed contract ----------

def test_guard_refuses_dirty_tree():
    d = _new_git_repo()
    (d / "README.md").write_text("wip\n")
    ok, msg = preflight.init_guard(d, allow_dirty=False)
    assert ok is False
    assert "REFUSING" in msg and "stash" in msg


def test_guard_allows_dirty_with_override():
    d = _new_git_repo()
    (d / "README.md").write_text("wip\n")
    ok, msg = preflight.init_guard(d, allow_dirty=True)
    assert ok is True
    assert "--allow-dirty" in msg


def test_guard_allows_clean_tree_silently():
    d = _new_git_repo()
    ok, msg = preflight.init_guard(d, allow_dirty=False)
    assert ok is True and msg == ""


def test_guard_warns_but_allows_non_repo():
    d = Path(tempfile.mkdtemp(prefix="kit-pf-nogit-"))
    ok, msg = preflight.init_guard(d, allow_dirty=False)
    assert ok is True
    assert "WARNING" in msg and "git" in msg.lower()


# ---------- init.run honors the gate ----------

def test_init_refuses_to_run_on_dirty_tree():
    import os
    d = _new_git_repo()
    (d / "README.md").write_text("uncommitted\n")
    cwd = os.getcwd()
    try:
        os.chdir(d)
        os.environ["CLAUDE_PROJECT_DIR"] = str(d)
        rc = kit_init.run()
        assert rc == 2, "init must fail closed on a dirty tree"
        assert not (d / ".sprint").exists(), "init must not scaffold on refusal"
        assert not (d / ".claude").exists()
    finally:
        os.chdir(cwd)


def test_init_runs_on_clean_tree():
    import os
    d = _new_git_repo()
    cwd = os.getcwd()
    try:
        os.chdir(d)
        os.environ["CLAUDE_PROJECT_DIR"] = str(d)
        rc = kit_init.run()
        assert rc == 0
        assert (d / ".sprint").exists() and (d / ".claude").exists()
    finally:
        os.chdir(cwd)
