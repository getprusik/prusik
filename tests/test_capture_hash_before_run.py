"""fb-97beccfb14a4 — `prusik gate capture` hashed the judged tree AFTER the command ran, so
a change landing mid-run was stamped onto output produced by the code before it (usage-rollup:
a fix edited three files during the sentinel's full-suite capture, the entry carried the
post-fix hash, and verdict-current carried the pre-fix PASS). The capture now snapshots the
tree before and after; a difference is refused by the `worktree_changed_mid_run` detector.

moat-finding: fb-97beccfb14a4
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from prusik import capture_diagnose as cd
from prusik import gate, ledger, schema

FEATURE = "feat"
PHASE = "regression"


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / ".sprint").mkdir()
    (r / ".gitignore").write_text(".coverage\n*.log\n.sprint/\nreports/\n")
    (r / "src.py").write_text("x = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(r))
    return r


def _capture(cmd: str, reset: bool = False) -> int:
    return gate.capture(SimpleNamespace(command=[cmd], reset=reset, feature=FEATURE,
                                        phase=PHASE, kind="tests"))


def _ev(root: Path) -> Path:
    return schema.evidence_path_for(root / "reports" / FEATURE, PHASE)


def _hash(root: Path) -> str:
    role = gate._PHASE_ROLE.get(PHASE, "")
    return gate._worktree_substantive_hash(root, gate._REVIEWER_INPUTS.get(role, ()))


# ---- the bug: a mid-run edit is refused, never stamped ----------------------------

@pytest.mark.parametrize("tail", ["echo '1 passed'", "echo '1 failed'; exit 1"])
def test_mid_run_edit_is_refused_green_or_red(root, capsys, tail):
    rc = _capture(f"echo 'x = 2' > src.py; {tail}")
    assert rc == 1
    assert not _ev(root).exists()                         # no entry recorded
    err = capsys.readouterr().err
    assert "src.py" in err and "re-capture" in err and "gitignore" in err
    modes = [r.get("mode") for r in ledger.read_all()
             if r.get("event") == "capture_non_evidence"]
    assert modes == ["worktree_changed_mid_run"]


def test_refused_capture_leaves_nothing_current_to_carry(root):
    _capture("echo 'x = 2' > src.py; echo '1 passed'")
    rel = str(_ev(root).relative_to(root))
    assert gate._evidence_unsatisfied(rel, FEATURE, root) is not None


@pytest.mark.parametrize("reset", [False, True])
def test_prior_evidence_survives_a_refusal(root, reset):
    assert _capture("echo '1 passed'") == 0
    before = _ev(root).read_bytes()
    assert _capture("echo 'x = 3' > src.py; echo '1 passed'", reset=reset) == 1
    assert _ev(root).read_bytes() == before               # byte-identical


# ---- no false refusals ------------------------------------------------------------

def test_unchanged_tree_records_the_pre_run_hash(root):
    pre = _hash(root)
    assert _capture("echo '1 passed'") == 0
    (entry,) = schema.load_evidence(_ev(root))
    assert entry["worktree_hash"] == pre


def test_gitignored_writes_are_still_recorded(root):
    pre = _hash(root)
    assert _capture("echo cov > .coverage; echo log > run.log; echo '1 passed'") == 0
    (entry,) = schema.load_evidence(_ev(root))
    assert entry["worktree_hash"] == pre


# ---- the detector, through the registry -------------------------------------------

def _result(paths: tuple[str, ...], exit_code: int = 0) -> cd.CaptureResult:
    return cd.CaptureResult(kind="tests", exit_code=exit_code, value=3, output="3 passed",
                            command="pytest", changed_paths=paths)


def test_detector_names_at_most_five_paths_and_counts_the_rest():
    paths = tuple(f"m{i}.py" for i in range(7))
    v = cd.diagnose(_result(paths))
    assert v is not None and v.mode == "worktree_changed_mid_run" and v.exit_code == 1
    assert all(f"m{i}.py" in v.remedy for i in range(5))
    assert "m5.py" not in v.remedy and "+2 more" in v.remedy


def test_detector_is_silent_on_an_unchanged_tree():
    assert cd.diagnose(_result(())) is None
    assert cd.diagnose(cd.CaptureResult(kind="tests", exit_code=0, value=3,
                                        output="3 passed", command="pytest")) is None
    assert "worktree_changed_mid_run" in cd.KNOWN_MODES
    assert len(cd._DETECTORS) == len(cd.KNOWN_MODES)
