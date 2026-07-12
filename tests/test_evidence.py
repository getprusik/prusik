"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: evidence.

Run: uv run python -m pytest tests/test_evidence.py -v
Or run the whole suite: uv run python -m pytest tests/ -v

Shared helpers live in tests/_common.py (private; pytest does not
collect leading-underscore modules). v0.23.0 split tests/test_smoke.py
by domain to keep individual files navigable.
"""

# noqa: F401 — wildcard imports below intentionally re-export everything
# from _common (prusik modules, helpers, the tempfile/json/os toolbelt).
# F401 individual unused-name warnings would obscure the rest.
from tests._common import *  # noqa: F401,F403,E402
from tests._common import (  # noqa: F401,E402
    argparse, contextlib, io, json, os, re, shutil, subprocess, sys,
    tempfile, time, Path,
    schema, phases, triage, discovery, gate, watchdog, issues,
    kit_init, kit_uninstall, kit_toggle, consistency, agents_doctor,
    kit_refresh, kit_pause, kit_permissions, kit_brief_lint,
    kit_fix_round, kit_bridge, kit_detect, kit_doctor, ledger_digest,
    _mktmp_project, _copy_sprint_config, _wt_file, _write_ledger,
    _capture_stdout, _capture_stderr, _VALID_BRIEF,
)


# ---------- v0.12.0 — reviewer execution-evidence (Candidate F) ----------

def _cap(feature, phase, kind, shell):
    return gate.capture(argparse.Namespace(
        feature=feature, phase=phase, kind=kind, command=["--", shell]))


def _rev_spec():
    """The shipped reviewing exit-artifact contract (regression + its
    evidence; mirrors sprint-config.yaml)."""
    return {"exit_artifacts": [
        {"path": "reports/{feature}/regression.txt", "must_contain": "PASS"},
        {"path": "reports/{feature}/regression.evidence.json",
         "validator": "execution_evidence"}]}


def test_v0120_capture_writes_evidence_and_ledger():
    tmp = _mktmp_project()
    try:
        rc = _cap("feat", "regression", "tests", "echo 3 passed, 1 skipped")
        assert rc == 0
        ev = tmp / "reports" / "feat" / "regression.evidence.json"
        ok, errs = schema.validate_evidence_file(ev)
        assert ok, errs
        entries = schema.load_evidence(ev)
        # executed = passed + failed; skips DO NOT count (auto-skip closure)
        assert entries[0]["nonempty_primitive"] == {"kind": "tests", "value": 3}
        assert entries[0]["captured_by"] == "kit-gate-capture"
        from prusik.ledger import read_all
        rv = [r for r in read_all()
              if r.get("event") == "reviewer_execution_verified"]
        assert rv and rv[-1]["ok"] is True and rv[-1]["nonempty"] == 3
    finally:
        shutil.rmtree(tmp)


def test_v0120_capture_all_skip_yields_zero_executed():
    """The #13[00:13] / DEV-1 auto-skip false-clean, closed mechanically."""
    tmp = _mktmp_project()
    try:
        _cap("feat", "regression", "tests", "echo 0 passed, 9 skipped")
        entries = schema.load_evidence(
            tmp / "reports" / "feat" / "regression.evidence.json")
        assert entries[0]["nonempty_primitive"]["value"] == 0
    finally:
        shutil.rmtree(tmp)


def test_v0120_gate_blocks_pass_without_evidence():
    tmp = _mktmp_project()
    try:
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text("PASS\n")
        missing = gate._unsatisfied_exit_artifacts(_rev_spec(), "feat")
        assert any("execution-evidence" in m and "no execution-evidence" in m
                   for m in missing), missing
    finally:
        shutil.rmtree(tmp)


def test_v0120_gate_blocks_zero_executed_pass():
    tmp = _mktmp_project()
    try:
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text("PASS\n")
        _cap("feat", "regression", "tests", "echo 0 passed, 5 skipped")
        missing = gate._unsatisfied_exit_artifacts(_rev_spec(), "feat")
        # v0.185.0: the false-clean block now carries a kind-aware remediation
        assert any("nothing measurable ran" in m and "0 tests executed" in m
                   for m in missing), missing
    finally:
        shutil.rmtree(tmp)


def test_v0120_gate_blocks_errored_phase_pass():
    """R1: phase errored (exit≠0) but claimed PASS — false-clean held."""
    tmp = _mktmp_project()
    try:
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text("PASS\n")
        _cap("feat", "regression", "tests", "echo 7 passed && exit 4")
        missing = gate._unsatisfied_exit_artifacts(_rev_spec(), "feat")
        assert any("exit_code=4" in m for m in missing), missing
    finally:
        shutil.rmtree(tmp)


def test_v0120_gate_passes_with_valid_evidence():
    tmp = _mktmp_project()
    try:
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text("PASS\n")
        _cap("feat", "regression", "tests", "echo 11 passed")
        assert gate._unsatisfied_exit_artifacts(_rev_spec(), "feat") == []
    finally:
        shutil.rmtree(tmp)


def test_v0120_stale_evidence_blocks():
    """Evidence bound to the worktree hash: a rebuild invalidates it."""
    tmp = _mktmp_project()
    try:
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text("PASS\n")
        _wt_file(tmp, "solo", "a.py", "x = 1\n")
        _cap("feat", "regression", "tests", "echo 9 passed")
        assert gate._unsatisfied_exit_artifacts(_rev_spec(), "feat") == []
        _wt_file(tmp, "solo", "a.py", "x = 2\n")  # rebuild → hash changes
        missing = gate._unsatisfied_exit_artifacts(_rev_spec(), "feat")
        assert any("stale" in m for m in missing), missing
    finally:
        shutil.rmtree(tmp)


def test_evidence_freshness_ignores_orchestration_edits():
    """field retro #3: in a full git worktree (TS), editing design/ or .claude/ —
    orchestration the reviewer never reads — must NOT re-stale its evidence;
    only a CODE change does (the real-rebuild detector stays intact)."""
    tmp = _mktmp_project()
    try:
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text("PASS\n")
        _wt_file(tmp, "solo", "a.py", "x = 1\n")
        _wt_file(tmp, "solo", "design/feat/deviations.md", "## Deviations\n")
        _cap("feat", "regression", "tests", "echo 9 passed")
        assert gate._unsatisfied_exit_artifacts(_rev_spec(), "feat") == []
        # edit orchestration the reviewer never read → evidence stays FRESH
        _wt_file(tmp, "solo", "design/feat/deviations.md", "## Deviations\n- DEV-1\n")
        _wt_file(tmp, "solo", ".claude/settings.json", '{"permissions":{"allow":["X"]}}\n')
        assert gate._unsatisfied_exit_artifacts(_rev_spec(), "feat") == [], \
            "orchestration edit must not re-stale code evidence"
        # edit CODE → correctly goes stale
        _wt_file(tmp, "solo", "a.py", "x = 2\n")
        assert any("stale" in m
                   for m in gate._unsatisfied_exit_artifacts(_rev_spec(), "feat"))
    finally:
        shutil.rmtree(tmp)


def test_v0120_agent_written_evidence_rejected():
    """Anti-fabrication: a hand-written manifest (no prusik captured_by) fails."""
    tmp = _mktmp_project()
    try:
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text("PASS\n")
        (tmp / "reports" / "feat" / "regression.evidence.json").write_text(
            json.dumps({"schema_version": "1.0", "entries": [{
                "phase": "regression", "command": "pytest", "exit_code": 0,
                "nonempty_primitive": {"kind": "tests", "value": 999},
                "output_sha": "deadbeef", "worktree_hash": "whatever",
                "captured_by": "agent"}]}))
        missing = gate._unsatisfied_exit_artifacts(_rev_spec(), "feat")
        assert any("execution-evidence" in m for m in missing), missing
    finally:
        shutil.rmtree(tmp)


def test_v0120_carry_forward_coherence():
    """v0.11.0 #1 coherence: a carried paired verdict satisfies evidence
    without re-capture (re-requiring it would defeat the dominant-cost cure)."""
    tmp = _mktmp_project()
    try:
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text(
            "PASS (carried forward — built code substantively unchanged)\n")
        # No evidence.json on disk, yet the carried verdict satisfies it.
        missing = gate._unsatisfied_exit_artifacts(_rev_spec(), "feat")
        assert not any("execution-evidence" in m for m in missing), missing
        from prusik.ledger import read_all
        assert any(r.get("event") == "reviewer_execution_verified"
                   and r.get("carried") is True for r in read_all())
    finally:
        shutil.rmtree(tmp)


def test_v0120_role_specs_mandate_capture_wrapper():
    base = Path(__file__).parent.parent / "prusik" / "templates" / ".claude" / "agents"
    rs = (base / "regression-sentinel.md").read_text()
    ce = (base / "conventions-enforcer.md").read_text()
    assert "prusik gate capture" in rs and "--phase regression" in rs, rs[:0]
    assert "prusik gate capture" in ce and "--phase conventions" in ce, ce[:0]



# ---------- v0.18.0 — F §3.5 companion invariants ----------

def test_v0180_baseline_schema_allows_declared_baseline():
    """Schema accepts baseline with required domain+source+known_failures_count."""
    tmp = _mktmp_project()
    try:
        (tmp / "reports" / "feat").mkdir(parents=True)
        ev_path = tmp / "reports" / "feat" / "regression.evidence.json"
        ev_path.write_text(json.dumps({
            "schema_version": "1.0",
            "entries": [{
                "phase": "regression", "command": "pytest -q",
                "exit_code": 0,
                "nonempty_primitive": {"kind": "tests", "value": 200},
                "output_sha": "abc123def456",
                "worktree_hash": "wt000000",
                "captured_by": "kit-gate-capture",
                "baseline": {
                    "domain": "integration+behavior",
                    "source": "post-integration-gate",
                    "known_failures_count": 13,
                },
            }],
        }))
        ok, errs = schema.validate_evidence_file(ev_path)
        assert ok, f"valid baseline declaration must pass schema: {errs}"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0180_baseline_schema_rejects_partial_baseline():
    """Schema rejects baseline missing domain or source."""
    tmp = _mktmp_project()
    try:
        (tmp / "reports" / "feat").mkdir(parents=True)
        ev_path = tmp / "reports" / "feat" / "regression.evidence.json"
        ev_path.write_text(json.dumps({
            "schema_version": "1.0",
            "entries": [{
                "phase": "regression", "command": "pytest -q",
                "exit_code": 0,
                "nonempty_primitive": {"kind": "tests", "value": 200},
                "output_sha": "abc123def456",
                "worktree_hash": "wt000000",
                "captured_by": "kit-gate-capture",
                "baseline": {"known_failures_count": 0},  # no domain, no source
            }],
        }))
        ok, errs = schema.validate_evidence_file(ev_path)
        assert not ok
        assert any("baseline.domain" in e for e in errs)
        assert any("baseline.source" in e for e in errs)
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0180_gate_rejects_empty_baseline_without_scope():
    """Substantive gate rule: empty known_failures (==0) requires declared
    domain+source. Without them = blocked at reviewing exit (false-clean
    class #13 [00:13]: empty baseline from structurally-blind context)."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text("PASS\n")
        # Manually write evidence WITH empty baseline but no domain/source.
        # Schema would reject — but bypass that to test the gate-side check
        # (which must catch it independently as defense-in-depth).
        ev_path = tmp / "reports" / "feat" / "regression.evidence.json"
        # Compute the worktree hash so freshness check passes
        from prusik.gate import _worktree_substantive_hash
        wt = _worktree_substantive_hash(tmp)
        ev_path.write_text(json.dumps({
            "schema_version": "1.0",
            "entries": [{
                "phase": "regression", "command": "pytest -q",
                "exit_code": 0,
                "nonempty_primitive": {"kind": "tests", "value": 200},
                "output_sha": "abc",
                "worktree_hash": wt,
                "captured_by": "kit-gate-capture",
                # baseline with empty known_failures + no domain/source —
                # schema-invalid AND substantively a false-clean
                "baseline": {"known_failures_count": 0,
                             "domain": "", "source": ""},
            }],
        }))
        phase_spec = {"exit_artifacts": [
            {"path": "reports/{feature}/regression.txt", "must_contain": "PASS"},
            {"path": "reports/{feature}/regression.evidence.json",
             "validator": "execution_evidence"},
        ]}
        missing = gate._unsatisfied_exit_artifacts(phase_spec, "feat")
        # Either schema rejects it (invalid evidence manifest) OR the
        # substantive gate rule rejects it (empty baseline w/o scope).
        # Both are correct — both close the class.
        assert any(
            ("empty baseline" in m) or ("invalid evidence manifest" in m
                                          and "baseline" in m)
            for m in missing
        ), f"expected baseline-honesty rejection in: {missing}"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_failed_count_parses_observed_failures():
    from prusik import evidence
    assert evidence.failed_count("5 passed, 3 failed in 1.2s") == 3
    assert evidence.failed_count("Tests 730 passed | 2 failed\n") == 2
    # the file-count line is excluded (mirrors executed_count)
    assert evidence.failed_count("Test Files 1 failed\nTests 12 passed | 4 failed") == 4
    assert evidence.failed_count("10 passed in 0.3s") == 0


# moat-finding: fb-c871c831bf3b
# This is the deterministic backstop for the "builder claims all-pre-existing"
# finding: a builder can ASSERT a red test is inherited, but case (c) below proves
# the advance gate BLOCKS when observed reds exceed the baseline-proven set — so a
# NEW regression masquerading as pre-existing can't be laundered through. Paired
# with test_baseline.py::test_prove_REFUSES_to_launder_a_sprint_introduced_failure
# (a failure that passes on base can't be baselined in the first place).
def test_baseline_known_failures_satisfies_advance_only_when_proven():
    """field bridge #3: a FAILING tests capture is accepted at advance ONLY when its
    observed failures are git-stash-PROVEN pre-existing, declared with
    domain+source, and bounded (observed ≤ declared). An inline count WITHOUT the
    proof is still blocked (no laundering); a NEW failure beyond the baseline is
    blocked. Lets an honest pre-existing failure be DECLARED, not dropped."""
    from prusik import baseline
    from datetime import date
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "regression.txt").write_text("PASS\n")
        from prusik.gate import _worktree_substantive_hash
        wt = _worktree_substantive_hash(tmp)
        ev_rel = "reports/feat/regression.evidence.json"

        def _write(observed, declared):
            (tmp / ev_rel).write_text(json.dumps({
                "schema_version": "1.0",
                "entries": [{
                    "phase": "regression", "command": "pytest tests/rbac -q",
                    "exit_code": 1,
                    "nonempty_primitive": {"kind": "tests", "value": 200},
                    "observed_failures": observed,
                    "output_sha": "abc", "worktree_hash": wt,
                    "captured_by": "kit-gate-capture",
                    "baseline": {"known_failures_count": declared,
                                 "domain": "integration",
                                 "source": "starlette-1.0 harness drift (PR#123)"},
                }],
            }))

        # (a) declared but NOT git-stash-proven → still blocked, points to prove
        _write(12, 12)
        msg = gate._evidence_unsatisfied(ev_rel, "feat", tmp)
        assert msg and "baseline prove" in msg
        # (b) prove 12 pre-existing failures → the same capture now passes
        today = date.today()
        for i in range(12):
            baseline.add_entry(tmp, f"tests/rbac::test_{i}", proven_sha="dead",
                               note="starlette drift", days=30, today=today)
        assert gate._evidence_unsatisfied(ev_rel, "feat", tmp) is None
        # (c) a NEW failure beyond the declared baseline → blocked again
        _write(13, 12)
        assert gate._evidence_unsatisfied(ev_rel, "feat", tmp) is not None
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0180_parse_pytest_skips_two_formats():
    """The wrapper extracts skips from both pytest output formats."""
    from prusik.gate import _parse_pytest_skips
    # Format 1: SKIPPED [N] path:line: reason
    out1 = "SKIPPED [1] tests/test_x.py:42: not yet wired"
    skips1 = _parse_pytest_skips(out1)
    assert len(skips1) == 1
    assert skips1[0]["reason"] == "not yet wired"
    assert "tests/test_x.py" in skips1[0]["location"]
    # Format 2: id SKIPPED (reason)
    out2 = "tests/test_y.py::test_thing SKIPPED (TODO: awaiting Phase 5)"
    skips2 = _parse_pytest_skips(out2)
    assert len(skips2) == 1
    assert "awaiting Phase 5" in skips2[0]["reason"]


def test_v0180_falsifiable_skip_flag_fires_on_absence_claim_with_repo_match():
    """A skip whose reason says 'X not yet wired' and X IS in the repo
    gets flagged. Environmental skips ('requires postgres') do NOT flag."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Plant a real file the skip reason will name
        (tmp / "src").mkdir(exist_ok=True)
        (tmp / "src" / "mobile_capture.py").write_text(
            "def capture():\n    return 'wired'\n")
        from prusik.gate import _falsifiable_skip_reasons
        skips = [
            # Absence-claim + repo match → flag
            {"test_id": "t1", "reason": "mobile_capture.py not yet wired",
             "location": "tests/x.py:1"},
            # Environmental skip → NO flag (no absence-phrase)
            {"test_id": "t2", "reason": "requires postgres",
             "location": "tests/y.py:1"},
        ]
        flagged = _falsifiable_skip_reasons(tmp, skips)
        ids = {f["test_id"] for f in flagged}
        assert "t1" in ids, "absence-claim + repo match should be flagged"
        assert "t2" not in ids, "environmental skip should NOT be flagged"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


