"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: ci-comment (kit/ci_comment.py — formats scan/verify-loop/findings
--json output as GitHub PR-comment markdown for the composite Action).

Run: uv run python -m pytest tests/test_ci_comment.py -v
Or run the whole suite: uv run python -m pytest tests/ -v
"""

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

from prusik import ci_comment


# --- dispatch / shape detection -------------------------------------------

def test_scan_with_findings_renders_flag_headers():
    data = {
        "root": "/repo",
        "stats": {"total_files": 12},
        "total": 1,
        "detectors": ["binding"],
        "findings": [{
            "detector": "binding", "class": "fetch_url", "severity": "medium",
            "message": "fetch mismatch", "file": "templates/x.html", "line": 7,
            "expected": ["/api/user"], "suggested_test": None,
            "meta": {"url": "/api/usre"},
        }],
    }
    md = ci_comment.format_comment(data)
    assert "prusik scan — 1 flag(s)" in md
    assert "fetch-URL ↔ route-path mismatches (1)" in md
    assert "templates/x.html:7" in md
    assert "/api/user" in md
    assert "decision-support" in md  # mission boundary line preserved


def test_scan_clean_renders_checkmark():
    data = {"root": "/repo", "stats": {"total_files": 9},
            "total": 0, "detectors": ["binding"], "findings": []}
    md = ci_comment.format_comment(data)
    assert "✅ prusik scan — clean" in md
    assert "9" in md


def test_scan_no_files_early_return_shape_is_not_unrecognized():
    """scan's no-scannable-files branch emits {root, stats, findings: [],
    message} — no binding/total/schema_version keys. It must still route to
    the scan formatter, not the raw-JSON fallback."""
    data = {"root": "/repo", "findings": [],
            "stats": {"total_files": 0}, "message": "no scannable files"}
    md = ci_comment.format_comment(data)
    assert "unrecognized input" not in md
    assert "✅ prusik scan — clean" in md


def test_scan_truncation_warning_surfaces():
    data = {"root": "/repo",
            "stats": {"total_files": 5000, "truncated": True, "file_limit": 5000},
            "total": 0, "detectors": ["binding"], "findings": []}
    md = ci_comment.format_comment(data)
    assert "truncated" in md


def test_verify_loop_closed_renders_checkmark():
    data = {
        "schema_version": "1.0",
        "aggregate": {"t0_findings": 2, "resolved": 2, "loop_closed": True},
        "per_finding": [
            {"id": "a", "status": "resolved"},
            {"id": "b", "status": "resolved"},
        ],
    }
    md = ci_comment.format_comment(data)
    assert "✅ prusik verify-loop — closed end-to-end (2/2)" in md


def test_verify_loop_partial_renders_warning_and_status_groups():
    data = {
        "schema_version": "1.0",
        "aggregate": {"t0_findings": 2, "resolved": 1, "loop_closed": False},
        "per_finding": [
            {"id": "a", "status": "resolved"},
            {"id": "b", "status": "still-present"},
        ],
    }
    md = ci_comment.format_comment(data)
    assert "⚠ prusik verify-loop — partial (1/2)" in md
    assert "still-present (1)" in md
    assert "resolved (1)" in md


def test_verify_loop_no_t0_findings():
    data = {"schema_version": "1.0",
            "aggregate": {"t0_findings": 0, "resolved": 0, "loop_closed": False},
            "per_finding": []}
    md = ci_comment.format_comment(data)
    assert "no T0 findings" in md


def test_verify_loop_with_schema_version_not_misrouted_to_findings():
    """Both verify-loop and findings carry schema_version. verify-loop has no
    top-level 'findings' key, so the aggregate check must win."""
    data = {"schema_version": "1.0",
            "aggregate": {"t0_findings": 1, "resolved": 0, "loop_closed": False},
            "per_finding": [{"id": "a", "status": "still-present"}]}
    md = ci_comment.format_comment(data)
    assert "verify-loop" in md
    assert "prusik findings" not in md


def test_findings_renders_severity_and_action():
    data = {
        "schema_version": "1.0",
        "findings": [{
            "kind": "binding_mismatch",
            "severity": "high",
            "summary": "fetch /api/usre has no matching route",
            "suggested_action": "change to /api/user",
        }],
    }
    md = ci_comment.format_comment(data)
    assert "prusik findings — 1 actionable" in md
    assert "[high] binding_mismatch" in md
    assert "change to /api/user" in md


def test_findings_empty_renders_none():
    data = {"schema_version": "1.0", "findings": []}
    md = ci_comment.format_comment(data)
    assert "✅ prusik findings — none" in md


def test_unrecognized_shape_falls_back_to_fenced_json():
    data = {"something": "unexpected"}
    md = ci_comment.format_comment(data)
    assert "unrecognized input" in md
    assert "```json" in md
    assert "unexpected" in md


# --- overflow / details collapse ------------------------------------------

def test_overflow_findings_collapse_into_details():
    findings = [{
        "detector": "binding", "class": "form_name", "severity": "medium",
        "message": "m", "file": f"t{i}.html", "line": i, "expected": ["k"],
        "suggested_test": None, "meta": {"name": f"f{i}"},
    } for i in range(8)]  # > _INLINE_LIMIT (5)
    data = {"root": "/r", "stats": {"total_files": 8}, "total": 8,
            "detectors": ["binding"], "findings": findings}
    md = ci_comment.format_comment(data)
    assert "<details>" in md
    assert "and 3 more flag(s)" in md  # 8 - 5 inline


def test_suggested_test_scaffold_rendered_as_code_block():
    findings = [{
        "detector": "binding", "class": "fetch_url", "severity": "medium",
        "message": "m", "file": "t.html", "line": 1, "expected": ["/y"],
        "suggested_test": {"stack": "python", "code": "def test_x():\n    assert True"},
        "meta": {"url": "/x"},
    }]
    data = {"root": "/r", "stats": {"total_files": 1}, "total": 1,
            "detectors": ["binding"], "findings": findings}
    md = ci_comment.format_comment(data)
    assert "```python" in md
    assert "def test_x():" in md


# --- run() CLI entry ------------------------------------------------------

def test_run_reads_from_path():
    data = {"schema_version": "1.0", "findings": []}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        out = _capture_stdout(lambda: ci_comment.run(path))
        assert "prusik findings — none" in out
    finally:
        os.unlink(path)


def test_run_bad_path_returns_rc2():
    rc = ci_comment.run("/nonexistent/findings.json")
    assert rc == 2


def test_run_invalid_json_stdin_returns_rc2(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        rc = ci_comment.run(None)
    assert rc == 2
    assert "invalid JSON" in err.getvalue()


def test_run_always_rc0_on_valid_input():
    """The Action — not the formatter — decides what to do with findings, so
    run() returns 0 on any well-formed JSON regardless of finding count."""
    data = {"root": "/r", "stats": {"total_files": 1}, "total": 1,
            "detectors": ["binding"],
            "findings": [{"detector": "binding", "class": "fetch_url",
                          "severity": "medium", "message": "m", "file": "t.html",
                          "line": 1, "expected": ["/y"], "suggested_test": None,
                          "meta": {"url": "/x"}}]}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        path = f.name
    try:
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            rc = ci_comment.run(path)
        assert rc == 0
        assert "1 flag(s)" in out_buf.getvalue()
    finally:
        os.unlink(path)


# --- integration: real scan output round-trips through the formatter ------

def test_real_scan_json_round_trips_through_formatter():
    """Run an actual `prusik scan --json` and confirm the formatter accepts the
    real contract (guards against the formatter drifting from scan's shape)."""
    from prusik import scan as kit_scan
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        (td_path / "src").mkdir()
        (td_path / "src" / "noop.py").write_text("def f(): return 1\n")
        out = _capture_stdout(
            lambda: kit_scan.scan(root=td_path, json_output=True))
        data = json.loads(out)
        md = ci_comment.format_comment(data)
        assert "unrecognized input" not in md
        assert "prusik scan" in md


# --- prove formatter (the v0.36 Action source=prove path) -----------------

def test_prove_pass_renders_check():
    data = {"command": "pytest -q", "kind": "tests", "exit_code": 0,
            "executed": 42, "min_executed": 1, "proven": True,
            "reason": "42 test(s) executed, exit 0"}
    md = ci_comment.format_comment(data)
    assert "✅ prusik prove — PASS" in md
    assert "pytest -q" in md
    assert "actually ran clean" in md


def test_prove_not_proven_renders_fail_and_fabrication_note():
    data = {"command": "pytest -q", "kind": "tests", "exit_code": 0,
            "executed": 0, "min_executed": 1, "proven": False,
            "reason": "exit 0 but only 0 test(s) executed — nothing actually ran"}
    md = ci_comment.format_comment(data)
    assert "❌ prusik prove — NOT PROVEN" in md
    assert "fabrication this check exists to catch" in md


def test_prove_json_round_trips_through_formatter():
    """The Action path: `prusik prove --json` output → ci-comment."""
    from prusik import prove
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = prove.run(["bash", "-c", "echo '5 passed in 0.1s'"], json_output=True)
    data = json.loads(buf.getvalue()[buf.getvalue().index("{"):])
    assert rc == 0
    md = ci_comment.format_comment(data)
    assert "unrecognized input" not in md
    assert "prusik prove — PASS" in md
