"""The bash-output-repeat convergence detector must not fire on a SUCCESSFUL
idempotent no-op repeated identically (e.g. `alembic upgrade head` once at head) —
it targets a stuck agent RETRYING A FAILING command.

moat-finding: fb-876ad6010f72
moat-finding: fb-427808ba40dd
"""

from __future__ import annotations

import io
import json

from prusik import gate


def test_bash_succeeded_signal_priority():
    # top-level tool_response_success is authoritative
    assert gate._bash_succeeded({"tool_response_success": True}, {}) is True
    assert gate._bash_succeeded({"tool_response_success": False}, {}) is False
    # fallbacks inside tool_response (graceful for field-name/version drift)
    assert gate._bash_succeeded({}, {"exit_code": 0}) is True
    assert gate._bash_succeeded({}, {"returncode": 2}) is False
    assert gate._bash_succeeded({}, {"is_error": True}) is False
    assert gate._bash_succeeded({}, {"success": True}) is True
    # no signal → None (caller keeps prior behavior, no regression)
    assert gate._bash_succeeded({}, {"stdout": "x"}) is None


def _run_post_tool(monkeypatch, tmp_path, *, command, output, success, times):
    """Drive post_tool N times with the same command+output; return the recorded
    convergence_stall events."""
    from prusik import ledger, phases
    monkeypatch.setattr(ledger, "project_root", lambda: tmp_path)
    monkeypatch.setattr(phases, "load_sprint_config", lambda *a, **k: {"x": 1})
    monkeypatch.setattr(phases, "current_sprint_state",
                        lambda *a, **k: {"phase": "reviewing", "feature": "feat"})
    events = []
    monkeypatch.setattr(ledger, "append",
                        lambda ev, **k: events.append((ev, k)))
    payload = {
        "tool_name": "Bash",
        "tool_response_success": success,
        "tool_input": {"command": command},
        "tool_response": {"stdout": output, "stderr": ""},
    }
    raw = json.dumps(payload)
    for _ in range(times):
        monkeypatch.setattr("sys.stdin", io.StringIO(raw))
        gate.post_tool()
    return [k for ev, k in events if ev == "convergence_stall"]


def test_repeated_success_noop_never_fires(tmp_path, monkeypatch):
    out = "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n" \
          "INFO  [alembic.runtime.migration] Will assume transactional DDL.\n" * 2
    stalls = _run_post_tool(monkeypatch, tmp_path,
                            command="alembic upgrade head", output=out,
                            success=True, times=4)
    assert stalls == []          # idempotent success repeated → not a stall


def test_repeated_failure_still_fires(tmp_path, monkeypatch):
    out = "FAILED tests/test_x.py::test_a - AssertionError\n" \
          "23 failed, 0 passed in 12.3s\n" + ("noise line\n" * 5)
    stalls = _run_post_tool(monkeypatch, tmp_path,
                            command="pytest tests/", output=out,
                            success=False, times=3)
    assert len(stalls) == 1      # repeated FAILURE → the detector still catches it
    assert stalls[0]["kind"] == "bash_output_repeat"


def _drive(monkeypatch, tmp_path, sequence):
    """Drive post_tool through a SEQUENCE of (command, output) with the REAL Bash
    payload shape — stdout/stderr only, NO success/exit field (confirmed absent).
    Returns the convergence_stall events."""
    from prusik import ledger, phases
    monkeypatch.setattr(ledger, "project_root", lambda: tmp_path)
    monkeypatch.setattr(phases, "load_sprint_config", lambda *a, **k: {"x": 1})
    monkeypatch.setattr(phases, "current_sprint_state",
                        lambda *a, **k: {"phase": "reviewing", "feature": "f"})
    events = []
    monkeypatch.setattr(ledger, "append", lambda ev, **k: events.append((ev, k)))
    for cmd, out in sequence:
        payload = {"tool_name": "Bash", "tool_input": {"command": cmd},
                   "tool_response": {"stdout": out, "stderr": ""}}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        gate.post_tool()
    return [k for ev, k in events if ev == "convergence_stall"]


def test_interleaved_identical_green_does_not_fire(tmp_path, monkeypatch):
    """fb-427808ba40dd: a clean command re-run at every gate (identical PASS output),
    with real work between each run, must NOT trip the guard — even with no success
    signal in the payload. Non-consecutive → the ring resets."""
    green = "@app/contracts: build OK\nall contracts valid\n" + "checked pkg\n" * 5
    seq = [("pnpm contracts:check", green), ("pnpm test", "Tests 40 passed\n" + "t\n" * 5),
           ("pnpm contracts:check", green), ("pnpm lint", "lint clean\n" + "l\n" * 5),
           ("pnpm contracts:check", green)]               # 3 identical, but interleaved
    assert _drive(monkeypatch, tmp_path, seq) == []


def test_consecutive_identical_still_fires(tmp_path, monkeypatch):
    """The m4-h2 stuck-loop signature — the SAME command back-to-back, no progress —
    must still be caught (payload-independent)."""
    out = "FAILED tests/test_x.py - AssertionError\n23 failed\n" + "noise line\n" * 8
    stalls = _drive(monkeypatch, tmp_path, [("pytest tests/", out)] * 3)
    assert len(stalls) == 1 and stalls[0]["kind"] == "bash_output_repeat"
