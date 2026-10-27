"""Measured token usage — what a sprint's phases actually cost (fb-a1b7db140a5e).

The numbers come from the host's own session transcripts, never from an agent claim: the
hooks register each session's transcript (SessionStart + every Stop), and on demand the
platform adapter reads usage records out of them. Everything here is host-neutral policy:

  - the session registry (`.sprint/sessions.json`, beside the ledger — same root resolution,
    so a builder worktree registers into the canonical root);
  - dedup by message id across EVERY registered session, keeping the per-field maximum (a
    host may write one row per content block with partial counters, and a resumed/forked
    session copies earlier rows into its own file);
  - attribution to the feature's phase windows — the effort lens's walk, half-open
    [start, end) — so time and tokens share one timeline;
  - measurability: a result is `measured` or carries the reason it isn't, so a configured
    budget is never silently compared against a number that could not be read.

`tokens` is input + output + cache writes + cache reads: every token the model processed.
A phase's usage is the project's spend during its window — every registered session in this
root, concurrent ones included; `by_session` keeps each session's share visible.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from prusik import ledger

if TYPE_CHECKING:                     # type-only: keeps Stop's import set unchanged
    from prusik.platform_adapter import SessionRef

_REGISTRY = "sessions.json"
_CATEGORIES = ("input", "output", "cache_write", "cache_read")

# The defaults shipped through v0.227.x: guesses 10-100x below measured usage (median phase
# 1.6M-22M tokens across three projects). A phase whose budget_tokens equals one of its
# phase's retired values is indistinguishable from an untouched default, so `prusik refresh`
# removes it; any other value is the user's and stays live. Single source for refresh + tests.
RETIRED_BUDGET_DEFAULTS: dict[str, frozenset[int]] = {
    "planning": frozenset({150000}),
    "solo_execute": frozenset({400000, 1000000}),   # template, fastapi-postgres preset
    "building": frozenset({1000000}),
    "reviewing": frozenset({300000}),
}


def registry_path() -> Path:
    return ledger.ledger_path().parent / _REGISTRY


def _parse_ts(ts: object) -> datetime | None:
    """The parser for the ledger/transcript boundary — both sides go through it: ledger
    `...+00:00` and transcript `...Z`. Python 3.10's fromisoformat rejects `Z`, so it is
    normalized here."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts[:-1] + "+00:00" if ts.endswith("Z") else ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _load_registry(path: Path) -> dict | None:
    """The registry; {} when absent, None when present but unreadable."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def register(ref: SessionRef | None) -> None:
    """Upsert a session (a platform_adapter.SessionRef, or None) into the registry. Hook-safe:
    never raises; reads and writes only the registry (no transcript, no ledger). The atomic
    replace keeps the file valid under concurrent sessions; a lost update heals at that
    session's next Stop."""
    if ref is None:
        return
    try:
        path = registry_path()
        reg = _load_registry(path)
        if reg is None:                       # corrupt: keep the evidence, start fresh
            os.replace(path, path.with_name(_REGISTRY + ".corrupt"))
            reg = {}
        now = datetime.now(timezone.utc).isoformat()
        entry = reg.get(ref.session)
        if not isinstance(entry, dict):
            entry = {"first_seen": now}
        entry["transcript_path"] = ref.transcript
        entry["last_seen"] = now
        reg[ref.session] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{_REGISTRY}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(reg, indent=1, sort_keys=True))
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — a hook must never fail on bookkeeping
        pass


def packaged_roles() -> frozenset[str]:
    """The packaged prusik roles (stems of `prusik/templates/.claude/agents/*.md`), present
    wherever prusik runs. The single source for `known_roles` and the export's role
    anonymization allowlist."""
    pkg = Path(__file__).parent / "templates" / ".claude" / "agents"
    return frozenset(p.stem for p in pkg.glob("*.md"))


def known_roles(root: Path) -> set[str]:
    """Roles a subagent's description may name: the packaged prusik agents plus any the
    project ships in .claude/agents."""
    from prusik import roles
    return set(packaged_roles()) | roles.shipped_agents(root)


@dataclass
class Usage:
    """A feature's (or one phase's) measured usage: totals, the four categories and the
    by_* attributions — or `measured=False` with the `reason` it could not be measured."""
    measured: bool
    reason: str | None = None
    tokens: int = 0
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0
    by_phase: dict[str, int] = field(default_factory=dict)
    by_role: dict[str, int] = field(default_factory=dict)
    by_model: dict[str, int] = field(default_factory=dict)
    by_session: dict[str, int] = field(default_factory=dict)
    malformed_lines: int = 0

    def breakdown(self) -> dict:
        return {"input": self.input, "output": self.output, "cache_write": self.cache_write,
                "cache_read": self.cache_read, "by_phase": self.by_phase,
                "by_role": self.by_role, "by_model": self.by_model,
                "by_session": self.by_session, "malformed_lines": self.malformed_lines}


def measure(feature: str, phase: str | None = None, now: datetime | None = None) -> Usage:
    """Measured usage of `feature` — or of one `phase`, every visit of it (rewinds included).
    Total: an unexpected failure is `measured=False` with its reason (loud at every caller),
    never an exception into sprint-complete, the watchdog or advance."""
    try:
        return _measure(feature, phase, now)
    except Exception as e:  # noqa: BLE001 — telemetry must never break its callers
        return Usage(measured=False, reason=f"usage read failed: {type(e).__name__}: {e}")


def _measure(feature: str, phase: str | None, now: datetime | None) -> Usage:
    """One root resolution: the ledger and the registry both come from `ledger_path()`, so they
    can never disagree. Coverage keys on each session's newest transcript write, not the
    registry's `last_seen`: Stop fires at turn END while phases are entered and left
    mid-turn, so an active session's last_seen routinely precedes the window (DEV-001)."""
    from prusik import effort
    from prusik.platform_adapter import get_adapter
    now = now or datetime.now(timezone.utc)
    windows: list[tuple[datetime, datetime, str]] = []
    for w in effort.phase_windows(ledger.read_all(), feature):
        if phase is not None and w["phase"] != phase:
            continue
        start = _parse_ts(w["start"])
        end = _parse_ts(w["end"]) if w["end"] else now
        if start and end:
            windows.append((start, end, w["phase"]))
    if not windows:
        scope = f"phase {phase!r} of " if phase else ""
        return Usage(measured=False,
                     reason=f"no {scope}feature {feature!r} window in the ledger")
    first = min(w[0] for w in windows)

    reg = _load_registry(registry_path())
    if reg is None:
        return Usage(measured=False, reason=f"session registry unreadable: {registry_path()}")
    adapter = get_adapter()
    active: list[tuple[str, str]] = []
    for sid, entry in sorted(reg.items(), key=lambda kv: str((kv[1] or {}).get("first_seen"))
                             if isinstance(kv[1], dict) else ""):
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("transcript_path") or "")
        written = adapter.last_write(path)
        if written is None:
            seen = _parse_ts(entry.get("last_seen"))
            if seen is not None and seen >= first:     # known active in the window: gone
                return Usage(measured=False, reason=f"transcript unreadable: {path}")
            continue                                   # long gone (host cleanup): no rows
        if datetime.fromtimestamp(written, timezone.utc) >= first:
            active.append((sid, path))
    if not active:
        return Usage(measured=False,
                     reason=f"no registered session was active since {first.isoformat()} "
                            f"(the hooks register transcripts at SessionStart and Stop)")

    role_names = known_roles(ledger.project_root())
    merged: dict[str, dict] = {}
    malformed = 0
    for sid, path in active:
        read = adapter.read_usage(path, role_names)
        if read.unreadable:
            return Usage(measured=False,
                         reason=f"transcript unreadable: {', '.join(read.unreadable)}")
        malformed += read.malformed
        for rec in read.records:
            cur = merged.get(rec.message_id)
            if cur is None:
                merged[rec.message_id] = {"ts": rec.ts, "model": rec.model, "role": rec.role,
                                          "session": sid,
                                          **{c: getattr(rec, c) for c in _CATEGORIES}}
            else:                             # partial rows / resume copies: per-field max
                for c in _CATEGORIES:
                    cur[c] = max(cur[c], getattr(rec, c))

    u = Usage(measured=True, malformed_lines=malformed)
    for row in merged.values():
        t = _parse_ts(row["ts"])
        if t is None:                         # counted, never silently outside every window
            u.malformed_lines += 1
            continue
        ph = next((p for s, e, p in windows if s <= t < e), None)
        if ph is None:
            continue
        n = sum(row[c] for c in _CATEGORIES)
        u.tokens += n
        for c in _CATEGORIES:
            setattr(u, c, getattr(u, c) + row[c])
        for bucket, key in ((u.by_phase, ph), (u.by_role, row["role"]),
                            (u.by_model, row["model"]), (u.by_session, row["session"])):
            bucket[key] = bucket.get(key, 0) + n
    return u


# ---- rollup: one product's measured usage, from the ledger alone -------------------------

_BY_KEYS = ("by_phase", "by_role", "by_model")   # never by_session: local plumbing


def _int_dict(value: object) -> dict[str, int]:
    """A snapshot's by_* attribution: non-dict reads as {}, non-int values are skipped
    (bool too — it is an int subclass but never a token count)."""
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items()
            if isinstance(v, int) and not isinstance(v, bool)}


def rollup(records: list[dict]) -> dict:
    """The ONE rollup of a product's measured token usage, over the ledger's `sprint_complete`
    events — pure: no transcript, file or clock is read, so it works long after the host has
    cleaned the transcripts up. It is the only reader of a snapshot's by_phase (`prusik
    effort` and the HQ export both take it from here).

    A sprint is measured when `actual.tokens` is an int and `actual.usage` is a snapshot;
    unmeasured with its own reason when `actual.tokens_unmeasured` is set; otherwise
    unmeasured as `pre-measurement` (completed before v0.228.0)."""
    import statistics
    done = sorted((r for r in records
                   if isinstance(r, dict) and r.get("event") == "sprint_complete"),
                  key=lambda r: str(r.get("ts") or ""))
    out: dict = {"sprints": 0, "measured": 0, "unmeasured": 0, "unmeasured_reasons": {},
                 "tokens": 0, "median_tokens": None,
                 "by_phase": {}, "by_role": {}, "by_model": {},
                 "by_month": {}, "per_sprint": []}
    measured_tokens: list[int] = []
    for r in done:
        actual = r.get("actual")
        if not isinstance(actual, dict):
            actual = {}
        tokens, snap = actual.get("tokens"), actual.get("usage")
        ts = str(r.get("ts") or "")
        is_measured = (isinstance(tokens, int) and not isinstance(tokens, bool)
                       and isinstance(snap, dict))
        out["sprints"] += 1
        month = out["by_month"].setdefault(ts[:7], {"sprints": 0, "measured": 0, "tokens": 0})
        month["sprints"] += 1
        entry: dict = {"feature": r.get("feature"), "ts": ts, "measured": is_measured,
                       "reason": None, "tokens": None,
                       "by_phase": {}, "by_role": {}, "by_model": {}}
        if is_measured and isinstance(tokens, int) and isinstance(snap, dict):
            out["measured"] += 1
            out["tokens"] += tokens
            month["measured"] += 1
            month["tokens"] += tokens
            measured_tokens.append(tokens)
            entry["tokens"] = tokens
            for key in _BY_KEYS:
                attributed = _int_dict(snap.get(key))
                entry[key] = attributed
                agg = out[key]
                for k, n in attributed.items():
                    agg[k] = agg.get(k, 0) + n
        else:
            reason = actual.get("tokens_unmeasured") or "pre-measurement"
            reason = str(reason)
            out["unmeasured"] += 1
            out["unmeasured_reasons"][reason] = out["unmeasured_reasons"].get(reason, 0) + 1
            entry["reason"] = reason
        out["per_sprint"].append(entry)
    if measured_tokens:
        out["median_tokens"] = statistics.median(measured_tokens)
    return out
