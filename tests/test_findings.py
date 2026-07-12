"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: findings (kit/findings.py, v0.26.0 — agent-readable JSON contract).

Run: uv run python -m pytest tests/test_findings.py -v
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


def _seed_binding_event(tmp, ts, template, url, expected):
    """Helper: seed a reviewer_binding_flagged event."""
    sp = tmp / ".sprint"
    sp.mkdir(parents=True, exist_ok=True)
    ev = {
        "ts": ts, "event": "reviewer_binding_flagged",
        "feature": "x", "binding_class": "fetch_url",
        "template": template, "url": url, "expected": expected,
    }
    with (sp / "ledger.jsonl").open("a") as f:
        f.write(json.dumps(ev) + "\n")


def test_v0260_findings_empty_ledger_returns_empty_contract():
    """Fresh project, no events → contract with count=0 and the stable
    schema_version. No errors."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        result = kit_findings.collect()
        assert result["schema_version"] == "1.1"
        assert result["stats"]["count"] == 0
        assert result["findings"] == []
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_schema_version_is_stable_contract():
    """The schema_version field is the contract-version stamp. Tests
    FAIL if it changes silently — a bump must be intentional + paired
    with a contract change. (Additive field changes don't bump.)"""
    from prusik import findings as kit_findings
    assert kit_findings.SCHEMA_VERSION == "1.1", (
        "SCHEMA_VERSION changed — was the change intentional? Agent "
        "prompt conventions consume this; bumping requires migration "
        "for downstream callers. (1.0→1.1 added the `detector` field, v0.40.0.)"
    )


def test_v0260_findings_translates_binding_event():
    """A reviewer_binding_flagged event becomes a binding_mismatch
    finding with the expected fields populated."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        _seed_binding_event(tmp, "2026-06-01T10:00:00+00:00",
                             "t.html", "/clients/search",
                             ["/invoices/clients/search"])
        result = kit_findings.collect()
        assert result["stats"]["count"] == 1
        f = result["findings"][0]
        assert f["kind"] == "binding_mismatch"
        assert f["severity"] == "medium"
        assert "/clients/search" in f["summary"]
        assert "/invoices/clients/search" in f["suggested_action"]
        assert f["id"].startswith("binding_mismatch:")
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_skips_non_finding_events():
    """phase_advance / sprint_started / etc. are workflow noise — NOT
    findings. The contract is surfaced ONLY for actionable events."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        sp = tmp / ".sprint"; sp.mkdir(parents=True, exist_ok=True)
        events = [
            {"ts": "2026-06-01T10:00:00+00:00", "event": "phase_advance",
             "phase": "scoping"},
            {"ts": "2026-06-01T10:01:00+00:00", "event": "sprint_started"},
            {"ts": "2026-06-01T10:02:00+00:00", "event": "reviewer_binding_flagged",
             "binding_class": "fetch_url", "template": "t.html",
             "url": "/x", "expected": []},
        ]
        (sp / "ledger.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n")
        result = kit_findings.collect()
        assert result["stats"]["count"] == 1, (
            "only the binding event is a finding; phase_advance and "
            "sprint_started are workflow noise"
        )
        assert result["findings"][0]["kind"] == "binding_mismatch"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_since_filter_excludes_old_events():
    """--since <ts> excludes events at or before the cursor."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        _seed_binding_event(tmp, "2026-06-01T10:00:00+00:00", "old.html",
                             "/old", [])
        _seed_binding_event(tmp, "2026-06-01T12:00:00+00:00", "new.html",
                             "/new", [])
        result = kit_findings.collect(since="2026-06-01T11:00:00+00:00")
        assert result["stats"]["count"] == 1
        assert result["findings"][0]["details"]["template"] == "new.html"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_consume_advances_cursor():
    """mark_consumed appends a findings_consumed event so subsequent
    --since last-turn skips already-seen findings."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        _seed_binding_event(tmp, "2026-06-01T10:00:00+00:00", "t.html",
                             "/x", [])
        result = kit_findings.collect(since="last-turn")
        assert result["stats"]["count"] == 1
        kit_findings.mark_consumed()
        # After consume, last-turn cursor is "now" → the 10:00 event
        # is BEFORE the cursor → no findings returned.
        result = kit_findings.collect(since="last-turn")
        assert result["stats"]["count"] == 0
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_since_last_turn_picks_up_new_events():
    """After consume, events with ts AFTER the cursor are surfaced."""
    from prusik import findings as kit_findings
    from datetime import datetime, timezone, timedelta
    tmp = _mktmp_project()
    try:
        # Seed initial, consume, then add a NEWER event
        _seed_binding_event(tmp, "2026-06-01T10:00:00+00:00", "old.html",
                             "/old", [])
        kit_findings.mark_consumed()
        # New event with ts strictly after the cursor (which is "now")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        _seed_binding_event(tmp, future, "new.html", "/new", [])
        result = kit_findings.collect(since="last-turn")
        assert result["stats"]["count"] == 1
        assert result["findings"][0]["details"]["template"] == "new.html"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_stable_id_dedupes_repeat_flags():
    """The same logical finding emitted twice produces the SAME id —
    so the agent can dedupe across re-emissions without needing to
    track ts."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        _seed_binding_event(tmp, "2026-06-01T10:00:00+00:00", "t.html",
                             "/x", ["/y"])
        _seed_binding_event(tmp, "2026-06-01T11:00:00+00:00", "t.html",
                             "/x", ["/y"])  # same shape, later ts
        result = kit_findings.collect()
        ids = [f["id"] for f in result["findings"]]
        assert len(ids) == 2  # two events surfaced
        assert ids[0] == ids[1], (
            "same logical finding must produce the same stable id "
            "(agent dedups on id, not ts)"
        )
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_includes_gate_blocks():
    """Gate_blocked events become high-severity findings — the agent
    treats them differently from binding-mismatch (which is medium)."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        sp = tmp / ".sprint"; sp.mkdir(parents=True, exist_ok=True)
        ev = {"ts": "2026-06-01T10:00:00+00:00", "event": "gate_blocked",
              "tool": "Bash", "command": "alembic upgrade head",
              "reason": "outside allowlist"}
        (sp / "ledger.jsonl").write_text(json.dumps(ev) + "\n")
        result = kit_findings.collect()
        assert result["stats"]["count"] == 1
        f = result["findings"][0]
        assert f["kind"] == "gate_block"
        assert f["severity"] == "high"
        assert "alembic" in f["summary"] or "Bash" in f["summary"]
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_run_emits_json_by_default():
    """`prusik findings` CLI defaults to JSON (the agent contract is the
    main consumer). --text is human-readable."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        _seed_binding_event(tmp, "2026-06-01T10:00:00+00:00", "t.html",
                             "/x", [])
        out = _capture_stdout(lambda: kit_findings.run(json_output=True))
        data = json.loads(out)  # must be valid JSON
        assert data["schema_version"] == "1.1"
        assert data["stats"]["count"] == 1
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_run_text_mode_is_human_readable():
    """--text mode emits human prose, not JSON. Useful for terminal
    inspection by the operator."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        _seed_binding_event(tmp, "2026-06-01T10:00:00+00:00", "t.html",
                             "/x", [])
        out = _capture_stdout(lambda: kit_findings.run(json_output=False))
        assert "prusik-findings" in out
        assert "binding_mismatch" in out
        # NOT JSON
        try:
            json.loads(out)
            raise AssertionError("text mode should NOT emit valid JSON")
        except json.JSONDecodeError:
            pass  # expected
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_rc_is_zero_findings_are_signal_not_error():
    """rc=0 always — findings are SIGNAL for the consumer (agent/CI),
    not an error. The caller decides what rc means in its context."""
    from prusik import findings as kit_findings
    tmp = _mktmp_project()
    try:
        _seed_binding_event(tmp, "2026-06-01T10:00:00+00:00", "t.html",
                             "/x", [])
        rc = kit_findings.run(json_output=True)
        assert rc == 0
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0260_findings_consume_via_cli_appends_ledger_event():
    """--consume adds a findings_consumed event to the ledger."""
    from prusik import findings as kit_findings
    from prusik import ledger as kit_ledger
    tmp = _mktmp_project()
    try:
        _seed_binding_event(tmp, "2026-06-01T10:00:00+00:00", "t.html",
                             "/x", [])
        _capture_stdout(
            lambda: kit_findings.run(consume=True, json_output=True))
        events = kit_ledger.read_all()
        consumed = [e for e in events if e.get("event") == "findings_consumed"]
        assert len(consumed) == 1, (
            "consume=True must append exactly one findings_consumed event"
        )
    finally:
        os.chdir("/"); shutil.rmtree(tmp)
