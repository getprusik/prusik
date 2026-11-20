"""fb-d39ef32820e4 — a green full-suite capture recorded under `solo_execute` was ignored by the
reviewing gate, which reads only `regression.evidence.json`, so regression-sentinel re-ran the
identical command on the same tree hash and exec dir (red-on-fork-point: a second 4527-test
run, ~4m38s). Evidence is identified by command, kind, exec dir and judged tree hash, not by
the phase it was filed under: a capture matching another phase's green entry reuses it.

moat-finding: fb-d39ef32820e4
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from prusik import gate, ledger, schema

FEATURE = "feat"
RUN = "echo r >> run.log; echo '3 passed'"


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
    (r / ".gitignore").write_text("*.log\n.sprint/\nreports/\n")
    (r / "src.py").write_text("x = 1\n")
    (r / "CLAUDE.md").write_text("# rules\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(r))
    return r


def _capture(phase: str, cmd: str | None = RUN, reset: bool = False, **extra) -> int:
    return gate.capture(SimpleNamespace(command=[cmd] if cmd else [], reset=reset,
                                        feature=FEATURE, phase=phase, kind="tests",
                                        **extra))


def _ev(root: Path, phase: str) -> Path:
    return schema.evidence_path_for(root / "reports" / FEATURE, phase)


def _runs(root: Path, name: str = "run.log") -> int:
    p = root / name
    return len(p.read_text().splitlines()) if p.exists() else 0


# ---- the bug: an identical green capture is re-run ------------------------------------

def test_green_solo_capture_is_reused_by_regression(root, capsys):
    assert _capture("solo_execute") == 0
    assert _capture("regression") == 0
    assert _runs(root) == 1                                 # the command ran once
    (entry,) = schema.load_evidence(_ev(root, "regression"))
    assert entry["phase"] == "regression"
    assert entry["reused_from"] == {"phase": "solo_execute"}
    assert entry["nonempty_primitive"] == {"kind": "tests", "value": 3}
    rel = str(_ev(root, "regression").relative_to(root))
    assert gate._evidence_unsatisfied(rel, FEATURE, root) is None
    out = capsys.readouterr().out
    assert "solo_execute" in out and "not re-run" in out


def test_reuse_preserves_the_source_and_earlier_entries(root):
    assert _capture("regression", "echo q >> other.log; echo '2 passed'") == 0
    assert _capture("solo_execute") == 0
    src_before = _ev(root, "solo_execute").read_bytes()
    assert _capture("regression") == 0
    assert _ev(root, "solo_execute").read_bytes() == src_before   # byte-identical
    entries = schema.load_evidence(_ev(root, "regression"))
    assert [e["command"] for e in entries] == ["echo q >> other.log; echo '2 passed'", RUN]
    assert "reused_from" not in entries[0]
    assert entries[1]["reused_from"] == {"phase": "solo_execute"}
    assert _runs(root) == 1


def test_reuse_is_on_the_ledger(root):
    assert _capture("solo_execute") == 0
    assert _capture("regression") == 0
    recs = [r for r in ledger.read_all()
            if r.get("event") == "reviewer_execution_verified" and r.get("phase") == "regression"]
    assert len(recs) == 1
    assert recs[0]["reused_from"] == "solo_execute"
    assert recs[0]["ok"] is True and recs[0]["nonempty"] == 3


# ---- no false reuse ---------------------------------------------------------------------

def test_changed_code_runs_again(root):
    assert _capture("solo_execute") == 0
    (root / "src.py").write_text("x = 2\n")
    assert _capture("regression") == 0
    assert _runs(root) == 2
    (entry,) = schema.load_evidence(_ev(root, "regression"))
    assert "reused_from" not in entry


def test_red_source_is_not_reused(root):
    red = "echo r >> run.log; echo '1 failed, 2 passed'; exit 1"
    assert _capture("solo_execute", red) == 1
    assert _capture("regression", red) == 1
    assert _runs(root) == 2


def test_different_command_is_not_reused(root):
    assert _capture("solo_execute") == 0
    assert _capture("regression", "echo r >> run.log; echo '3 passed' ") == 0
    assert _runs(root) == 2


def test_phase_with_extra_judged_inputs_is_not_reused(root):
    assert _capture("solo_execute") == 0
    assert _capture("conventions") == 0                     # its hash includes CLAUDE.md
    assert _runs(root) == 2


def test_baseline_request_is_not_reused(root):
    assert _capture("solo_execute") == 0
    assert _capture("regression", baseline_known_failures=0, baseline_domain="d",
                    baseline_source="s") == 0
    assert _runs(root) == 2


def test_same_phase_recapture_runs_again(root):
    assert _capture("solo_execute") == 0
    assert _capture("solo_execute") == 0
    assert _runs(root) == 2


def test_junit_source_entry_is_not_reused(root):
    assert _capture("solo_execute") == 0
    p = _ev(root, "solo_execute")
    data = json.loads(p.read_text())
    data["entries"][0]["junit_files"] = []
    p.write_text(json.dumps(data))
    assert _capture("regression") == 0
    assert _runs(root) == 2


def test_invalid_source_file_is_ignored(root):
    p = _ev(root, "solo_execute")
    p.parent.mkdir(parents=True)
    p.write_text("not json")
    assert _capture("regression") == 0
    assert _runs(root) == 1


def test_reset_never_reuses(root):
    assert _capture("solo_execute") == 0
    assert _capture("regression", reset=True) == 0
    assert _runs(root) == 2
    (entry,) = schema.load_evidence(_ev(root, "regression"))
    assert "reused_from" not in entry
    assert _capture("regression", cmd=None, reset=True) == 0
    assert schema.load_evidence(_ev(root, "regression")) == []
    assert _runs(root) == 2
