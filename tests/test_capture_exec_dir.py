"""fb-caff9937144e — `prusik gate capture` forced cwd=project-root, so a
worktree-scoped capture (`cd worktrees/solo && prusik gate capture -- <cmd>`)
ran <cmd> against MAIN while the evidence hash stamped the worktree: a laundered
wrong-tree green (or spurious wrong-tree red). The fix runs the command in the
invocation cwd when it is inside the project, records the exec dir as provenance,
and warns loudly when a worktree-mode sprint captured at root.

moat-finding: fb-caff9937144e
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from prusik import gate, schema


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=str(cwd), check=True, capture_output=True, text=True)


def _repo_with_worktree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / ".sprint").mkdir()
    (root / "src.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    solo = root / "worktrees" / "solo"
    _git(root, "worktree", "add", "-q", str(solo), "-b", "solo")
    return root, solo


# ---- unit: exec-dir resolution --------------------------------------------------

def test_resolve_exec_dir_honors_cwd_inside_project(tmp_path, monkeypatch):
    root, solo = _repo_with_worktree(tmp_path)
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(solo))
    assert gate._resolve_exec_dir(root).resolve() == solo.resolve()   # runs in the worktree


def test_resolve_exec_dir_falls_back_to_root_when_cwd_outside(tmp_path, monkeypatch):
    root, _ = _repo_with_worktree(tmp_path)
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(tmp_path.parent))  # outside repo
    assert gate._resolve_exec_dir(root).resolve() == root.resolve()   # never runs outside


# ---- unit: the wrong-tree warning fires only in the danger case -----------------

def test_wrong_tree_warning_only_worktree_mode_at_root(tmp_path):
    root, solo = _repo_with_worktree(tmp_path)
    assert gate._wrong_tree_warning(root, root) is not None           # worktrees + ran at root
    assert gate._wrong_tree_warning(solo, root) is None               # ran inside the worktree
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / ".sprint").mkdir()
    assert gate._wrong_tree_warning(plain, plain) is None             # no worktrees → root is fine


# ---- behavioral: the command actually runs in the worktree ----------------------

def test_capture_command_runs_in_worktree_not_root(tmp_path, monkeypatch):
    root, solo = _repo_with_worktree(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(solo))   # canonicalizes to root
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(solo))  # agent cd'd into the worktree
    rc = gate.capture(SimpleNamespace(
        command=["touch RAN_HERE.txt && echo '1 passed'"], reset=False,
        feature="feat", phase="regression", kind="tests"))
    assert rc == 0
    assert (solo / "RAN_HERE.txt").exists()               # ran in the worktree...
    assert not (root / "RAN_HERE.txt").exists()           # ...NOT against main (the bug)
    ev = schema.evidence_path_for(root / "reports" / "feat", "regression")
    assert schema.load_evidence(ev)[0]["exec_dir"] == "worktrees/solo"   # provenance recorded


def test_capture_at_root_records_dot_and_warns_in_worktree_mode(tmp_path, monkeypatch, capsys):
    root, _ = _repo_with_worktree(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(root))   # ran at root, worktrees exist
    rc = gate.capture(SimpleNamespace(
        command=["echo '1 passed'"], reset=False,
        feature="feat", phase="regression", kind="tests"))
    assert rc == 0                                        # warn, not refuse (root capture is legit sometimes)
    assert "PROJECT ROOT" in capsys.readouterr().err     # but the wrong-tree risk is named
    ev = schema.evidence_path_for(root / "reports" / "feat", "regression")
    assert schema.load_evidence(ev)[0]["exec_dir"] == "."
