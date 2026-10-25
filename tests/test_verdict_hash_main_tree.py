"""fb-3fbf1846ac92 — a reviewer verdict must bind to the code it judged.

Reviewer verdicts and `gate capture` evidence were fingerprinted over `worktrees/` only. In a
main-tree build (no builder worktrees — every engine self-build) that is a fingerprint of
nothing, so a stale PASS survived any code change: verdict-current carried it forward, evidence
captured before the change read fresh, and the exit-artifact check could overwrite a reviewer's
FAIL report with "PASS (carried forward)". A carry marker written in an earlier cycle also
waived evidence forever, because only its text was checked, never its hash.

Adversarial cases are the point: a source edit after a recorded PASS, a stale carry marker left
on disk by a rewind, a FAIL report the carry-forward must not overwrite, and orchestration churn
(reports/, findings/, decisions/, a gitignored capture artifact) that must NOT re-gate.

moat-finding: fb-3fbf1846ac92
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pytest

from prusik import gate, ledger


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


def _init(root: Path, files: dict[str, str], *, git: bool = True, commit: bool = True) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    if git:
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "t@t.test")
        _git(root, "config", "user.name", "t")
        if commit and files:
            _git(root, "add", "-A")
            _git(root, "commit", "-qm", "init")


def _enter(root: Path, monkeypatch) -> Path:
    (root / ".sprint").mkdir(exist_ok=True)
    monkeypatch.chdir(root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    return root


def _h(root: Path) -> str:
    return gate._worktree_substantive_hash(root)


def _record(role="regression-sentinel", verdict="PASS") -> int:
    return gate.record_verdict(argparse.Namespace(role=role, feature="feat", artifact=None,
                                                  verdict=verdict))


def _current(role="regression-sentinel") -> int:
    return gate.verdict_current(argparse.Namespace(role=role, feature="feat", artifact=None))


def _rev_spec() -> dict:
    return {"exit_artifacts": [
        {"path": "reports/{feature}/regression.txt", "must_contain": "PASS"},
        {"path": "reports/{feature}/regression.evidence.json",
         "validator": "execution_evidence"}]}


# ---- mode boundary ---------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["absent", "empty", "file-only", "empty-teammate"])
def test_worktreeless_states_fingerprint_the_project_source(tmp_path, monkeypatch, state):
    _init(tmp_path, {"src/app.py": "x = 1\n"})
    if state != "absent":
        (tmp_path / "worktrees").mkdir()
    if state == "file-only":
        (tmp_path / "worktrees" / "README").write_text("stray\n")
    if state == "empty-teammate":
        (tmp_path / "worktrees" / "backend").mkdir()
    root = _enter(tmp_path, monkeypatch)
    before = _h(root)
    (root / "src" / "app.py").write_text("x = 2\n")
    assert _h(root) != before


def test_worktree_mode_is_unchanged(tmp_path, monkeypatch):
    _init(tmp_path, {"src/app.py": "x = 1\n"})
    wt = tmp_path / "worktrees" / "solo" / "src"
    wt.mkdir(parents=True)
    (wt / "app.py").write_text("x = 10\n")
    root = _enter(tmp_path, monkeypatch)
    before = _h(root)
    (root / "src" / "app.py").write_text("x = 2\n")          # root edit: not judged in worktree mode
    assert _h(root) == before
    (wt / "app.py").write_text("x = 11\n")                   # worktree edit: judged
    assert _h(root) != before


# ---- what main-tree mode covers ----------------------------------------------------------------

def test_project_in_a_repo_subdirectory(tmp_path, monkeypatch):
    _init(tmp_path, {"proj dir/src/app.py": "x = 1\n", "sibling/b.py": "y = 1\n"})
    root = _enter(tmp_path / "proj dir", monkeypatch)
    before = _h(root)
    (tmp_path / "sibling" / "b.py").write_text("y = 2\n")    # outside the project
    (root / "reports" / "feat").mkdir(parents=True)
    (root / "reports" / "feat" / "regression.txt").write_text("PASS\n")
    assert _h(root) == before
    (root / "src" / "app.py").write_text("x = 2\n")
    assert _h(root) != before


def test_non_ascii_filename_is_fingerprinted(tmp_path, monkeypatch):
    _init(tmp_path, {"src/über.py": "x = 1\n"})
    root = _enter(tmp_path, monkeypatch)
    before = _h(root)
    (root / "src" / "über.py").write_text("x = 2\n")
    assert _h(root) != before


def test_non_git_walk_never_enters_vcs_or_virtualenv(tmp_path, monkeypatch):
    _init(tmp_path, {"src/app.py": "x = 1\n", ".venv/lib/site.py": "v = 1\n",
                     ".hg/store/x": "h\n"}, git=False)
    root = _enter(tmp_path, monkeypatch)
    before = _h(root)
    (root / ".venv" / "lib" / "site.py").write_text("v = 2\n")
    (root / ".hg" / "store" / "x").write_text("h2\n")
    assert _h(root) == before
    (root / "src" / "app.py").write_text("x = 2\n")
    assert _h(root) != before


def test_orchestration_churn_and_ignored_capture_artifacts_keep_it_stable(tmp_path, monkeypatch):
    _init(tmp_path, {"src/app.py": "x = 1\n", ".gitignore": ".coverage\n"})
    root = _enter(tmp_path, monkeypatch)
    before = _h(root)
    for rel in ("reports/feat/regression.txt", "design/feat/scope.md", "findings/fb-x.json",
                "decisions/feat.json", "briefs/feat.md", ".sprint/state.json", ".coverage"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("churn\n")
    assert _h(root) == before


# ---- the stale-PASS holes ------------------------------------------------------------------------

def test_source_change_regates_verdict_current(tmp_path, monkeypatch):
    _init(tmp_path, {"src/app.py": "x = 1\n"})
    root = _enter(tmp_path, monkeypatch)
    assert _record() == 0
    assert _current() == 0
    (root / "src" / "app.py").write_text("x = 2\n")
    assert _current() == 1


def test_evidence_captured_before_a_source_change_reads_stale(tmp_path, monkeypatch):
    _init(tmp_path, {"src/app.py": "x = 1\n", ".gitignore": ".sprint/\n"})
    root = _enter(tmp_path, monkeypatch)
    rc = gate.capture(argparse.Namespace(feature="feat", phase="regression", kind="tests",
                                         command=["--", "echo 3 passed"]))
    assert rc == 0
    assert gate._evidence_unsatisfied("reports/feat/regression.evidence.json", "feat",
                                      root) is None
    (root / "src" / "app.py").write_text("x = 2\n")
    why = gate._evidence_unsatisfied("reports/feat/regression.evidence.json", "feat", root)
    assert why and "stale" in why


def test_fail_report_is_not_overwritten_after_a_source_change(tmp_path, monkeypatch):
    _init(tmp_path, {"src/app.py": "x = 1\n"})
    root = _enter(tmp_path, monkeypatch)
    assert _record() == 0                                  # an earlier round's PASS
    (root / "src" / "app.py").write_text("x = 2\n")        # the fix round changed code
    rep = root / "reports" / "feat" / "regression.txt"
    rep.parent.mkdir(parents=True)
    rep.write_text("FAIL\n2 regressions\n")
    missing = gate._unsatisfied_exit_artifacts(_rev_spec(), "feat")
    assert rep.read_text().startswith("FAIL")
    assert any("regression.txt" in m for m in missing), missing


def test_stale_carry_marker_satisfies_nothing_after_a_source_change(tmp_path, monkeypatch):
    _init(tmp_path, {"src/app.py": "x = 1\n"})
    root = _enter(tmp_path, monkeypatch)
    assert _record() == 0
    assert gate._try_carry_forward("reports/feat/regression.txt", "feat", root)  # marker written
    assert gate._unsatisfied_exit_artifacts(_rev_spec(), "feat") == []          # legit now
    (root / "src" / "app.py").write_text("x = 2\n")        # a rewind left the marker on disk
    missing = gate._unsatisfied_exit_artifacts(_rev_spec(), "feat")
    assert any("regression.txt" in m and "verdict line" in m for m in missing), missing
    assert any("execution-evidence" in m for m in missing), missing


def test_carry_prose_in_a_fresh_report_is_not_a_marker(tmp_path, monkeypatch):
    # Real adopter reports say "(carried forward, not merge-blocking)" in their body. A FRESH
    # PASS/APPROVED with that prose and no ledger verdict must stay satisfied, exactly as before.
    _init(tmp_path, {"src/app.py": "x = 1\n"})
    root = _enter(tmp_path, monkeypatch)
    rep = root / "reports" / "feat"
    rep.mkdir(parents=True)
    (rep / "conventions.txt").write_text(
        "PASS\n\n=== ADVISORIES (carried forward, not merge-blocking) ===\n- nit\n")
    (rep / "scope-approval.txt").write_text(
        "APPROVED\n\nNotes: risk R2 (carried forward from the brief-critic) is accepted.\n")
    spec = {"exit_artifacts": [
        {"path": "reports/{feature}/conventions.txt", "must_contain": "PASS"},
        {"path": "reports/{feature}/scope-approval.txt", "must_contain": "APPROVED"}]}
    assert gate._unsatisfied_exit_artifacts(spec, "feat") == []


@pytest.mark.parametrize("approval,role,artifact", [
    ("regression.txt", "regression-sentinel", None),
    ("conventions.txt", "conventions-enforcer", None),
    ("scope-approval.txt", "scope-critic", "design/feat/scope.md"),
])
def test_writer_output_round_trips_through_the_reader(tmp_path, monkeypatch, approval, role,
                                                       artifact):
    # The reader must recognise EXACTLY what the writer emits, for every approval gate — not
    # a hand-typed copy of the format (conventions review, fb-3fbf1846ac92).
    _init(tmp_path, {"src/app.py": "x = 1\n", "design/feat/scope.md": "## Goal recap\nx\n"})
    root = _enter(tmp_path, monkeypatch)
    verdict = "APPROVED" if artifact else "PASS"
    assert gate.record_verdict(argparse.Namespace(role=role, feature="feat", artifact=artifact,
                                                  verdict=verdict)) == 0
    marker = gate._approval_carried_forward(f"reports/feat/{approval}", "feat", root)
    assert marker and gate._is_carry_marker(marker + "\n")


@pytest.mark.parametrize("text,expected", [
    ("PASS (carried forward — built code unchanged since x @ ab12)\n", True),
    ("\n\nAPPROVED (carried forward — design/f/scope.md unchanged)\n", True),
    ("PASS\n=== ADVISORIES (carried forward, not merge-blocking) ===\n", False),
    ("FAIL\nPASS (carried forward — quoted from a prior round)\n", False),
    ("", False),
])
def test_only_the_engine_marker_shape_is_a_carry_marker(text, expected):
    assert gate._is_carry_marker(text) is expected


# ---- empty judged input --------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["regression-sentinel", "conventions-enforcer"])
def test_empty_judged_set_refuses_verdict_and_carry_forward(tmp_path, monkeypatch, capsys, role):
    _init(tmp_path, {}, commit=False)
    root = _enter(tmp_path, monkeypatch)
    (root / "reports" / "feat").mkdir(parents=True)        # orchestration only — nothing judged
    (root / "reports" / "feat" / "regression.txt").write_text("PASS\n")
    assert _record(role) == 2
    err = capsys.readouterr().err
    assert "no source files" in err and "gitignored" not in err   # cause named correctly
    approval = "regression.txt" if role == "regression-sentinel" else "conventions.txt"
    assert gate._approval_carried_forward(f"reports/feat/{approval}", "feat", root) is None
    assert not any(r.get("event") == "critic_verdict" for r in ledger.read_all())


def test_record_refusal_names_a_parent_repo_ignore(tmp_path, monkeypatch, capsys):
    _init(tmp_path, {".gitignore": "proj/\n"}, commit=True)
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "app.py").write_text("x = 1\n")
    _enter(proj, monkeypatch)
    assert _record() == 2
    assert "gitignored" in capsys.readouterr().err
