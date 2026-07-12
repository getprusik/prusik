"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: eval.

Run: uv run python -m pytest tests/test_eval.py -v
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


# ============================================================
# v0.21.0 — prusik eval suite (empirical substantiation)
# ============================================================

def test_v0210_eval_lists_corpus_cases():
    """`prusik eval list` discovers corpus cases in benchmarks/cases/. The
    first ship has 3 cases all grounded in observed trial defects."""
    from prusik import eval as kit_eval
    cases = kit_eval.list_cases()
    assert len(cases) >= 3, f"expected ≥3 corpus cases, got {len(cases)}"
    case_ids = {c["id"] for c in cases}
    assert "case-001-dev1-fetch-url-route-path" in case_ids
    assert "case-002-dev1-form-name-dropthrough" in case_ids
    assert "case-003-cross-touch-set-test-reach" in case_ids
    # Every case has a defect_class declared (not "?" placeholder)
    for c in cases:
        assert c["defect_class"] != "?", \
            f"case {c['id']} missing defect_class in expected-outcomes.yaml"
        assert c["trial_origin"] != "?", \
            f"case {c['id']} missing trial_origin (each case must be grounded)"


def test_v0210_eval_runs_all_cases_passing():
    """The shipped corpus + harness substantiates "checks fire on
    observed defect classes." All 3 cases must pass — if a check
    regresses, this test flags it."""
    from prusik import eval as kit_eval
    cases = kit_eval.list_cases()
    results = [kit_eval.run_case(c) for c in cases]
    failed = [r for r in results if not r["ok"]]
    assert not failed, (
        f"v0.21.0 corpus has falsifiable misses — eval is prusik's "
        f"own substantiation. Misses: "
        f"{[(r['case_id'], r['checks']) for r in failed]}"
    )


def test_v0210_eval_case_001_fetch_url_initial_flags_clean_does_not():
    """Case-001 (DEV-1 root #1): initial-repo must produce ≥1
    fetch_url finding; clean variant must produce 0 (FP control)."""
    from prusik import eval as kit_eval
    cases = [c for c in kit_eval.list_cases()
             if c["id"] == "case-001-dev1-fetch-url-route-path"]
    assert cases, "case-001 missing from corpus"
    result = kit_eval.run_case(cases[0])
    assert result["ok"], f"case-001 failed: {result['checks']}"
    check = result["checks"][0]
    assert check["initial_findings"] >= 1, \
        "case-001 must flag the fetch-URL on the buggy initial repo"
    assert check["clean_findings"] == 0, \
        "case-001 clean variant must NOT false-fire (FP control)"


def test_v0210_eval_case_003_cross_touch_set_finds_out_of_set_test():
    """Case-003: a test OUTSIDE the touched set referencing a route
    INSIDE the touched set must be flagged at reviewer time."""
    from prusik import eval as kit_eval
    cases = [c for c in kit_eval.list_cases()
             if c["id"] == "case-003-cross-touch-set-test-reach"]
    assert cases, "case-003 missing from corpus"
    result = kit_eval.run_case(cases[0])
    assert result["ok"], f"case-003 failed: {result['checks']}"


def test_v0210_eval_run_returns_nonzero_when_case_misses():
    """The runner returns rc=1 on any miss — falsifiable for CI. We
    can't easily mutate the corpus, so test that a case_filter pointing
    at a nonexistent case yields rc=1 (the no-match case)."""
    from prusik import eval as kit_eval
    rc = kit_eval.run(case_filter="nonexistent-case-xyz",
                       json_output=False)
    assert rc == 1, \
        "missing case_filter must return rc=1 (falsifiable signal for CI)"


def test_v0210_eval_run_returns_zero_when_all_pass():
    """All-pass case: rc=0. The runner's contract is binary — rc=0 for
    all-hit, rc=1 for any-miss; CI builds on this."""
    from prusik import eval as kit_eval
    rc = kit_eval.run(case_filter=None, json_output=True)
    # The shipped corpus passes; rc must be 0.
    assert rc == 0, \
        "shipped corpus must pass under run(); rc=1 means a real regression"


# ============================================================
# v0.25.0 — agent-control comparison (kit-on vs vibe-coding)
# ============================================================

def test_v0250_agent_control_runs_all_cases():
    """Agent-control mode runs through every corpus case + emits a
    per-case + aggregate result. Substantiates the 'kit catches what
    vibe-coding misses' framing."""
    from prusik import eval as kit_eval
    out = _capture_stdout(
        lambda: kit_eval.run_agent_control(case_filter=None,
                                            json_output=True))
    data = json.loads(out)
    assert "framing" in data
    assert "aggregate" in data
    assert "per_case" in data
    assert data["aggregate"]["total_cases"] >= 3
    # Every case has the expected per-case fields
    for c in data["per_case"]:
        assert "case_id" in c
        assert "defect_class" in c
        assert "vibe_coding_outcome" in c
        assert "kit_on_outcome" in c
        assert "kit_on_findings" in c


def test_v0250_agent_control_kit_on_catches_all_shipped_corpus():
    """The current shipped corpus must show 100% kit-on catch rate.
    If a regression breaks one of the checks, this test fires loudly —
    the falsifiable signal that the substantiation claim no longer holds."""
    from prusik import eval as kit_eval
    out = _capture_stdout(
        lambda: kit_eval.run_agent_control(case_filter=None,
                                            json_output=True))
    data = json.loads(out)
    total = data["aggregate"]["total_cases"]
    on = data["aggregate"]["kit_on_catches"]
    assert on == total, \
        (f"shipped corpus must show kit-on catches == total; got "
         f"{on}/{total}. Means a check regressed — investigate.")
    assert data["aggregate"]["improvement_rate"] == 1.0


def test_v0250_agent_control_kit_off_baseline_is_zero():
    """The kit-off baseline is 0 catches by construction (no detection
    runs without prusik). This is the framing's load-bearing premise:
    every defect that gets through this assumption ships unflagged."""
    from prusik import eval as kit_eval
    out = _capture_stdout(
        lambda: kit_eval.run_agent_control(case_filter=None,
                                            json_output=True))
    data = json.loads(out)
    assert data["aggregate"]["kit_off_catches"] == 0, \
        "kit-off baseline must always be 0 (no detection runs)"


def test_v0250_agent_control_returns_zero_on_all_catches():
    """rc=0 when kit-on catches everything; rc=1 when any case slips
    through. The binary CI signal matches `prusik eval run`'s contract."""
    from prusik import eval as kit_eval
    _capture_stdout(
        lambda: kit_eval.run_agent_control(case_filter=None,
                                            json_output=False))
    # Re-run capturing return code:
    rc = kit_eval.run_agent_control(case_filter=None, json_output=True)
    assert rc == 0, "agent-control on the shipped corpus must rc=0"


def test_v0250_agent_control_case_filter_works():
    """--case <prefix> narrows the run. Useful for CI loops or focused
    debugging."""
    from prusik import eval as kit_eval
    out = _capture_stdout(
        lambda: kit_eval.run_agent_control(case_filter="case-001",
                                            json_output=True))
    data = json.loads(out)
    assert data["aggregate"]["total_cases"] == 1
    assert data["per_case"][0]["case_id"].startswith("case-001")


def test_v0250_agent_control_per_case_includes_trial_origin():
    """Each case's trial_origin propagates to the agent-control output —
    the substantiation claim depends on these defects being REAL trial
    observations, not invented; the output must surface the provenance."""
    from prusik import eval as kit_eval
    out = _capture_stdout(
        lambda: kit_eval.run_agent_control(case_filter=None,
                                            json_output=True))
    data = json.loads(out)
    for c in data["per_case"]:
        assert c.get("trial_origin"), \
            f"case {c['case_id']} missing trial_origin — provenance is load-bearing"
        assert c["trial_origin"] != "?", \
            f"case {c['case_id']} has placeholder trial_origin"


