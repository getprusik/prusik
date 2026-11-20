"""fb-1f42f449f29d — the full-suite check counted capture proof only inside the current phase's
ledger window. A full suite captured green in `solo_execute` and reused by the reviewers drew
"no green full-suite tests run ... this reviewing phase" for a proven tree, and an in-window
capture stayed credited after the code changed. Capture proof is a statement about a tree:
it is credited through evidence entries whose hash is the tree being judged now.

moat-finding: fb-1f42f449f29d
"""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest
import yaml

from prusik import gate, schema, shared_infra, suite_baseline

FEATURE = "feat"
FULL = 906
ENTERED = "2026-06-05T12:00:00+00:00"
IN_WINDOW = "2026-06-05T12:30:00+00:00"
BEFORE = "2026-06-05T11:00:00+00:00"


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
    (r / ".claude").mkdir()
    (r / ".claude" / "sprint-config.yaml").write_text(
        yaml.safe_dump({"require_full_suite_at_build": True}))
    (r / ".gitignore").write_text("*.log\n.sprint/\nreports/\n.claude/\n")
    (r / "src.py").write_text("x = 1\n")
    (r / "CLAUDE.md").write_text("# rules\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    suite_baseline.update(r, FULL)
    _ledger(r, current="reviewing")
    return r


def _ledger(root: Path, *, current: str, events: list[dict] = ()) -> None:
    rows = [{"ts": ENTERED, "event": "phase_advance", "to_phase": current,
             "feature": FEATURE}, *events]
    (root / ".sprint" / "ledger.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows))


def _hash(root: Path, phase: str) -> str:
    role = gate._PHASE_ROLE.get(phase, "")
    return gate._worktree_substantive_hash(root, gate._REVIEWER_INPUTS.get(role, ()))


def _entry(root: Path, phase: str, *, value: int = FULL, exit_code: int = 0,
           kind: str = "tests", wt_hash: str | None = None, **extra) -> dict:
    return {"phase": phase, "command": "pytest -q", "exit_code": exit_code,
            "nonempty_primitive": {"kind": kind, "value": value},
            "output_sha": "0" * 16, "worktree_hash": wt_hash or _hash(root, phase),
            "exec_dir": ".", "captured_by": schema.EVIDENCE_CAPTURED_BY, **extra}


def _evidence(root: Path, phase: str, *entries: dict, feature: str = FEATURE) -> None:
    d = root / "reports" / feature
    d.mkdir(parents=True, exist_ok=True)
    schema.evidence_path_for(d, phase).write_text(json.dumps(
        {"schema_version": schema.EVIDENCE_SCHEMA_VERSION, "entries": list(entries)}))


def _run(current: str = "reviewing", target: str = "integrating"):
    out, err = io.StringIO(), io.StringIO()
    with mock.patch.object(shared_infra, "touched_shared_test_infra", return_value=[]), \
            redirect_stdout(out), redirect_stderr(err):
        rc = gate._full_suite_gate(current, target, FEATURE)
    return rc, out.getvalue() + err.getvalue()


def _change_code(root: Path) -> None:
    (root / "src.py").write_text("x = 2\n")


# ---- credited ---------------------------------------------------------------------------

def test_solo_capture_of_the_same_tree_is_credited_at_reviewing_exit(root):
    _evidence(root, "solo_execute", _entry(root, "solo_execute"))
    rc, text = _run()
    assert rc is None
    assert "ADVISORY" not in text and "BLOCKED" not in text


def test_conventions_entry_at_the_current_tree_is_credited(root):
    _evidence(root, "conventions", _entry(root, "conventions"))
    assert _run()[0] is None


def test_junit_entry_at_the_current_tree_is_credited(root):
    _evidence(root, "regression", _entry(root, "regression", junit_files=["r.xml"],
                                         observed_failures=0))
    assert _run()[0] is None


def test_current_entry_beside_an_older_tree_entry_is_credited(root):
    _evidence(root, "regression", _entry(root, "regression", wt_hash="f" * 16),
              _entry(root, "regression"))
    assert _run()[0] is None


def test_rewind_and_return_with_no_code_change_is_credited(root):
    _evidence(root, "regression", _entry(root, "regression"))
    _ledger(root, current="reviewing", events=[
        {"ts": "2026-06-05T12:20:00+00:00", "event": "phase_advance",
         "to_phase": "building", "feature": FEATURE},
        {"ts": "2026-06-05T12:40:00+00:00", "event": "phase_advance",
         "to_phase": "reviewing", "feature": FEATURE}])
    assert _run()[0] is None


def test_capture_at_the_current_tree_is_credited_at_building_exit(root):
    _ledger(root, current="building")
    _evidence(root, "solo_execute", _entry(root, "solo_execute"))
    assert _run("building", "reviewing")[0] is None


def test_proven_prove_run_in_window_is_still_credited(root):
    _ledger(root, current="reviewing", events=[
        {"ts": IN_WINDOW, "event": "prove_run", "kind": "tests", "proven": True,
         "executed": FULL}])
    assert _run()[0] is None


# ---- not credited -----------------------------------------------------------------------

def test_capture_before_a_code_change_is_not_credited(root):
    _evidence(root, "solo_execute", _entry(root, "solo_execute"))
    _change_code(root)
    rc, text = _run()
    assert rc == 2
    assert "no green full-suite tests run" in text


def test_in_window_ledger_record_of_an_older_tree_is_not_credited(root):
    _evidence(root, "regression", _entry(root, "regression"))
    _ledger(root, current="reviewing", events=[
        {"ts": IN_WINDOW, "event": "reviewer_execution_verified", "feature": FEATURE,
         "phase": "regression", "exit_code": 0, "nonempty": FULL, "kind": "tests",
         "ok": True}])
    _change_code(root)
    assert _run()[0] == 2


@pytest.mark.parametrize("name,kw", [
    ("red", {"exit_code": 1}),
    ("lint kind", {"kind": "lint"}),
    ("zero count", {"value": 0}),
])
def test_weaker_entries_at_the_current_tree_are_not_credited(root, name, kw):
    _evidence(root, "regression", _entry(root, "regression", **kw))
    assert _run()[0] == 2, f"{name} entry was credited"


def test_entry_without_captured_by_is_not_credited(root):
    e = _entry(root, "regression")
    del e["captured_by"]
    _evidence(root, "regression", e)
    assert _run()[0] == 2


def test_subset_at_the_current_tree_reads_as_a_subset(root):
    _evidence(root, "regression", _entry(root, "regression", value=133))
    rc, text = _run()
    assert rc == 2
    assert "looks like a subset" in text


def test_invalid_evidence_file_is_skipped(root):
    d = root / "reports" / FEATURE
    d.mkdir(parents=True)
    schema.evidence_path_for(d, "regression").write_text("not json")
    _evidence(root, "solo_execute", _entry(root, "solo_execute"))
    rc, text = _run()
    assert rc is None
    assert "check skipped" not in text


def test_another_features_reports_are_never_scanned(root):
    _evidence(root, "regression", _entry(root, "regression"), feature="other")
    assert _run()[0] == 2
