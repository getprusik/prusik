"""drive no longer halts on a gate deny the role recovered from (fb-a5ef442478e8): a spawn
denial that matches a `gate_blocked` ledger event for the same session and tool is logged and
drive goes on. criterion: drive-gate-denials."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout

from prusik import drive, gate, ledger
from prusik import platform_adapter as pa
from tests.test_sprint_drive import F, proj, rig  # noqa: F401  (fixtures)


def _fire(payload):
    stdin_backup = sys.stdin
    sys.stdin = io.StringIO(json.dumps(payload))
    try:
        with redirect_stdout(io.StringIO()):
            gate.pre_tool()
    finally:
        sys.stdin = stdin_backup


def _blocked():
    return [e for e in ledger.read_all() if e["event"] == "gate_blocked"]


def test_gate_blocked_records_hook_session(proj):
    _fire({"session_id": "sess-1", "tool_name": "Write",
           "tool_input": {"file_path": "src/outside.py"}})
    _fire({"tool_name": "Write", "tool_input": {"file_path": "src/outside2.py"}})
    ev = _blocked()
    assert len(ev) == 2
    assert ev[0]["session"] == "sess-1"
    assert "session" not in ev[1]


def _deny(rig, *tools):
    """The reviewer spawn comes back with a denial for each tool."""
    def result(req):
        return pa.SpawnResult(req.session_id, 0, False, "ok",
                              tuple({"tool_name": t} for t in tools), False)
    rig.adapter.results["integrator"] = result


def _sid_hook(rig, tool_events):
    def probe(req):
        for t, with_session in tool_events:
            extra = {"session": req.session_id} if with_session else {}
            ledger.append("gate_blocked", tool=t, **extra)
    rig.adapter.probe = probe


def _integrating(proj):
    from tests.test_sprint_drive import set_phase
    set_phase(proj, "integrating")


def test_all_denials_matched_continues(rig, proj):
    _integrating(proj)
    _deny(rig, "Bash")
    _sid_hook(rig, [("Bash", True)])
    assert rig.run() == 0
    ev = [e for e in ledger.read_all() if e["event"] == "drive_gate_denials"]
    assert len(ev) == 1 and ev[0]["tools"] == ["Bash"] and ev[0]["role"] == "integrator"
    assert "gate denials recovered: Bash" in (proj / "reports/feat/drive.log").read_text()


def test_one_unmatched_denial_escalates(rig, proj):
    _integrating(proj)
    _deny(rig, "Bash", "Write")
    _sid_hook(rig, [("Bash", True)])
    assert rig.run() == 3
    esc = rig.escalation()
    assert esc["kind"] == "permission_denied"
    assert esc["reason"].endswith("denied: Write")


def test_events_without_session_never_match(rig, proj):
    _integrating(proj)
    _deny(rig, "Bash")
    _sid_hook(rig, [("Bash", False)])
    assert rig.run() == 3
    assert rig.escalation()["kind"] == "permission_denied"


def test_unmatched_denials_is_pure():
    ev = [{"event": "gate_blocked", "tool": "Bash", "session": "s"},
          {"event": "gate_blocked", "tool": "Edit"},
          {"event": "other", "tool": "Read", "session": "s"}]
    d = [{"tool_name": "Bash"}, {"tool": "Edit"}, {"tool_name": "Read"}]
    assert drive._unmatched_denials(d, ev, "s") == ["Edit", "Read"]
