"""The pre-push hook refuses a commit whose exact tree has no local full-suite pass.

0.244.0 pushed twice with code edited after its last suite run, and CI found it after the
push. scripts/pre-push.sh checks each pushed commit's tree hash against the passes that
scripts/suite.sh records, so an edit after the run blocks the push.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          env=_env())


def _repo(tmp: Path) -> Path:
    remote, work = tmp / "remote.git", tmp / "work"
    assert _git(tmp, "init", "-q", "--bare", str(remote)).returncode == 0
    work.mkdir()
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "t@t.t"),
                 ("config", "user.name", "t"), ("remote", "add", "origin", str(remote))):
        _git(work, *args)
    (work / "scripts").mkdir()
    shutil.copy2(_REPO / "scripts" / "pre-push.sh", work / "scripts" / "pre-push.sh")
    hooks = work / ".git" / "hooks"
    (hooks / "pre-push").symlink_to(work / "scripts" / "pre-push.sh")
    (work / "a.txt").write_text("1\n")
    _git(work, "add", ".")
    assert _git(work, "commit", "-qm", "base").returncode == 0
    return work


def _stamp(work: Path) -> None:
    tree = _git(work, "rev-parse", "HEAD^{tree}").stdout.strip()
    with open(work / ".git" / "prusik-suite-pass", "a", encoding="utf-8") as fh:
        fh.write(tree + "\n")


def test_push_without_a_suite_pass_is_blocked(tmp_path):
    work = _repo(tmp_path)
    p = _git(work, "push", "-q", "origin", "main")
    assert p.returncode != 0
    assert "no local full-suite pass" in p.stderr


def test_push_of_the_tree_that_passed_goes_through(tmp_path):
    work = _repo(tmp_path)
    _stamp(work)
    p = _git(work, "push", "-q", "origin", "main")
    assert p.returncode == 0, p.stderr


def test_an_edit_after_the_pass_blocks_the_push(tmp_path):
    work = _repo(tmp_path)
    _stamp(work)
    (work / "a.txt").write_text("2\n")
    _git(work, "commit", "-qam", "edit after the suite ran")
    p = _git(work, "push", "-q", "origin", "main")
    assert p.returncode != 0
    assert "no local full-suite pass" in p.stderr
    # the remote never received the unverified commit
    assert _git(tmp_path / "remote.git", "rev-parse", "-q", "--verify", "main").returncode != 0
