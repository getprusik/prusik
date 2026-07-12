"""A fix-round patches partial-mirror worktrees but the post-build worktree→root
assembly was not re-run, so the reviewer re-tested the STALE pre-fix-round root and
reported the same now-fixed defects forever — the unbreakable reviewing loop.
fix-round end now re-assembles.

The assembly criterion has been hardened across three findings, each fixing the prior
fix's blind spot — pinned here together so a future change can't regress either edge:
  - fb-db53b5d5d380: the loop itself (re-stage at fix-round end).
  - fb-bfc8ffdf0fd9: a worktree-local conftest STUB must NOT clobber canonical root.
  - fb-ba9d617d55cb: a CLEAN worktree deliverable whose root copy is stale must SYNC,
    even when it did not change DURING the round (the v0.134.0 round-delta scoping that
    fixed fb-bfc8ffdf0fd9 was too narrow and missed this — green worktree, red gate).
The current criterion is "stage every file that DIFFERS from root, minus drop-at-
integration stubs" — which satisfies all three.

A fourth finding hardened it further: the differ-from-root sync (fb-ba9d617d55cb) was
too BROAD — an UNMARKED worktree placeholder shadowing a pristine committed canonical
(a 1206-line tests/integration/conftest.py) was silently overwritten, cascading to 510
fixture-errors + DB schema corruption (fb-5bb5171810ee). The structural guard: a git-
PRISTINE root file (tracked AND unmodified vs HEAD — never a sprint target) is never
overwritten; only sprint-touched (untracked/modified) root files sync.

moat-finding: fb-db53b5d5d380
moat-finding: fb-bfc8ffdf0fd9
moat-finding: fb-ba9d617d55cb
moat-finding: fb-5bb5171810ee
"""

from __future__ import annotations

import json
import subprocess

import pytest

from prusik import consistency

# A worktree-local stub carries a drop-at-integration marker so it is never staged.
_STUB = ('"""This conftest is dropped at integration; the canonical '
         'tests/conftest.py takes over."""\nSTUB\n')


def _partial_mirror(root, role, files: dict[str, str]) -> None:
    base = root / "worktrees" / role
    for rel, content in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)


def test_assembly_stages_fixed_code_over_stale_root(tmp_path):
    # root has the PRE-fix-round (buggy) code; the worktree has the FIX.
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "cli.py").write_text("def run(): return 'BUG'\n")
    _partial_mirror(tmp_path, "backend", {"src/cli.py": "def run(): return 'FIXED'\n"})

    staged = consistency.assemble_worktrees_to_root(tmp_path)

    assert "src/cli.py" in staged
    # the reviewer reads root → now sees the FIXED code, not the stale defect
    assert (tmp_path / "src" / "cli.py").read_text() == "def run(): return 'FIXED'\n"


def test_orchestration_and_symlinks_are_not_staged(tmp_path):
    _partial_mirror(tmp_path, "backend", {
        "src/app.py": "ok\n",
        "reports/x/regression.txt": "PASS\n",     # orchestration — never staged
        ".sprint/state.json": "{}\n",             # orchestration — never staged
        "node_modules/dep/i.js": "x\n",           # dep dir — never staged
    })
    # a symlinked secret staged into the worktree must not be copied to root
    secret = tmp_path / "real.env"
    secret.write_text("PLAID=sk_live\n")
    (tmp_path / "worktrees" / "backend" / ".env").symlink_to(secret)

    staged = consistency.assemble_worktrees_to_root(tmp_path)

    assert staged == ["src/app.py"]
    assert not (tmp_path / "reports").exists()
    assert not (tmp_path / ".sprint" / "state.json").exists()
    assert not (tmp_path / ".env").exists()       # symlinked secret not propagated


def test_marked_stub_never_clobbers_canonical_root(tmp_path):
    """fb-bfc8ffdf0fd9 (adversarial): a worktree conftest STUB that self-declares it is
    dropped at integration must NOT overwrite the canonical root conftest — even though
    its content DIFFERS from root."""
    (tmp_path / "conftest.py").write_text("CANONICAL fixtures\n")   # tracked root file
    _partial_mirror(tmp_path, "backend", {"conftest.py": _STUB, "app.py": "FIXED\n"})

    staged = consistency.assemble_worktrees_to_root(tmp_path)

    assert "app.py" in staged                                      # real deliverable synced
    assert "conftest.py" not in staged                            # stub skipped
    assert (tmp_path / "conftest.py").read_text() == "CANONICAL fixtures\n"  # NOT clobbered


def test_stale_deliverable_unchanged_during_round_still_syncs(tmp_path):
    """fb-ba9d617d55cb (the regression of v0.134.0): a CLEAN worktree deliverable whose
    ROOT copy is stale/dirty must sync even though it did not change DURING the round.
    The old round-delta scoping skipped it → green worktree, red root gate, no path to
    reconcile. differs-from-root catches it."""
    (tmp_path / "tests").mkdir(parents=True)
    # root copy is the OLDER dirty version from the initial build assembly (has F841)
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    y = 1  # unused\n    assert True\n")
    # the worktree holds the CLEAN version, but it was written in the initial build —
    # NOT during this fix-round — so a round-delta snapshot would treat it as unchanged.
    _partial_mirror(tmp_path, "test-writer",
                    {"tests/test_x.py": "def test_x():\n    assert True\n"})
    baseline = consistency._worktree_file_hashes(tmp_path)        # nothing changes after

    staged = consistency.assemble_worktrees_to_root(tmp_path, baseline=baseline)

    assert "tests/test_x.py" in staged                            # synced despite no round-delta
    assert "unused" not in (tmp_path / "tests" / "test_x.py").read_text()  # root now clean


def test_in_sync_file_is_not_restaged(tmp_path):
    """A worktree file identical to root is a no-op (not reported as staged)."""
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "a.py").write_text("same\n")
    _partial_mirror(tmp_path, "backend", {"src/a.py": "same\n", "src/b.py": "new\n"})

    staged = consistency.assemble_worktrees_to_root(tmp_path)

    assert staged == ["src/b.py"]                                 # only the divergent file


def _git(d, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "-C", str(d), *args],
        capture_output=True, text=True, check=True)


def _has_git():
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


@pytest.mark.skipif(not _has_git(), reason="git not available")
def test_unmarked_stub_does_not_clobber_pristine_canonical(tmp_path):
    """fb-5bb5171810ee (the catastrophe): an UNMARKED worktree placeholder shadowing a
    pristine COMMITTED canonical (a large shared conftest) must NOT overwrite it — while
    a genuinely-stale deliverable (untracked at root, written by the initial assembly)
    must still sync. The git-pristine guard distinguishes them with no marker."""
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    # a large committed canonical conftest — the sprint never targets it
    canonical = "CANONICAL\n" + "\n".join(f"fixture_{i} = {i}" for i in range(1200)) + "\n"
    (root / "tests" / "integration").mkdir(parents=True)
    (root / "tests" / "integration" / "conftest.py").write_text(canonical)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "canonical fixtures")
    # the initial build assembly laid a STALE deliverable at root (untracked) ...
    (root / "tests" / "test_feature.py").write_text("def test_x():\n    y=1  # stale\n    assert 1\n")
    # ... whose CLEAN version is in the worktree; and the test-writer dropped an UNMARKED
    # 14-line placeholder that happens to shadow the canonical conftest path.
    _partial_mirror(root, "test-writer", {
        "tests/test_feature.py": "def test_x():\n    assert 1\n",       # clean deliverable
        "tests/integration/conftest.py": "# placeholder for my standalone tests\nx = 1\n",
    })

    staged = consistency.assemble_worktrees_to_root(root)

    # the pristine canonical survives — NOT clobbered by the unmarked placeholder
    assert (root / "tests" / "integration" / "conftest.py").read_text() == canonical
    assert "tests/integration/conftest.py" not in staged
    # the stale deliverable (untracked → not pristine) still syncs
    assert "stale" not in (root / "tests" / "test_feature.py").read_text()
    assert "tests/test_feature.py" in staged


@pytest.mark.skipif(not _has_git(), reason="git not available")
def test_sprint_modified_tracked_file_still_syncs(tmp_path):
    """A tracked file the sprint LEGITIMATELY changed (so its root copy is modified vs
    HEAD — not pristine) must still sync from the worktree; the guard only protects
    PRISTINE files."""
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "app.py").write_text("VERSION = 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    # the sprint modified app.py at root (initial assembly) → not pristine
    (root / "app.py").write_text("VERSION = 2  # stale partial edit\n")
    _partial_mirror(root, "backend", {"app.py": "VERSION = 3  # final\n"})

    staged = consistency.assemble_worktrees_to_root(root)

    assert "app.py" in staged
    assert (root / "app.py").read_text() == "VERSION = 3  # final\n"


def test_fix_round_end_syncs_stale_deliverable_and_skips_stub(tmp_path, monkeypatch):
    """End-to-end through fix_round.end (no baseline dependency): the stale deliverable
    syncs and the drop-at-integration stub is preserved — both findings at once."""
    from prusik import fix_round, ledger
    monkeypatch.setattr(ledger, "project_root", lambda: tmp_path)
    monkeypatch.setattr(ledger, "append", lambda *a, **k: None)
    (tmp_path / ".sprint").mkdir(parents=True)
    (tmp_path / "conftest.py").write_text("CANONICAL\n")
    (tmp_path / "app.py").write_text("STALE\n")                   # stale root deliverable
    _partial_mirror(tmp_path, "backend", {"conftest.py": _STUB, "app.py": "FIXED\n"})
    # the marker need not carry a baseline anymore — the criterion is differs-from-root
    fix_round._marker_path(tmp_path).write_text(json.dumps({
        "feature": "feat", "round": 1, "started_at": "2026-06-06T00:00:00+00:00"}))

    rc = fix_round.end("feat", root=tmp_path)
    assert rc == 0
    assert (tmp_path / "app.py").read_text() == "FIXED\n"          # stale deliverable synced
    assert (tmp_path / "conftest.py").read_text() == "CANONICAL\n"  # stub NOT propagated
