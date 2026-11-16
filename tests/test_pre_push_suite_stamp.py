"""The pre-push hook refuses a commit whose exact tree has no local full-suite pass.

0.244.0 pushed twice with code edited after its last suite run, and CI found it after the
push. scripts/pre-push.sh checks each pushed commit's tree hash against the passes that
scripts/suite.sh records, so an edit after the run blocks the push.

moat-finding: fb-be2b998c642c
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DECLARED = _REPO / "scripts" / "non-test-inputs.txt"


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
    shutil.copy2(_DECLARED, work / "scripts" / "non-test-inputs.txt")
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


# --- a commit whose diff from a passed tree touches only declared non-test inputs carries it ---


def _prefixes() -> list[str]:
    out = []
    for line in _DECLARED.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _commit(work: Path, files: dict[str, str | None], msg: str = "c") -> None:
    for rel, body in files.items():
        path = work / rel
        if body is None:
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    _git(work, "add", "-A")
    assert _git(work, "commit", "-qm", msg).returncode == 0


def _push_blocked(work: Path, tmp: Path) -> None:
    p = _git(work, "push", "-q", "origin", "main")
    assert p.returncode != 0
    assert "no local full-suite pass" in p.stderr
    assert _git(tmp / "remote.git", "rev-parse", "-q", "--verify", "main").returncode != 0


def test_declared_set_is_findings():
    assert _prefixes() == ["findings/"]


def test_findings_only_child_of_the_stamped_tree_pushes(tmp_path):
    work = _repo(tmp_path)
    _stamp(work)
    passed = _git(work, "rev-parse", "HEAD^{tree}").stdout.strip()
    _commit(work, {"findings/fb-1.json": "{}\n"})
    p = _git(work, "push", "-q", "origin", "main")
    assert p.returncode == 0, p.stderr
    assert passed[:12] in p.stderr


def test_non_findings_child_blocks(tmp_path):
    work = _repo(tmp_path)
    _stamp(work)
    _commit(work, {"b.txt": "x\n"})
    _push_blocked(work, tmp_path)


def test_mixed_child_blocks(tmp_path):
    work = _repo(tmp_path)
    _stamp(work)
    _commit(work, {"findings/fb-1.json": "{}\n", "b.txt": "x\n"})
    _push_blocked(work, tmp_path)


def test_rename_out_of_findings_blocks(tmp_path):
    work = _repo(tmp_path)
    _commit(work, {"findings/fb-1.json": "{}\n"}, "findings")
    _stamp(work)
    assert _git(work, "mv", "findings/fb-1.json", "fb-1.json").returncode == 0
    assert _git(work, "commit", "-qm", "rename out").returncode == 0
    _push_blocked(work, tmp_path)


def test_prefix_lookalikes_block(tmp_path):
    work = _repo(tmp_path)
    _stamp(work)
    _commit(work, {"findings.json": "{}\n"})
    _push_blocked(work, tmp_path)
    _commit(work, {"findingsX/a": "x\n"})
    _push_blocked(work, tmp_path)


def test_two_findings_only_steps_from_the_stamped_tree_push(tmp_path):
    work = _repo(tmp_path)
    _stamp(work)
    _commit(work, {"findings/fb-1.json": "{}\n"}, "one")
    _commit(work, {"findings/fb-2.json": "{}\n"}, "two")
    p = _git(work, "push", "-q", "origin", "main")
    assert p.returncode == 0, p.stderr


def test_stamp_whose_tree_object_is_missing_blocks(tmp_path):
    work = _repo(tmp_path)
    with open(work / ".git" / "prusik-suite-pass", "a", encoding="utf-8") as fh:
        fh.write("1" * 40 + "\n")
    _commit(work, {"findings/fb-1.json": "{}\n"})
    _push_blocked(work, tmp_path)


def test_unreadable_declared_file_blocks(tmp_path):
    work = _repo(tmp_path)
    _stamp(work)
    (work / "scripts" / "non-test-inputs.txt").unlink()
    _commit(work, {"findings/fb-1.json": "{}\n"})
    _push_blocked(work, tmp_path)


def _root_reads(tests_dir: Path, skip: Path) -> list[str]:
    root = r"(?:_REPO|_ROOT|REPO|ROOT|REPO_ROOT|PROJECT_ROOT|parents\[\d+\]|parent)"
    hits = []
    for pre in _prefixes():
        name = re.escape(pre.rstrip("/"))
        pat = re.compile(root + r"\s*(?:/\s*|,\s*)[\"']" + name + r"(?:/|[\"'])")
        for f in sorted(tests_dir.rglob("*.py")):
            if f.resolve() == skip:
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if pat.search(line):
                    hits.append(f"{f.name}:{n}: {line.strip()}")
    return hits


def test_guard_flags_a_repo_root_read_of_a_declared_prefix(tmp_path):
    (tmp_path / "test_x.py").write_text('p = _REPO / "findings" / "a.json"\n', encoding="utf-8")
    (tmp_path / "test_y.py").write_text('p = tmp_path / "findings"\n', encoding="utf-8")
    hits = _root_reads(tmp_path, Path(__file__).resolve())
    assert len(hits) == 1 and hits[0].startswith("test_x.py")


def test_no_test_reads_a_declared_prefix_from_the_repo_root():
    """The carry is only true while no test reads these paths; an indirect build can evade a
    textual scan, so this narrows the risk rather than removing it."""
    hits = _root_reads(_REPO / "tests", Path(__file__).resolve())
    assert not hits, "a test reads a declared non-test-input path from the repo root:\n" + "\n".join(hits)
