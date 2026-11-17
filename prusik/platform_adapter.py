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
phase attribution and measurability. `current_session_id` names the session this process
runs in, so the warning about a grown session reads the right transcript.

Only the Claude Code adapter ships today — this is the SEAM, not a second adapter (which
the freeze-speculative-surface posture builds only when a real adopter moves to a runtime,
roadmap Horizon-3). An unknown adapter name fails LOUD; there is no silent fallback.
"""

from __future__ import annotations

import abc
import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping
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
        a host can't identify sessions; the lease check is then inapplicable.
      - `cwd`: the directory a shell command starts in, when the host says. Relative paths
        in `command` start there; with None they start at the project root."""
    tool: str
    file_targets: tuple[str, ...] = field(default_factory=tuple)
    command: str | None = None
    session: str | None = None
    cwd: str | None = None


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
    # The cache_creation sub-counters. A row without them has 0 here, and the cache writes
    # they do not account for are priced at the 1h rate (prusik.pricing).
    cache_write_5m: int = 0
    cache_write_1h: int = 0


@dataclass
class TranscriptUsage:
    """What one session's transcripts yielded. `unreadable` names files that could not be
    read; `malformed` counts lines skipped as unparseable (never fatal)."""
    records: list[UsageRecord] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    malformed: int = 0


class SpawnError(Exception):
    """The host could not be asked (or answered unreadably) when drive needed it."""


@dataclass(frozen=True)
class AgentSpec:
    """A role's agent definition handed to the host's headless spawn."""
    name: str
    description: str
    tools: tuple[str, ...]
    prompt: str


@dataclass(frozen=True)
class SpawnRequest:
    """One headless role spawn. `root` is the project root (where the agents file lives),
    `cwd` where the child runs. `on_tick` is called every `tick_s` seconds while it runs."""
    role: str
    prompt: str
    model: str | None
    cwd: str
    root: str
    session_id: str
    agent_spec: AgentSpec
    timeout_s: int
    env: dict[str, str]
    on_tick: Callable[[], None] | None = None
    tick_s: int = 300


@dataclass(frozen=True)
class SpawnResult:
    session_id: str
    rc: int
    is_error: bool
    text: str
    denials: tuple[dict, ...]
    timed_out: bool


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
    def current_session_id(self) -> str | None:
        """The host session this process runs in, or None outside one."""

    @abc.abstractmethod
    def read_usage(self, transcript: str, role_names: set[str],
                   main_role: str = "main") -> TranscriptUsage:
        """Usage records from a session's transcript files; `role_names` are the roles a
        subagent may be attributed to; the main file's rows carry `main_role`."""

    @abc.abstractmethod
    def spawn_env(self, auth: str, base: Mapping[str, str]) -> dict[str, str]:
        """The environment a spawned session runs with for the configured `auth`
        ("claude.ai" | "api_key"); ValueError for an unknown value."""

    @abc.abstractmethod
    def auth_method(self, env: Mapping[str, str]) -> tuple[str, str]:
        """(method, source) the host would authenticate with under `env`; SpawnError when
        it cannot be determined."""

    @abc.abstractmethod
    def spawn(self, req: SpawnRequest) -> SpawnResult:
        """Run one role headless to completion (or timeout) and return what it reported."""

    @abc.abstractmethod
    def last_write(self, transcript: str) -> float | None:
        """Newest mtime across a session's transcript files (None when the main one is
        missing). Transcripts are append-only, so this bounds which records a session can
        hold without reading it."""

    @abc.abstractmethod
    def spawn_model_refs(self) -> frozenset[str]:
        """Model refs the host's subagent spawn call accepts."""

    @abc.abstractmethod
    def pinned_model_note(self, ref: str) -> str | None:
        """None when `ref` is not a pinned/dated/provider model id. Otherwise the note
        explaining how to pin it through the host instead of the spawn call. It
        classifies the ref and carries the message in one return value."""

    @abc.abstractmethod
    def model_matches(self, ref: str, observed_model: str) -> bool:
        """Whether a measured transcript model id is the model `ref` routes to."""


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
                             session=session, cwd=payload.get("cwd") or None)
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

    def current_session_id(self) -> str | None:
        # Verified 2026-11-14 on Claude Code 2.1.325: exported to every Bash subprocess,
        # subagents' included (they carry the parent's id).
        return os.environ.get("CLAUDE_CODE_SESSION_ID") or None

    _AUTHS = ("claude.ai", "api_key")

    def spawn_env(self, auth: str, base: Mapping[str, str]) -> dict[str, str]:
        if auth not in self._AUTHS:
            raise ValueError(f"unknown drive.auth {auth!r} — one of {list(self._AUTHS)}")
        env = dict(base)
        env.pop("CLAUDE_CODE_SESSION_ID", None)       # the child must not read the parent's id
        if auth == "claude.ai":
            env.pop("ANTHROPIC_API_KEY", None)
        return env

    def auth_method(self, env: Mapping[str, str]) -> tuple[str, str]:
        import subprocess               # function-level: the hook hot path never spawns
        try:
            p = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True,
                               env=dict(env), timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            raise SpawnError(f"`claude auth status` could not run: {e}") from e
        if p.returncode != 0:
            raise SpawnError(f"`claude auth status` exited {p.returncode}: "
                             f"{(p.stderr or p.stdout).strip()[:200]}")
        try:
            data = json.loads(p.stdout)
            return str(data["authMethod"]), str(data.get("apiKeySource") or "login")
        except (ValueError, KeyError, TypeError) as e:
            raise SpawnError(f"`claude auth status` output unparseable: {p.stdout[:200]!r}") from e

    def spawn(self, req: SpawnRequest) -> SpawnResult:
        agents_dir = Path(req.root) / ".sprint" / "drive-agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        spec = req.agent_spec
        agents_file = agents_dir / f"{req.session_id}.json"
        agents_file.write_text(json.dumps({spec.name: {
            "description": spec.description, "prompt": spec.prompt,
            "tools": list(spec.tools)}}), encoding="utf-8")
        cmd = ["claude", "-p", req.prompt, "--agents", str(agents_file), "--agent", spec.name]
        if req.model:
            cmd += ["--model", req.model]
        cmd += ["--permission-mode", "auto", "--permission-prompts", "none",
                "--output-format", "json", "--session-id", req.session_id]
        try:
            return self._run(cmd, req)
        finally:
            agents_file.unlink(missing_ok=True)

    @staticmethod
    def _run(cmd: list[str], req: SpawnRequest) -> SpawnResult:
        # Output goes to temp files, not pipes: a long run can never block on a full pipe
        # while this loop waits in slices.
        import subprocess
        import tempfile
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out,
                                        stderr=err, cwd=req.cwd, env=req.env)
            except OSError as e:
                raise SpawnError(f"could not start claude: {e}") from e
            deadline = time.monotonic() + req.timeout_s
            timed_out = False
            while True:
                left = deadline - time.monotonic()
                if left <= 0:
                    proc.kill()
                    proc.wait()
                    timed_out = True
                    break
                try:
                    proc.wait(timeout=min(req.tick_s, left))
                    break
                except subprocess.TimeoutExpired:
                    if req.on_tick is not None:
                        req.on_tick()
            out.seek(0)
            err.seek(0)
            stdout = out.read().decode("utf-8", errors="replace")
            stderr = err.read().decode("utf-8", errors="replace")
        if timed_out:
            return SpawnResult(req.session_id, -1, True,
                               f"timed out after {req.timeout_s}s", (), True)
        try:
            data = json.loads(stdout)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except ValueError:
            return SpawnResult(req.session_id, proc.returncode, True,
                               (stderr or stdout)[:500], (), False)
        denials = tuple(d for d in (data.get("permission_denials") or []) if isinstance(d, dict))
        return SpawnResult(req.session_id, proc.returncode,
                           bool(data.get("is_error")) or proc.returncode != 0,
                           str(data.get("result") or ""), denials, False)

    def session_ref(self, payload: dict) -> SessionRef | None:
        sid, path = payload.get("session_id"), payload.get("transcript_path")
        if isinstance(sid, str) and sid and isinstance(path, str) and path:
            return SessionRef(session=sid, transcript=path)
        return None

    @staticmethod
    def _subagent_files(main: Path) -> list[Path]:
        d = main.with_suffix("") / "subagents"
        return sorted(d.glob("agent-*.jsonl")) if d.is_dir() else []

    def read_usage(self, transcript: str, role_names: set[str],
                   main_role: str = "main") -> TranscriptUsage:
        out = TranscriptUsage()
        main = Path(transcript)
        if not self._read_file(main, main_role, out):
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
        split = usage.get("cache_creation")
        split = split if isinstance(split, dict) else {}
        w5m = int(split.get("ephemeral_5m_input_tokens") or 0)
        w1h = int(split.get("ephemeral_1h_input_tokens") or 0)
        return UsageRecord(message_id=mid, ts=ts,
                           model=model if isinstance(model, str) and model else "unknown",
                           role=role, input=counts[0], output=counts[1],
                           cache_write=counts[2], cache_read=counts[3],
                           cache_write_5m=w5m, cache_write_1h=w1h)

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


    # Model routing. The spawn refs are the Agent tool's `model` enum, verified on Claude Code
    # 2026-10-27 against the live tool schema (opus, sonnet, haiku, fable).
    _SPAWN_REFS = frozenset({"opus", "sonnet", "haiku", "fable"})
    _ID_SPLIT = re.compile(r"[-.@:_/]")

    def spawn_model_refs(self) -> frozenset[str]:
        return self._SPAWN_REFS

    def pinned_model_note(self, ref: str) -> str | None:
        low = ref.lower()
        if not (low.startswith("claude-") or "anthropic.claude-" in low):
            return None
        return (f"'{ref}' is a pinned model id, and the spawn call accepts aliases only "
                f"({', '.join(sorted(self._SPAWN_REFS))}). Keep the alias in the profile and "
                "pin the dated or Bedrock id through Claude Code's alias env settings "
                "(ANTHROPIC_DEFAULT_OPUS_MODEL, ANTHROPIC_DEFAULT_SONNET_MODEL, "
                "ANTHROPIC_DEFAULT_HAIKU_MODEL).")

    def model_matches(self, ref: str, observed_model: str) -> bool:
        tokens = set(self._ID_SPLIT.split(observed_model.lower()))
        return ref.lower() in tokens and "claude" in tokens


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
