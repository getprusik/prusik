"""fb-b587d8d9b71c — the regression-capture false-clean that bit every solo sprint.

Two distinct root causes, both fixed here (the v0.185.0 close was premature — it only
made the *diagnosis message* kind-aware and never touched the behavior):

(a) WRONG ROOT. A reviewer subagent runs in a linked git worktree (`worktrees/solo`),
    which checks out `.claude/` from the branch — so `ledger.project_root()` stopped AT
    the worktree. Capture then wrote evidence to `worktrees/solo/reports/` (invisible to
    the root reviewing gate) and hashed a `worktrees/` set that doesn't exist under the
    worktree → a permanent tests=0 / "stale" bounce. project_root() now resolves a linked
    worktree to the sprint's canonical root, so write + read + hash agree from any cwd.

(b) TURBO CACHE REPLAY. `pnpm test` via turbo on a cache hit prints `>>> FULL TURBO` and
    re-emits/elides the cached output WITHOUT running the tool → the parsed count reads 0.
    Recording tests=0 false-blocked the advance gate as "nothing ran". Capture now REFUSES
    to record a cache replay (exit 1, with the force-fresh remedy) — turbo's banner is
    never accepted as a pass, and never logged as a false-clean.

moat-finding: fb-b587d8d9b71c
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from prusik import gate, ledger, schema


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _canonical_repo_with_solo_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A real sprint root (`.sprint/`, `.claude/`, `worktrees/`) plus a LINKED git
    worktree at `worktrees/solo` — exactly the shape a solo reviewer runs in."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / ".sprint").mkdir()
    (root / ".claude").mkdir()
    (root / ".claude" / "settings.json").write_text("{}\n")
    (root / "src.py").write_text("x = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    solo = root / "worktrees" / "solo"
    _git(root, "worktree", "add", "-q", str(solo), "-b", "solo")
    return root, solo


def test_project_root_resolves_linked_worktree_to_canonical(tmp_path, monkeypatch):
    root, solo = _canonical_repo_with_solo_worktree(tmp_path)
    # The reviewer's cwd/project-dir is the WORKTREE — the broken case.
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(solo))
    resolved = ledger.project_root().resolve()
    assert resolved == root.resolve()          # canonical root, where the gate reads
    assert resolved != solo.resolve()           # adversarial: NOT the worktree


def test_canonical_root_left_unchanged(tmp_path):
    """A non-worktree path (`.git` is a dir, or no git at all) must pass through
    untouched — the redirect fires ONLY for a genuine linked worktree."""
    root, _ = _canonical_repo_with_solo_worktree(tmp_path)
    assert ledger._canonical_worktree_root(root).resolve() == root.resolve()
    plain = tmp_path / "plain"
    plain.mkdir()
    assert ledger._canonical_worktree_root(plain) == plain


def test_capture_from_worktree_writes_evidence_to_canonical_root(tmp_path, monkeypatch):
    root, solo = _canonical_repo_with_solo_worktree(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(solo))   # reviewer runs in the worktree
    rc = gate.capture(SimpleNamespace(
        command=["echo '3 passed'"], reset=False,
        feature="feat", phase="regression", kind="tests"))
    assert rc == 0
    canonical_ev = schema.evidence_path_for(root / "reports" / "feat", "regression")
    worktree_ev = schema.evidence_path_for(solo / "reports" / "feat", "regression")
    assert canonical_ev.exists()                 # the root gate can SEE it
    assert not worktree_ev.exists()              # adversarial: NOT stranded in the worktree
    entries = schema.load_evidence(canonical_ev)
    assert entries and entries[0]["nonempty_primitive"]["value"] == 3


def test_turbo_cache_replay_is_refused_not_recorded(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    rc = gate.capture(SimpleNamespace(
        command=["echo '>>> FULL TURBO'"], reset=False,
        feature="feat", phase="regression", kind="tests"))
    assert rc == 1                               # fail closed — the tool did not run
    ev = schema.evidence_path_for(tmp_path / "reports" / "feat", "regression")
    assert not ev.exists()                       # the tests=0 false-clean is NOT recorded
    err = capsys.readouterr().err.lower()
    assert "full turbo" in err and "force" in err   # names the cache cause + the remedy


def test_cache_replay_with_real_count_still_records(tmp_path, monkeypatch):
    """Adversarial pair: a replay whose cached logs DID carry a real count (value>0) is
    the tool's own number, bound to the current hash — it must still be recorded, not
    discarded along with the elided-log case."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    rc = gate.capture(SimpleNamespace(
        command=["printf '>>> FULL TURBO\\n5 passed\\n'"], reset=False,
        feature="feat", phase="regression", kind="tests"))
    assert rc == 0
    ev = schema.evidence_path_for(tmp_path / "reports" / "feat", "regression")
    assert schema.load_evidence(ev)[0]["nonempty_primitive"]["value"] == 5


@pytest.mark.parametrize("text,expect", [
    (">>> FULL TURBO", True),
    ("cache hit, replaying logs abc123", True),
    ("cache hit, suppressing logs", True),
    ("Tests  5 passed (5)", False),
    ("5 passed in 0.3s", False),
])
def test_turbo_replay_detector(text, expect):
    from prusik import capture_diagnose
    assert capture_diagnose._is_turbo_cache_replay(text) is expect
