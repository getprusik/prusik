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

`tokens` is input + output + cache writes + cache reads: every token the model processed. It
is telemetry. Budgets compare `cost_usd` (fb-1d6f8de5b8d3): each response priced by its own
model from `prusik/prices.json` (see `prusik.pricing`); a model not in the table makes the
whole result unmeasured, naming the model.
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
_ROW_FIELDS = _CATEGORIES + ("cache_write_5m", "cache_write_1h")   # + the cache_creation split

# A session whose latest turn re-reads more than this above its first turn is warned about.
# Evidence (this repo's registered transcripts, 2026-10-26 to 2026-11-14): a sprint briefed in
# a fresh session started 26K-87K above that session's first turn; one briefed after another
# sprint in the same session started 147K-273K above it. Growth, not absolute size, so a
# larger system prompt is not warned for its baseline.
SESSION_GROWTH_WARN = 120_000

# The session behind fb-9dac611d867c grew from 53K to 180K on design reads, then re-read them
# for ~40 more turns (~3-4M), so 40K of design reads x tens of turns is >= 1M.
PLAN_BOUNDARY_GROWTH = 40_000


def turn_context(input: int, cache_write: int, cache_read: int) -> int:  # noqa: A002
    """The one definition of a turn's context: what that response read (output is excluded,
    it is not re-read by that turn)."""
    return input + cache_write + cache_read

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


def tag_session(session: str, *, role: str, feature: str) -> None:
    """Record which role and feature a session was spawned for (`prusik drive`), so `measure`
    attributes its main-thread rows to that role instead of `main`. Same atomic replace as
    `register`; it keeps the keys it does not set, so the SessionStart hook later adds
    `transcript_path`."""
    path = registry_path()
    reg = _load_registry(path)
    if reg is None:                           # corrupt: keep the evidence, start fresh
        os.replace(path, path.with_name(_REGISTRY + ".corrupt"))
        reg = {}
    entry = reg.get(session)
    if not isinstance(entry, dict):
        entry = {"first_seen": datetime.now(timezone.utc).isoformat()}
    entry["role"] = role
    entry["feature"] = feature
    reg[session] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{_REGISTRY}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(reg, indent=1, sort_keys=True))
    os.replace(tmp, path)


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
    by_role_model: dict[str, dict[str, int]] = field(default_factory=dict)   # {role: {model: n}}
    malformed_lines: int = 0
    peak_context: int | None = None    # largest main-thread turn context in the windows
    # Cost (API list price): total plus the same attributions as the tokens.
    cost_usd: float = 0.0
    cost_by_category: dict[str, float] = field(default_factory=dict)
    cost_by_phase: dict[str, float] = field(default_factory=dict)
    cost_by_role: dict[str, float] = field(default_factory=dict)
    cost_by_model: dict[str, float] = field(default_factory=dict)
    cost_by_session: dict[str, float] = field(default_factory=dict)
    cache_write_unsplit: int = 0       # cache-write tokens priced at 1h for want of a split
    # Each row's cost over its own model's input price: the tokens the same spend would have
    # bought as plain input. `tokens / input_equivalent` is the multiplier.
    input_equivalent: float = 0.0

    def breakdown(self) -> dict:
        r = lambda d: {k: round(v, 6) for k, v in d.items()}  # noqa: E731
        return {"cost_usd": round(self.cost_usd, 6),
                "cost_by_category": r(self.cost_by_category),
                "cost_by_phase": r(self.cost_by_phase), "cost_by_role": r(self.cost_by_role),
                "cost_by_model": r(self.cost_by_model),
                "cache_write_unsplit": self.cache_write_unsplit,
                "input_equivalent": round(self.input_equivalent, 3),
                **self._token_breakdown()}

    def _token_breakdown(self) -> dict:
        return {"input": self.input, "output": self.output, "cache_write": self.cache_write,
                "cache_read": self.cache_read, "by_phase": self.by_phase,
                "by_role": self.by_role, "by_model": self.by_model,
                "by_session": self.by_session, "by_role_model": self.by_role_model,
                "malformed_lines": self.malformed_lines,
                "peak_context": self.peak_context}


def multiplier(tokens: int, input_equivalent: float | None) -> float | None:
    """Raw tokens per input-equivalent token; None when there is no input-equivalent to divide
    by (nothing measured), never NaN or inf."""
    return tokens / input_equivalent if input_equivalent and input_equivalent > 0 else None


def spend_data(u: Usage) -> dict:
    """The spend report of a measured Usage as data (`prusik cost --json`, the watchdog
    payload): raw tokens by category; cost by category, role, phase and model; the multiplier
    (raw tokens per input-equivalent token) and the effective USD per million raw tokens. The
    last two are None when there are no tokens to divide by. Cost is API list price."""
    from prusik import pricing
    r = lambda d: {k: round(v, 6) for k, v in d.items()}  # noqa: E731
    cats = dict(u.cost_by_category)
    write = cats.pop("cache_write_5m", 0.0) + cats.pop("cache_write_1h", 0.0)
    cats["cache_write"] = write
    mult = multiplier(u.tokens, u.input_equivalent)
    return {"price_basis": pricing.LABEL, "tokens": u.tokens,
            "tokens_by_category": {"input": u.input, "output": u.output,
                                   "cache_write": u.cache_write, "cache_read": u.cache_read},
            "cost_usd": round(u.cost_usd, 6),
            "cost_by_category": r({c: cats.get(c, 0.0) for c in
                                   ("input", "output", "cache_write", "cache_read")}),
            "cost_by_role": r(u.cost_by_role), "cost_by_phase": r(u.cost_by_phase),
            "cost_by_model": r(u.cost_by_model),
            "input_equivalent_tokens": round(u.input_equivalent, 3),
            "multiplier": None if mult is None else round(mult, 3),
            "effective_usd_per_mtok": (round(u.cost_usd / u.tokens * 1e6, 6)
                                       if u.tokens else None)}


def spend_report(u: Usage) -> list[str]:
    """THE renderer of a measured Usage: every budget message, `prusik effort`'s sprint
    figures, sprint-complete, the phase line, the watchdog and `prusik cost` print these
    lines. An unmeasured Usage renders its reason, never a $0."""
    from prusik import effort
    if not u.measured:
        return [f"cost UNMEASURED: {u.reason}"]
    d = spend_data(u)
    usd, tok = effort.fmt_usd, effort.fmt_tokens

    def by(title: str, costs: dict) -> str:
        items = ", ".join(f"{k} {usd(v)}" for k, v in
                          sorted(costs.items(), key=lambda kv: -kv[1])[:5])
        return f"cost by {title}: {items or 'none'}"

    tc, cc = d["tokens_by_category"], d["cost_by_category"]
    if d["multiplier"] is None:
        mult = "multiplier: n/a (no tokens measured)" if not u.tokens else \
            "multiplier: n/a (no input-equivalent measured)"
    else:
        mult = (f"multiplier: {d['multiplier']:.1f} raw tokens per input-equivalent token "
                f"({tok(round(u.input_equivalent))} input-equivalent)")
    if d["effective_usd_per_mtok"] is not None:
        mult += f"; effective ${d['effective_usd_per_mtok']:,.2f} per million raw tokens"
    return [f"cost {usd(u.cost_usd)} ({d['price_basis']}); tokens {tok(u.tokens)} (telemetry)",
            "tokens: " + ", ".join(f"{k.replace('_', ' ')} {tok(v)}" for k, v in tc.items()),
            "cost by category: " + ", ".join(f"{k.replace('_', ' ')} {usd(v)}"
                                             for k, v in cc.items()),
            by("role", u.cost_by_role), by("phase", u.cost_by_phase),
            by("model", u.cost_by_model), mult]


def run_cost(feature: str, phase: str | None = None, session: str | None = None,
             json_output: bool = False) -> int:
    """`prusik cost`: the spend report of a sprint, one phase of it, or one session's share.
    Exit 2, printing the reason, when the cost is unmeasured (never $0)."""
    import sys
    u = measure(feature, phase=phase, session=session)
    scope = " ".join(x for x in (f"phase {phase}" if phase else "",
                                 f"session {session}" if session else "") if x)
    if not u.measured:
        print(f"[prusik-cost] cost UNMEASURED: {u.reason}", file=sys.stderr)
        if json_output:
            print(json.dumps({"measured": False, "reason": u.reason}, indent=2))
        return 2
    if json_output:
        print(json.dumps({"measured": True, "feature": feature, "phase": phase,
                          "session": session, **spend_data(u)}, indent=2))
        return 0
    print(f"{feature}{' ' + scope if scope else ''}")
    for line in spend_report(u):
        print(f"  {line}")
    return 0


def measure(feature: str, phase: str | None = None, now: datetime | None = None,
            session: str | None = None) -> Usage:
    """Measured usage of `feature` — or of one `phase`, every visit of it (rewinds included).
    Total: an unexpected failure is `measured=False` with its reason (loud at every caller),
    never an exception into sprint-complete, the watchdog or advance."""
    try:
        return _measure(feature, phase, now, session)
    except Exception as e:  # noqa: BLE001 — telemetry must never break its callers
        return Usage(measured=False, reason=f"usage read failed: {type(e).__name__}: {e}")


def _measure(feature: str, phase: str | None, now: datetime | None,
             session: str | None = None) -> Usage:
    """One root resolution: the ledger and the registry both come from `ledger_path()`, so they
    can never disagree. Coverage keys on each session's newest transcript write, not the
    registry's `last_seen`: Stop fires at turn END while phases are entered and left
    mid-turn, so an active session's last_seen routinely precedes the window (DEV-001)."""
    from prusik import effort, pricing   # lazy: Stop's import set stays unchanged
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
    active: list[tuple[str, str, str]] = []
    for sid, entry in sorted(reg.items(), key=lambda kv: str((kv[1] or {}).get("first_seen"))
                             if isinstance(kv[1], dict) else ""):
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("transcript_path") or "")
        if not path:                                   # tagged by drive, hook not yet seen
            continue
        written = adapter.last_write(path)
        if written is None:
            seen = _parse_ts(entry.get("last_seen"))
            if seen is not None and seen >= first:     # known active in the window: gone
                return Usage(measured=False, reason=f"transcript unreadable: {path}")
            continue                                   # long gone (host cleanup): no rows
        if datetime.fromtimestamp(written, timezone.utc) >= first:
            active.append((sid, path, str(entry.get("role") or "main")))
    if not active:
        return Usage(measured=False,
                     reason=f"no registered session was active since {first.isoformat()} "
                            f"(the hooks register transcripts at SessionStart and Stop)")

    role_names = known_roles(ledger.project_root())
    merged: dict[str, dict] = {}
    malformed = 0
    if session is not None:                    # one session's share: its rows only
        active = [a for a in active if a[0] == session]
        if not active:
            return Usage(measured=False,
                         reason=f"session {session!r} was not active in the window of "
                                f"{feature!r}" + (f" phase {phase!r}" if phase else ""))
    for sid, path, main_role in active:
        read = adapter.read_usage(path, role_names, main_role=main_role)
        if read.unreadable:
            return Usage(measured=False,
                         reason=f"transcript unreadable: {', '.join(read.unreadable)}")
        malformed += read.malformed
        for rec in read.records:
            cur = merged.get(rec.message_id)
            if cur is None:
                merged[rec.message_id] = {"ts": rec.ts, "model": rec.model, "role": rec.role,
                                          "session": sid,
                                          **{c: getattr(rec, c) for c in _ROW_FIELDS}}
            else:                             # partial rows / resume copies: per-field max
                for c in _ROW_FIELDS:
                    cur[c] = max(cur[c], getattr(rec, c))

    u = Usage(measured=True, malformed_lines=malformed)
    peak: int | None = None
    for row in merged.values():
        t = _parse_ts(row["ts"])
        if t is None:                         # counted, never silently outside every window
            u.malformed_lines += 1
            continue
        ph = next((p for s, e, p in windows if s <= t < e), None)
        if ph is None:
            continue
        n = sum(row[c] for c in _CATEGORIES)
        try:
            priced = pricing.price_row(
                row["model"], cache_write=row["cache_write"],
                **{c: row[c] for c in ("input", "output", "cache_read",
                                       "cache_write_5m", "cache_write_1h")})
        except pricing.UnpricedModel as e:    # fail closed: never 0, never a neighbour's price
            return Usage(measured=False, reason=f"cost unmeasured: {e}")
        cost = sum(priced.values())
        in_price = pricing.input_price_per_token(
            row["model"], prompt_tokens=row["input"] + row["cache_write"] + row["cache_read"])
        u.input_equivalent += cost / in_price if in_price else 0.0
        u.cache_write_unsplit += pricing.unsplit_writes(
            row["cache_write"], row["cache_write_5m"], row["cache_write_1h"])
        u.cost_usd += cost
        for cat, v in priced.items():
            u.cost_by_category[cat] = u.cost_by_category.get(cat, 0.0) + v
        for cbucket, key in ((u.cost_by_phase, ph), (u.cost_by_role, row["role"]),
                             (u.cost_by_model, row["model"]),
                             (u.cost_by_session, row["session"])):
            cbucket[key] = cbucket.get(key, 0.0) + cost
        if row["role"] == "main":
            ctx = turn_context(row["input"], row["cache_write"], row["cache_read"])
            peak = ctx if peak is None else max(peak, ctx)
        u.tokens += n
        for c in _CATEGORIES:
            setattr(u, c, getattr(u, c) + row[c])
        for bucket, key in ((u.by_phase, ph), (u.by_role, row["role"]),
                            (u.by_model, row["model"]), (u.by_session, row["session"])):
            bucket[key] = bucket.get(key, 0) + n
        inner = u.by_role_model.setdefault(row["role"], {})
        inner[row["model"]] = inner.get(row["model"], 0) + n
    u.peak_context = peak
    return u


# ---- the current session's growth --------------------------------------------------------

@dataclass
class SessionContext:
    """The current host session's context: `applies=False` outside a host session; otherwise
    `measured` with the first and latest main-thread turn's context, or the `reason` it
    could not be read."""
    applies: bool
    measured: bool = False
    reason: str | None = None
    first: int = 0
    current: int = 0

    @property
    def growth(self) -> int:
        return self.current - self.first


def session_context() -> SessionContext:
    """Total: any failure is `applies=True, measured=False` with its reason, never raised."""
    try:
        return _session_context()
    except Exception as e:  # noqa: BLE001 — advisory telemetry must never break its callers
        return SessionContext(applies=True,
                              reason=f"session context read failed: {type(e).__name__}: {e}")


def _session_context() -> SessionContext:
    from prusik.platform_adapter import get_adapter
    adapter = get_adapter()
    sid = adapter.current_session_id()
    if not sid:
        return SessionContext(applies=False)
    path = registry_path()
    reg = _load_registry(path)
    if reg is None:
        return SessionContext(applies=True, reason=f"session registry unreadable: {path}")
    entry = reg.get(sid)
    transcript = entry.get("transcript_path") if isinstance(entry, dict) else None
    if not transcript:
        return SessionContext(
            applies=True,
            reason=f"session {sid} is not registered in {path} "
                   f"(the hooks register it at SessionStart)")
    read = adapter.read_usage(str(transcript), set())
    if read.unreadable:
        return SessionContext(applies=True,
                              reason=f"transcript unreadable: {', '.join(read.unreadable)}")
    rows: dict[str, dict[str, int]] = {}
    for rec in read.records:
        if rec.role != "main":
            continue
        cur = rows.setdefault(rec.message_id, {c: 0 for c in _CATEGORIES})
        for c in _CATEGORIES:
            cur[c] = max(cur[c], getattr(rec, c))
    if not rows:
        return SessionContext(applies=True, reason=f"no main-thread usage in {transcript}")
    ctxs = [turn_context(r["input"], r["cache_write"], r["cache_read"]) for r in rows.values()]
    return SessionContext(applies=True, measured=True, first=ctxs[0], current=ctxs[-1])


# ---- rollup: one product's measured usage, from the ledger alone -------------------------

_BY_KEYS = ("by_phase", "by_role", "by_model")   # never by_session: local plumbing


def _int_dict(value: object) -> dict[str, int]:
    """A snapshot's by_* attribution: non-dict reads as {}, non-int values are skipped
    (bool too — it is an int subclass but never a token count)."""
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items()
            if isinstance(v, int) and not isinstance(v, bool)}


def _num_dict(value: object) -> dict[str, float]:
    """A snapshot's cost attribution: non-dict reads as {}, non-numbers are skipped."""
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


def role_models(record: dict) -> dict[str, dict[str, int]]:
    """The role x model token split of a `sprint_complete` record's usage snapshot -- the
    only reader of `by_role_model` (rollup and the export never carry it). {} when it is
    absent (unmeasured, or recorded before by_role_model existed). Present but malformed at
    any level raises ValueError naming what is wrong: the caller decides how to surface it,
    it is never read as "nothing observed"."""
    actual = record.get("actual") if isinstance(record, dict) else None
    snap = actual.get("usage") if isinstance(actual, dict) else None
    raw = snap.get("by_role_model") if isinstance(snap, dict) else None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"by_role_model is {type(raw).__name__}, not a mapping")
    out: dict[str, dict[str, int]] = {}
    for role, models in raw.items():
        if not isinstance(models, dict):
            raise ValueError(f"by_role_model[{role!r}] is {type(models).__name__}, not a mapping")
        bad = [m for m, n in models.items() if not isinstance(n, int) or isinstance(n, bool)]
        if bad:
            raise ValueError(f"by_role_model[{role!r}] has non-int counts for {bad}")
        out[str(role)] = {str(m): n for m, n in models.items()}
    return out


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
                 "cost_usd": 0.0, "cost_measured": 0, "median_cost_usd": None,
                 "cost_by_phase": {}, "cost_by_role": {}, "cost_by_model": {},
                 "by_phase": {}, "by_role": {}, "by_model": {},
                 "by_month": {}, "per_sprint": []}
    measured_tokens: list[int] = []
    measured_costs: list[float] = []
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
                       "reason": None, "tokens": None, "cost_usd": None,
                       "by_phase": {}, "by_role": {}, "by_model": {},
                       "peak_context": None, "input_equivalent": None}
        if is_measured and isinstance(tokens, int) and isinstance(snap, dict):
            out["measured"] += 1
            out["tokens"] += tokens
            month["measured"] += 1
            month["tokens"] += tokens
            measured_tokens.append(tokens)
            entry["tokens"] = tokens
            peak = snap.get("peak_context")
            ie = snap.get("input_equivalent")
            entry["input_equivalent"] = (ie if isinstance(ie, (int, float))
                                         and not isinstance(ie, bool) else None)
            entry["peak_context"] = (peak if isinstance(peak, int)
                                     and not isinstance(peak, bool) else None)
            for key in _BY_KEYS:
                attributed = _int_dict(snap.get(key))
                entry[key] = attributed
                agg = out[key]
                for k, n in attributed.items():
                    agg[k] = agg.get(k, 0) + n
            # A snapshot recorded before cost existed has no cost_usd: its cost is unmeasured
            # (None), never 0, and it stays out of every cost total.
            cost = snap.get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                out["cost_usd"] += cost
                out["cost_measured"] += 1
                measured_costs.append(cost)
                entry["cost_usd"] = cost
                for key in _BY_KEYS:
                    cagg = out["cost_" + key]
                    for k, v in _num_dict(snap.get("cost_" + key)).items():
                        cagg[k] = cagg.get(k, 0.0) + v
        else:
            reason = actual.get("tokens_unmeasured") or "pre-measurement"
            reason = str(reason)
            out["unmeasured"] += 1
            out["unmeasured_reasons"][reason] = out["unmeasured_reasons"].get(reason, 0) + 1
            entry["reason"] = reason
        out["per_sprint"].append(entry)
    if measured_tokens:
        out["median_tokens"] = statistics.median(measured_tokens)
    if measured_costs:
        out["median_cost_usd"] = statistics.median(measured_costs)
    return out
