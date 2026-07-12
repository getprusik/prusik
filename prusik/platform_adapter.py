"""Platform-adapter seam (roadmap Horizon-2 E).

prusik's gate decisions are platform-agnostic — "is this write inside the phase's writable
scope?", "does this phase deny this command?" — but the I/O around them is not: how the
host agent delivers a tool-call event, and how a DENY is expressed, are specific to the
runtime (Claude Code's PreToolUse hook today; an OpenHands SecurityAnalyzer, a Codex
pre-tool callback, or a Cursor rule tomorrow). This module factors that I/O behind a thin
adapter so the gate's allow/deny logic never sees a host-specific shape.

What stays in the gate (`gate._gate_tool_event`): the policy — `is_path_writable`,
`deny_commands`/`deny_bash`, the ledger `gate_blocked` record, the human-readable reason.
What an adapter owns: `parse_event` (native tool-call → neutral `ToolEvent`) and `deny`
(neutral reason → native refusal). Adding a runtime = one adapter, no gate changes.

Only the Claude Code adapter ships today — this is the SEAM, not a second adapter (which
the freeze-speculative-surface posture builds only when a real adopter moves to a runtime,
roadmap Horizon-3). An unknown adapter name fails LOUD; there is no silent fallback.
"""

from __future__ import annotations

import abc
import json
import os
import sys
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolEvent:
    """A host's tool-call, normalized to what the gate reasons about:
      - `file_targets`: paths this event would directly WRITE (Write/Edit/notebook edits).
      - `command`: a shell command, if this is a shell invocation (the gate parses its
        redirect targets + applies deny_commands itself — that parsing is host-neutral).
      - `tool`: the native tool name, carried for ledger/messages only (never branched on
        by the gate)."""
    tool: str
    file_targets: tuple[str, ...] = field(default_factory=tuple)
    command: str | None = None


class PlatformAdapter(abc.ABC):
    """The seam every runtime implements. `read_payload` + `parse_event` turn the host's
    native tool-call event into a `ToolEvent`; `deny` expresses a refusal natively."""

    name: str = "abstract"

    @abc.abstractmethod
    def read_payload(self) -> dict:
        """Read the host's raw tool-call event (e.g. Claude Code's PreToolUse JSON)."""

    @abc.abstractmethod
    def parse_event(self, payload: dict) -> ToolEvent | None:
        """Map the native payload to a neutral `ToolEvent`, or None if not gateable."""

    @abc.abstractmethod
    def deny(self, reason: str) -> int:
        """Express a DENY in the host's native form; return the process exit code."""


class ClaudeCodeAdapter(PlatformAdapter):
    """Claude Code's PreToolUse hook: the event arrives as JSON on stdin
    (`tool_name` + `tool_input`); a deny is JSON on stdout with exit 0."""

    name = "claude-code"
    _WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")

    def read_payload(self) -> dict:
        try:
            raw = sys.stdin.read()
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {}

    def parse_event(self, payload: dict) -> ToolEvent | None:
        tool = payload.get("tool_name", "")
        ti = payload.get("tool_input", {}) or {}
        if tool in self._WRITE_TOOLS:
            target = (ti.get("file_path") or ti.get("path")
                      or ti.get("notebook_path"))
            return ToolEvent(tool=tool,
                             file_targets=(target,) if target else ())
        if tool == "Bash":
            return ToolEvent(tool=tool, command=ti.get("command", ""))
        return ToolEvent(tool=tool)          # a non-write, non-shell tool → nothing to gate

    def deny(self, reason: str) -> int:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }))
        return 0


_ADAPTERS = {ClaudeCodeAdapter.name: ClaudeCodeAdapter}


def get_adapter(name: str | None = None) -> PlatformAdapter:
    """The active platform adapter. Defaults to Claude Code; `PRUSIK_ADAPTER` selects
    another once one ships. An unknown name fails LOUD (no silent fallback to a default
    that would silently mis-gate a runtime it wasn't built for)."""
    name = name or os.environ.get("PRUSIK_ADAPTER", ClaudeCodeAdapter.name)
    cls = _ADAPTERS.get(name)
    if cls is None:
        raise ValueError(
            f"unknown platform adapter {name!r} — shipped: {sorted(_ADAPTERS)}. "
            f"Building a second adapter is roadmap Horizon-3, triggered by a real adopter "
            f"moving to that runtime.")
    return cls()
