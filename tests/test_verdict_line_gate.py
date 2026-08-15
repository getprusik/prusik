"""fb-d3c6dd0da1e6 — the reviewing exit gate bound regression.txt with
`must_contain: PASS`, but that was a substring-anywhere check: a stale FAIL-bodied
report whose fix-instructions merely MENTIONED "PASS" satisfied the gate and rode
into integrating. The verdict is the FIRST non-empty line by every role spec ("first
line must be exactly PASS/FAIL", "APPROVED/REJECTED"); the gate now matches the
leading token of that line, closing the hole for regression, conventions AND the
scope/plan APPROVED artifacts (which shared it — conventions only blocked by luck).

moat-finding: fb-d3c6dd0da1e6
"""

from __future__ import annotations

from prusik import gate


# A real FAIL-bodied regression report: the verdict is FAIL, but the body mentions
# "PASS" in prose and fix-instructions — exactly the shape that escaped the old check.
_FAIL_BODY_MENTIONING_PASS = (
    "FAIL\n\n"
    "3 tests failed in api/billing/retry.py.\n"
    "Fix them and re-run until the suite reports PASS, then re-capture.\n"
    "Legend: PASS = green, FAIL = red.\n"
)


# ---- the unit: leading-token, not substring-anywhere -----------------------------

def test_verdict_line_ok_rejects_a_fail_body_that_mentions_the_token():
    assert gate._verdict_line_ok("PASS\nall green\n", "PASS") is True
    assert gate._verdict_line_ok("PASS (carried forward — worktree unchanged)\n", "PASS") is True
    # THE ESCAPE: FAIL verdict, "PASS" only in the body → must NOT satisfy
    assert gate._verdict_line_ok(_FAIL_BODY_MENTIONING_PASS, "PASS") is False
    # leading blank lines are skipped to the first real line
    assert gate._verdict_line_ok("\n\nAPPROVED\n", "APPROVED") is True
    assert gate._verdict_line_ok("REJECTED\nreason: scope drift\n", "APPROVED") is False
    assert gate._verdict_line_ok("", "PASS") is False        # empty file never satisfies


# ---- the exit gate: a stale FAIL report cannot ride into integrating -------------

def _as_project(tmp_path, monkeypatch):
    """Resolve the project root the RUNTIME-AGNOSTIC way: the `.sprint` marker-walk
    from cwd, not the Claude-specific CLAUDE_PROJECT_DIR override. A non-Claude agent
    (Codex/Gemini/Cursor) never sets that env, so the gate must resolve the root from
    prusik's own on-disk markers — which is what this exercises."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    (tmp_path / ".sprint").mkdir(exist_ok=True)
    monkeypatch.chdir(tmp_path)


def test_reviewing_exit_gate_blocks_a_fail_body_that_contains_pass(tmp_path, monkeypatch):
    _as_project(tmp_path, monkeypatch)
    f = "feat"
    (tmp_path / "reports" / f).mkdir(parents=True)
    reg = tmp_path / "reports" / f / "regression.txt"
    spec = {"exit_artifacts": [
        {"path": "reports/{feature}/regression.txt", "must_contain": "PASS"}]}
    # No sprint state / ledger → no carry-forward can fire; a bare FAIL body that
    # contains "PASS" in prose must be reported unsatisfied.
    reg.write_text(_FAIL_BODY_MENTIONING_PASS)
    assert any("regression.txt" in m for m in gate._unsatisfied_exit_artifacts(spec, f))
    # a compliant first-line-PASS report satisfies it
    reg.write_text("PASS\nfull suite green\n")
    assert gate._unsatisfied_exit_artifacts(spec, f) == []


# ---- the pre-sprint gate shares the fix (same substring hole) --------------------

def test_pre_sprint_gate_blocks_a_fail_verdict_that_mentions_pass(tmp_path, monkeypatch):
    _as_project(tmp_path, monkeypatch)
    f = "feat"
    (tmp_path / "reports" / f).mkdir(parents=True)
    (tmp_path / "reports" / f / "brief-critique.txt").write_text(_FAIL_BODY_MENTIONING_PASS)
    config = {"pre_sprint_gates": {"brief_critique": {
        "enabled": True,
        "require_artifact": "reports/{feature}/brief-critique.txt",
        "must_contain": "PASS"}}}
    unmet = gate._check_pre_sprint_gates(config, f, tmp_path)
    assert any("brief-critique.txt" in u for u in unmet), unmet
