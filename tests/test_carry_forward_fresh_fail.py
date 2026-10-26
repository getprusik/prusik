"""fb-00c98b8c70c6 — a carried-forward verdict never overwrites a newer report.

Carry-forward fired at two exit-artifact sites: a missing approval file, and a present file
whose verdict line isn't the gate token. At the second, a reviewer FAIL written by the
reviewer-artifact fallback (nobody called record-verdict) was rewritten as "PASS (carried
forward)" from the older ledger PASS at the same hash. The newer evidence must win, at the
exit gate AND at `verdict-current` (every verdict role's Step 0) — otherwise the gate asks for a
re-run that Step 0 then skips, and the sprint stalls.

Mtimes are set with os.utime relative to the verdict's ledger ts, so nothing depends on timing.

moat-finding: fb-00c98b8c70c6
"""

from __future__ import annotations

import argparse
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from prusik import gate, ledger

F = "feat"

# (approval basename, role, judged artifact, gate token, negative verdict)
GATES = [
    ("scope-approval.txt", "scope-critic", f"design/{F}/scope.md", "APPROVED", "REJECTED"),
    ("plan-approval.txt", "plan-critic", f"design/{F}/plan.md", "APPROVED", "REJECTED"),
    ("regression.txt", "regression-sentinel", None, "PASS", "FAIL"),
    ("conventions.txt", "conventions-enforcer", None, "PASS", "FAIL"),
]
IDS = [g[0] for g in GATES]


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    files = {"src/app.py": "x = 1\n", "CLAUDE.md": "## Style\n",
             f"design/{F}/scope.md": "## Goal\nShip it.\n",
             f"design/{F}/plan.md": "## Modules touched\nsrc/app.py\n"}
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.test")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / ".sprint").mkdir()
    (tmp_path / "reports" / F).mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def _config(root: Path) -> None:
    """A sprint config naming each approval file as an exit artifact (one in the trivial set)."""
    def art(base, tok):
        return {"path": f"reports/{{feature}}/{base}", "must_contain": tok}
    cfg = {"version": 1, "phases": [
        {"name": "scoping", "exit_artifacts": [art("scope-approval.txt", "APPROVED")],
         "trivial_exit_artifacts": [{"path": "design/{feature}/trivial.md"}]},
        {"name": "planning", "exit_artifacts": [art("plan-approval.txt", "APPROVED")]},
        {"name": "reviewing", "exit_artifacts": [art("regression.txt", "PASS"),
                                                 art("conventions.txt", "PASS")]}]}
    (root / ".claude").mkdir(exist_ok=True)
    (root / ".claude" / "sprint-config.yaml").write_text(yaml.safe_dump(cfg))


def _record(role, artifact, verdict) -> float:
    """Record a verdict; return its ledger ts as epoch seconds."""
    assert gate.record_verdict(argparse.Namespace(role=role, feature=F, artifact=artifact,
                                                  verdict=verdict)) == 0
    v = [r for r in ledger.read_all() if r.get("event") == "critic_verdict"][-1]
    return datetime.fromisoformat(v["ts"]).timestamp()


def _current(role, artifact) -> int:
    return gate.verdict_current(argparse.Namespace(role=role, feature=F, artifact=artifact))


def _spec(base, tok) -> dict:
    return {"exit_artifacts": [{"path": f"reports/{{feature}}/{base}", "must_contain": tok}]}


def _report(root, base, text, mtime) -> Path:
    p = root / "reports" / F / base
    p.write_text(text)
    os.utime(p, (mtime, mtime))
    return p


# ---- the exit gate --------------------------------------------------------------------------

@pytest.mark.parametrize("base,role,artifact,tok,neg", GATES, ids=IDS)
def test_newer_report_is_never_overwritten(proj, base, role, artifact, tok, neg):
    ts = _record(role, artifact, tok)
    body = f"{neg}\n\nfresh finding: the change breaks X\n"
    p = _report(proj, base, body, ts + 10)
    missing = gate._unsatisfied_exit_artifacts(_spec(base, tok), F)
    assert missing, "a report newer than the recorded verdict must leave the gate unmet"
    assert p.read_text() == body, "the newer report must not be rewritten"
    assert f"re-run {role}" in missing[0]
    assert "delet" not in missing[0].lower(), "the remedy must never be deleting the report"
    assert not any(r.get("event") == "verdict_carried_forward" for r in ledger.read_all())


@pytest.mark.parametrize("base,role,artifact,tok,neg", GATES, ids=IDS)
def test_older_report_still_carries_forward(proj, base, role, artifact, tok, neg):
    ts = _record(role, artifact, tok)
    p = _report(proj, base, f"{neg}\n\nround-1 finding, since fixed\n", ts - 10)
    assert gate._unsatisfied_exit_artifacts(_spec(base, tok), F) == []
    assert gate._is_carry_marker(p.read_text())


@pytest.mark.parametrize("base,role,artifact,tok,neg", GATES, ids=IDS)
def test_missing_report_carries_forward(proj, base, role, artifact, tok, neg):
    _record(role, artifact, tok)
    assert gate._unsatisfied_exit_artifacts(_spec(base, tok), F) == []
    assert gate._is_carry_marker((proj / "reports" / F / base).read_text())


# ---- verdict-current (every verdict role's Step 0) -------------------------------------------

@pytest.mark.parametrize("base,role,artifact,tok,neg", GATES, ids=IDS)
def test_verdict_current_re_gates_on_a_newer_report(proj, capsys, base, role, artifact, tok,
                                                    neg):
    _config(proj)
    ts = _record(role, artifact, tok)
    _report(proj, base, f"{neg}\n", ts + 10)
    assert _current(role, artifact) == 1, "Step 0 must not skip the re-run the gate asks for"
    assert "newer" in capsys.readouterr().out


def test_verdict_current_carries_when_the_report_is_older(proj):
    _config(proj)
    ts = _record("regression-sentinel", None, "PASS")
    _report(proj, "regression.txt", "FAIL\n", ts - 10)
    assert _current("regression-sentinel", None) == 0


def test_verdict_current_without_config_is_unchanged(proj):
    ts = _record("regression-sentinel", None, "PASS")
    _report(proj, "regression.txt", "FAIL\n", ts + 10)
    assert _current("regression-sentinel", None) == 0


# ---- end to end: the block clears in-band ----------------------------------------------------

@pytest.mark.parametrize("rerun,clears", [("PASS", True), ("FAIL", False)])
def test_block_clears_only_by_a_recorded_rerun(proj, rerun, clears):
    _config(proj)
    role, base, spec = "regression-sentinel", "regression.txt", _spec("regression.txt", "PASS")
    ts = _record(role, None, "PASS")
    _report(proj, base, "FAIL\n\nunrecorded fallback report\n", ts + 10)
    assert gate._unsatisfied_exit_artifacts(spec, F)
    assert _current(role, None) == 1
    # The re-run Step 0 now allows: the role writes its report, then records its verdict.
    (proj / "reports" / F / base).write_text(f"{rerun}\n\nre-run\n")
    _record(role, None, rerun)
    assert (gate._unsatisfied_exit_artifacts(spec, F) == []) is clears
    assert (_current(role, None) == 0) is clears


# ---- execution evidence is unaffected --------------------------------------------------------

def test_evidence_check_ignores_a_newer_non_marker_report(proj):
    """_evidence_unsatisfied waives evidence only for a current carry MARKER; a fresh FAIL
    report is not one, so missing evidence still reads as missing (as before)."""
    ts = _record("regression-sentinel", None, "PASS")
    _report(proj, "regression.txt", "FAIL\n", ts + 10)
    reason = gate._evidence_unsatisfied(f"reports/{F}/regression.evidence.json", F, proj)
    assert reason and "no execution-evidence" in reason
