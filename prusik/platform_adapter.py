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

The same seam carries the host I/O behind measured token usage (fb-a1b7db140a5e):
`session_ref` (hook payload → neutral `SessionRef`), `read_usage` (the host's transcript
files → neutral `UsageRecord`s) and `last_write` (newest transcript write, the cheap
coverage bound). What stays host-neutral in `prusik.usage`: the session registry, dedup,
phase attribution and measurability.

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
from pathlib import Path


@dataclass(frozen=True)
class ToolEvent:
    """A host's tool-call, normalized to what the gate reasons about:
      - `file_targets`: paths this event would directly WRITE (Write/Edit/notebook edits).
      - `command`: a shell command, if this is a shell invocation (the gate parses its
        redirect targets + applies deny_commands itself — that parsing is host-neutral).
      - `tool`: the native tool name, carried for ledger/messages only (never branched on
        by the gate).
      - `session`: the host's session identity, when it provides one — the key for
        cross-session serialization (fb-0b9d0a6d1ce6 single-writer lease). None when
        a host can't identify sessions; the lease check is then inapplicable."""
    tool: str
    file_targets: tuple[str, ...] = field(default_factory=tuple)
    command: str | None = None
    session: str | None = None


@dataclass(frozen=True)
class SessionRef:
    """A host session and where its transcript lives, from a hook payload. The key the
    usage registry stores (fb-a1b7db140a5e)."""
    session: str
    transcript: str


@dataclass(frozen=True)
class UsageRecord:
    """One model response's token usage, normalized. `message_id` is the host's response
    id, the dedup key: a host may write one row per content block (partial counters) and a
    resumed session may copy rows, so `usage` merges repeats; the adapter yields every row."""
    message_id: str
    ts: str
    model: str
    role: str
    input: int
    output: int
    cache_write: int
    cache_read: int


@dataclass
class TranscriptUsage:
    """What one session's transcripts yielded. `unreadable` names files that could not be
    read; `malformed` counts lines skipped as unparseable (never fatal)."""
    records: list[UsageRecord] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    malformed: int = 0


class PlatformAdapter(abc.ABC):
    """The seam every runtime implements. `read_payload` + `parse_event` turn the host's
    native tool-call event into a `ToolEvent`; `deny` expresses a refusal natively;
    `session_ref` / `read_usage` / `last_write` expose a session's transcript usage."""

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

    @abc.abstractmethod
    def session_ref(self, payload: dict) -> SessionRef | None:
        """The session + transcript a hook payload identifies, or None."""

    @abc.abstractmethod
    def read_usage(self, transcript: str, role_names: set[str]) -> TranscriptUsage:
        """Usage records from a session's transcript files; `role_names` are the roles a
        subagent may be attributed to."""

    @abc.abstractmethod
    def last_write(self, transcript: str) -> float | None:
        """Newest mtime across a session's transcript files (None when the main one is
        missing). Transcripts are append-only, so this bounds which records a session can
        hold without reading it."""


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
        session = payload.get("session_id") or None
        if tool in self._WRITE_TOOLS:
            target = (ti.get("file_path") or ti.get("path")
                      or ti.get("notebook_path"))
            return ToolEvent(tool=tool,
                             file_targets=(target,) if target else (),
                             session=session)
        if tool == "Bash":
            return ToolEvent(tool=tool, command=ti.get("command", ""),
                             session=session)
        return ToolEvent(tool=tool, session=session)  # non-write, non-shell → nothing to gate

    def deny(self, reason: str) -> int:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }))
        return 0

    # Usage (fb-a1b7db140a5e). Verified on Claude Code 2026-10-26: every hook payload carries
    # `session_id` + `transcript_path`; the session JSONL has one row per content block, the
    # usage repeated on each and `output_tokens` partial on all but the final one; subagents
    # live at <stem>/subagents/agent-<id>.jsonl beside agent-<id>.meta.json
    # {"agentType", "description", ...}.
    _USAGE_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                   "cache_read_input_tokens")
    _GENERIC_AGENT_TYPES = ("general-purpose", "")

    def session_ref(self, payload: dict) -> SessionRef | None:
        sid, path = payload.get("session_id"), payload.get("transcript_path")
        if isinstance(sid, str) and sid and isinstance(path, str) and path:
            return SessionRef(session=sid, transcript=path)
        return None

    @staticmethod
    def _subagent_files(main: Path) -> list[Path]:
        d = main.with_suffix("") / "subagents"
        return sorted(d.glob("agent-*.jsonl")) if d.is_dir() else []

    def read_usage(self, transcript: str, role_names: set[str]) -> TranscriptUsage:
        out = TranscriptUsage()
        main = Path(transcript)
        if not self._read_file(main, "main", out):
            out.unreadable.append(str(main))
            return out
        for f in self._subagent_files(main):
            role = self._subagent_role(f.with_suffix(".meta.json"), role_names)
            if not self._read_file(f, role, out):
                out.unreadable.append(str(f))
        return out

    def last_write(self, transcript: str) -> float | None:
        main = Path(transcript)
        try:
            newest = main.stat().st_mtime
        except OSError:
            return None
        for f in self._subagent_files(main):
            try:
                newest = max(newest, f.stat().st_mtime)
            except OSError:
                continue
        return newest

    @classmethod
    def _read_file(cls, path: Path, role: str, out: TranscriptUsage) -> bool:
        # errors="replace": a live transcript can end mid multibyte character, and a stray
        # non-UTF-8 byte sits in message text, never in the numbers read here.
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            return False
        with fh:
            for line in fh:
                if '"assistant"' not in line:       # cheap pre-filter before parsing
                    continue
                try:
                    rec = cls._usage_row(json.loads(line), role)
                except (ValueError, TypeError, AttributeError):
                    out.malformed += 1              # any malformed row: counted, not fatal
                    continue
                if rec is not None:
                    out.records.append(rec)
        return True

    @classmethod
    def _usage_row(cls, row: object, role: str) -> UsageRecord | None:
        """One transcript row → a UsageRecord; None for rows that carry no usage (user,
        synthetic, zero). Raises ValueError/TypeError on a malformed assistant row."""
        if not isinstance(row, dict) or row.get("type") != "assistant":
            return None
        msg = row.get("message")
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if not isinstance(msg, dict) or not isinstance(usage, dict):
            raise TypeError("assistant row without a message.usage mapping")
        counts = [int(usage.get(k) or 0) for k in cls._USAGE_KEYS]
        mid, ts, model = msg.get("id"), row.get("timestamp"), msg.get("model")
        if not (isinstance(mid, str) and mid and isinstance(ts, str) and ts):
            raise TypeError("assistant row without a string id and timestamp")
        if not any(counts):
            return None                             # synthetic / zero-usage rows
        return UsageRecord(message_id=mid, ts=ts,
                           model=model if isinstance(model, str) and model else "unknown",
                           role=role, input=counts[0], output=counts[1],
                           cache_write=counts[2], cache_read=counts[3])

    @classmethod
    def _subagent_role(cls, meta_path: Path, role_names: set[str]) -> str:
        """agentType; for a generic agent, the prusik role its description names (a role
        dispatched through a general-purpose agent); else agentType, else `subagent`."""
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            meta = {}
        agent_type = meta.get("agentType") or ""
        if agent_type not in cls._GENERIC_AGENT_TYPES:
            return agent_type
        desc = (meta.get("description") or "").lower().replace("-", " ")
        named = [r for r in role_names if r.lower().replace("-", " ") in desc]
        if named:
            return max(named, key=len)              # the most specific name wins
        return agent_type or "subagent"


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
