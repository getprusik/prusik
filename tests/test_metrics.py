"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: metrics (prusik/metrics.py) — defect-prevention scorecard from the
ledger. `compute` is pure, so we feed synthetic ledger records.

Run: uv run python -m pytest tests/test_metrics.py -v
"""

from tests._common import *  # noqa: F401,F403,E402
from tests._common import (  # noqa: F401,E402
    argparse, contextlib, io, json, os, re, shutil, subprocess, sys,
    tempfile, time, Path,
    schema, phases, triage, discovery, gate, watchdog, issues,
    kit_init, kit_uninstall, kit_toggle, consistency, agents_doctor,
    kit_refresh, kit_pause, kit_permissions, kit_brief_lint,
    kit_fix_round, kit_detect, kit_doctor, ledger_digest,
    _mktmp_project, _copy_sprint_config, _wt_file, _write_ledger,
    _capture_stdout, _capture_stderr, _VALID_BRIEF,
)

from prusik import metrics


def _ev(event, **f):
    return {"ts": "2026-05-01T00:00:00+00:00", "event": event, **f}


def _sample():
    return [
        _ev("reviewer_binding_flagged", feature="a"),
        _ev("reviewer_binding_flagged", feature="a"),
        _ev("reviewer_test_set_reach", feature="b"),
        _ev("reviewer_skip_flagged", feature="a"),
        _ev("reviewer_execution_verified", feature="a", ok=True),
        _ev("reviewer_execution_verified", feature="a", ok=False),   # caught
        _ev("reviewer_execution_verified", feature="b", ok=False),   # caught
        _ev("fix_round_start", feature="b", round=1),
        _ev("gate_blocked", tool="Write", target="api/x.py"),
        _ev("gate_blocked", tool="Bash", command="rm -rf /"),
        _ev("advance_blocked", from_phase="scoping", to_phase="building"),
        _ev("phase_rewind", feature="a"),
        _ev("verify_loop_checked", feature="a", t0_count=4, resolved=3, loop_closed=False),
        _ev("sprint_started", feature="a"),
        _ev("sprint_complete", feature="a"),
    ]


def test_caught_before_merge_counts():
    m = metrics.compute(_sample())
    c = m["caught_before_merge"]
    assert c["binding_mismatches"] == 2
    assert c["test_reach_gaps"] == 1
    assert c["suspect_skips"] == 1
    assert c["non_runs_or_failures_caught"] == 2  # two ok=False executions
    assert c["review_fix_rounds"] == 1
    assert m["headline_caught_before_merge"] == 2 + 1 + 1 + 2 + 1


def test_execution_evidence_split():
    m = metrics.compute(_sample())
    e = m["execution_evidence"]
    assert e["executions_verified"] == 3   # all reviewer_execution_verified
    assert e["non_runs_or_failures_caught"] == 2


def test_process_discipline_counts():
    m = metrics.compute(_sample())
    p = m["process_discipline"]
    assert p["out_of_phase_writes_blocked"] == 2
    assert p["premature_transitions_blocked"] == 1
    assert p["phase_rewinds"] == 1


def test_verify_loop_closure_rate():
    m = metrics.compute(_sample())
    vl = m["verify_loop"]
    assert vl["t0_findings"] == 4 and vl["resolved"] == 3
    assert vl["closure_rate_pct"] == 75


def test_by_feature_rollup():
    m = metrics.compute(_sample())
    # feature a: 2 binding + 1 skip + 1 non-run = 4; feature b: 1 reach + 1 non-run + 1 fix = 3
    assert m["by_feature"]["a"] == 4
    assert m["by_feature"]["b"] == 3


def test_empty_ledger_is_all_zero_not_crash():
    m = metrics.compute([])
    assert m["events_total"] == 0
    assert m["headline_caught_before_merge"] == 0
    assert m["verify_loop"]["closure_rate_pct"] is None


def test_closure_rate_none_when_no_t0():
    m = metrics.compute([_ev("sprint_started")])
    assert m["verify_loop"]["closure_rate_pct"] is None


# ---------- run() end-to-end against a real ledger file ----------

def test_run_reads_ledger_and_filters_since():
    tmp = _mktmp_project()
    try:
        sp = tmp / ".sprint"
        sp.mkdir(parents=True, exist_ok=True)
        with (sp / "ledger.jsonl").open("w") as f:
            f.write(json.dumps({"ts": "2026-04-01T00:00:00+00:00",
                                "event": "reviewer_binding_flagged", "feature": "old"}) + "\n")
            f.write(json.dumps({"ts": "2026-05-15T00:00:00+00:00",
                                "event": "reviewer_binding_flagged", "feature": "new"}) + "\n")
        # all-time: 2 binding flags
        out = _capture_stdout(lambda: metrics.run())
        assert "defect-prevention signal" in out
        # since cutoff: only the May one
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            metrics.run(since="2026-05-01T00:00:00+00:00", json_output=True)
        m = json.loads(buf.getvalue())
        assert m["caught_before_merge"]["binding_mismatches"] == 1
        assert m["since"] == "2026-05-01T00:00:00+00:00"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_run_json_shape():
    m = metrics.compute(_sample())
    # the JSON path just wraps compute() + since; spot-check serializability
    s = json.dumps({"since": None, **m})
    assert "headline_caught_before_merge" in s
