"""The platform-adapter seam (roadmap Horizon-2 E) factors host-specific I/O — parsing a
tool-call event and expressing a DENY — behind an adapter, so the gate's allow/deny policy
runs on a neutral ToolEvent. Claude Code is the only shipped adapter; an unknown runtime
fails LOUD (no silent fallback that would mis-gate a runtime the adapter wasn't built for).

moat-finding: roadmap-horizon-2e-platform-adapter
"""

from __future__ import annotations

import json

import pytest

from prusik import platform_adapter as pa


def test_parse_write_tools_to_file_targets():
    a = pa.ClaudeCodeAdapter()
    for tool, key in [("Write", "file_path"), ("Edit", "file_path"),
                      ("NotebookEdit", "notebook_path")]:
        ev = a.parse_event({"tool_name": tool, "tool_input": {key: "src/x.py"}})
        assert ev.tool == tool
        assert ev.file_targets == ("src/x.py",)
        assert ev.command is None


def test_parse_bash_to_command():
    ev = pa.ClaudeCodeAdapter().parse_event(
        {"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    assert ev.command == "ls -la"
    assert ev.file_targets == ()


def test_parse_path_fallback_and_inert_tools():
    a = pa.ClaudeCodeAdapter()
    assert a.parse_event({"tool_name": "Write", "tool_input": {"path": "p"}}).file_targets == ("p",)
    # a non-write, non-shell tool carries nothing to gate
    read = a.parse_event({"tool_name": "Read", "tool_input": {"file_path": "x"}})
    assert read.file_targets == () and read.command is None


def test_deny_emits_pretooluse_json(capsys):
    rc = pa.ClaudeCodeAdapter().deny("nope")
    assert rc == 0
    hso = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert hso["permissionDecisionReason"] == "nope"


def test_get_adapter_default_is_claude_code(monkeypatch):
    monkeypatch.delenv("PRUSIK_ADAPTER", raising=False)
    a = pa.get_adapter()
    assert isinstance(a, pa.ClaudeCodeAdapter) and a.name == "claude-code"


def test_get_adapter_unknown_fails_loud():
    # NO silent fallback to a default — an unknown runtime must not be silently mis-gated
    with pytest.raises(ValueError):
        pa.get_adapter("openhands")


def test_gate_runs_on_neutral_event_and_denies_via_adapter(monkeypatch):
    # The gate policy operates on the neutral ToolEvent and routes the refusal through the
    # adapter — no host-specific shape reaches the policy.
    from prusik import gate, ledger, phases
    monkeypatch.setattr(phases, "is_path_writable", lambda *a, **k: (False, "out of scope"))
    monkeypatch.setattr(ledger, "append", lambda *a, **k: None)
    monkeypatch.setattr(gate, "_worktree_redirect_rel", lambda *a, **k: None)
    monkeypatch.setattr(gate, "_worktree_redirect_hint", lambda *a, **k: None)
    captured = {}

    class StubAdapter:
        def deny(self, reason):
            captured["reason"] = reason
            return 7

    ev = pa.ToolEvent(tool="Write", file_targets=("src/x.py",))
    rc = gate._gate_tool_event(ev, {}, "reviewing", "feat", StubAdapter())
    assert rc == 7
    assert "blocks write to src/x.py" in captured["reason"]


def test_gate_allows_when_writable(monkeypatch):
    from prusik import gate, ledger, phases
    monkeypatch.setattr(phases, "is_path_writable", lambda *a, **k: (True, ""))
    monkeypatch.setattr(ledger, "append", lambda *a, **k: None)
    ev = pa.ToolEvent(tool="Write", file_targets=("src/x.py",))
    assert gate._gate_tool_event(ev, {}, "building", "feat", object()) == 0   # no deny
