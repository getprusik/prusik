"""Agent-readable findings — v0.26.0.

Closes the operator-in-the-loop bottleneck. Before v0.26.0, prusik findings
flowed: prusik flags → operator reads stdout → operator tells agent →
agent fixes. Three steps, human-bound in the middle, so flags shipped
that no one read.

v0.26.0 ships a STABLE JSON CONTRACT plus a `prusik findings` CLI that
the agent itself can consume. Schema is versioned so the agent's
prompt convention can rely on field shapes. `--since last-turn`
filters to events the agent hasn't consumed yet; emitting a
`findings_consumed` ledger event when the agent reads moves the
cursor forward (the agent doesn't re-process old flags).

Schema (schema_version "1.0"):
  {
    "schema_version": "1.0",
    "stats": {"count": N, "since": "..."},
    "findings": [
      {
        "id": "<event-class>:<stable-key>",
        "kind": "binding_mismatch" | "test_reach" | "gate_block" |
                "suspect_skip" | ...,
        "ts": "<ISO8601>",
        "severity": "info" | "medium" | "high",
        "summary": "<one-line human-readable>",
        "details": {<event-shape-specific fields>},
        "suggested_action": "<actionable next step>"
      }
    ]
  }

Sources:
  - ledger: events recorded by prusik's gates (binding-flagged,
    test-set-reach, suspect-skip, gate-blocked). The default — these
    are the agent's regular feedback channel.
  - scan: synthesizes a fresh `prusik scan` run as findings. Useful when
    the agent wants to ask "what would prusik flag right now?"
    without waiting for a gate.

Honest scope:
  - The contract is "v1.0" — additive changes only within the major
    version. Field renames or removals require schema_version bump.
  - Agent self-correction is a USE CASE the contract enables, not a
    capability prusik enforces. The agent's prompt convention is
    documented in the slash-command surface (separate ship; this
    ship lands the contract + CLI).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from prusik import ledger


# Schema version — bumped on incompatible changes only. Additive
# field changes do NOT bump (clients tolerate unknown fields).
# v1.1: each finding carries a `detector` field (built-in or custom). Additive
# (clients tolerate unknown fields), but bumped so consumers can rely on it.
SCHEMA_VERSION = "1.1"


# Map of ledger event types → finding kind. Events not in this map are
# not surfaced as findings (e.g. phase_advance is workflow noise, not
# actionable feedback).
_FINDING_EVENT_MAP: dict[str, str] = {
    "reviewer_binding_flagged": "binding_mismatch",
    "reviewer_test_set_reach": "test_reach",
    "reviewer_skip_flagged": "suspect_skip",
    "gate_blocked": "gate_block",
    "advance_blocked": "gate_block",
    "fix_round_start": "fix_round",
    "detector_flagged": "detector",   # v1.1 — custom / pluggable detectors
}

# event → built-in detector name (for the `detector` field on v1.1 findings).
_EVENT_DETECTOR: dict[str, str] = {
    "reviewer_binding_flagged": "binding",
    "reviewer_test_set_reach": "test-reach",
}


def _event_detector(event: dict) -> str | None:
    ev = event.get("event", "")
    if ev == "detector_flagged":
        return event.get("detector")
    return _EVENT_DETECTOR.get(ev)


def _stable_id(event: dict) -> str:
    """A stable id per (event-class, salient-key) so the agent can
    dedupe across re-emissions. Not cryptographic — just enough to
    treat duplicates as one."""
    kind = _FINDING_EVENT_MAP.get(event.get("event", ""), "other")
    # Pick a stable key per kind. Falls back to ts if nothing better.
    salient: tuple[Any, ...]
    if kind == "binding_mismatch":
        salient = (event.get("template", ""), event.get("url", ""),
                   event.get("form_name", ""), event.get("binding_class", ""))
    elif kind == "test_reach":
        salient = (event.get("contract_id", ""),
                   event.get("contract_class", ""))
    elif kind == "suspect_skip":
        salient = (event.get("test_id", ""), event.get("reason", ""))
    elif kind == "gate_block":
        salient = (event.get("tool", ""), event.get("command", ""),
                   event.get("reason", ""))
    elif kind == "detector":
        salient = (event.get("detector", ""), event.get("cls", ""),
                   event.get("file", ""), event.get("line", ""))
    else:
        salient = (event.get("ts", ""),)
    return f"{kind}:{'|'.join(str(s) for s in salient)}"


def _summarize(event: dict) -> str:
    """One-line human-readable summary per finding kind."""
    kind = _FINDING_EVENT_MAP.get(event.get("event", ""), "other")
    if kind == "binding_mismatch":
        binding_class = event.get("binding_class", "?")
        if binding_class == "fetch_url":
            return (f"Template fetches {event.get('url', '?')!r} but no "
                    f"touched route resolves there")
        elif binding_class == "form_name":
            return (f"Template emits <input name="
                    f"{event.get('form_name', '?')!r}> but no touched "
                    f"handler reads it")
        return f"Binding mismatch flagged ({binding_class})"
    if kind == "test_reach":
        return (f"Touched contract {event.get('contract_id', '?')!r} "
                f"referenced by tests OUTSIDE the touched set")
    if kind == "suspect_skip":
        return f"Suspect skip flagged: {event.get('test_id', '?')}"
    if kind == "gate_block":
        return (f"Gate blocked {event.get('tool', '?')}: "
                f"{event.get('reason', event.get('command', '?'))}")
    if kind == "fix_round":
        return f"Fix round started for {event.get('feature', '?')}"
    if kind == "detector":
        return (event.get("summary")
                or f"{event.get('detector', '?')} flagged: {event.get('cls', '?')}")
    return f"Event: {event.get('event', '?')}"


def _suggest_action(event: dict) -> str:
    """Actionable next step per finding. The agent can act on this."""
    kind = _FINDING_EVENT_MAP.get(event.get("event", ""), "other")
    if kind == "binding_mismatch":
        binding_class = event.get("binding_class", "")
        expected = event.get("expected", [])
        if binding_class == "fetch_url" and expected:
            return (f"Change the fetch URL to {expected[0]!r} "
                    f"(the qualified path on the prefixed router)")
        if binding_class == "form_name":
            return ("Either rename the <input name=...> to match a "
                    "handler key OR update the handler to read the "
                    "template's name")
        return "Adjudicate the binding and apply the cross-module fix"
    if kind == "test_reach":
        return ("Either pull the reaching test(s) into the touched set "
                "OR rely on the post-integration full-suite gate")
    if kind == "suspect_skip":
        return ("Investigate the skip — prusik's heuristic flagged it as "
                "a likely test-bypass; either un-skip or document why")
    if kind == "gate_block":
        return ("Read the gate-block reason; apply the named fix or "
                "request operator override (--allow-rewind / explicit "
                "exception path)")
    if kind == "detector":
        return (event.get("suggested_action")
                or "Adjudicate the flagged finding, then fix or document it")
    return "Review the event and decide if action is needed"


def _severity(event: dict) -> str:
    """Per-kind severity. Adjusted later as we learn FP rates."""
    kind = _FINDING_EVENT_MAP.get(event.get("event", ""), "other")
    if kind == "detector":
        return event.get("severity", "medium")  # detector self-declares
    if kind in ("binding_mismatch", "suspect_skip"):
        return "medium"
    if kind == "gate_block":
        return "high"
    return "info"


def _detector_finding_to_contract(f, ts: str) -> dict:
    """Normalized detector Finding → findings-schema (v1.1) dict.

    Built-in detectors route through the same pseudo-event path the ledger
    source uses, so their summary/suggested_action are unchanged. Custom
    detectors get a generic `kind: detector` mapping carrying their own
    severity/message."""
    if f.detector == "binding":
        fd = _event_to_finding({
            "event": "reviewer_binding_flagged", "ts": ts,
            "binding_class": f.cls,
            "template": f.file or "",
            "url": f.meta.get("url"),
            "form_name": f.meta.get("name"),
            "expected": list(f.expected),
            "source": "scan",
        })
        if f.suggested_test:
            fd["suggested_test"] = f.suggested_test
        return fd
    if f.detector == "test-reach":
        return _event_to_finding({
            "event": "reviewer_test_set_reach", "ts": ts,
            "contract_id": f.meta.get("contract_id"),
            "contract_class": f.cls,
            "source": "scan",
        })
    # Custom / future detector — generic mapping.
    details = {"class": f.cls, "file": f.file, "line": f.line,
               "expected": list(f.expected)}
    details.update(f.meta)
    fd = {
        "id": f"detector:{f.detector}:{f.cls}:{f.file or ''}:{f.line or ''}",
        "kind": "detector",
        "detector": f.detector,
        "ts": ts,
        "severity": f.severity,
        "summary": f.message,
        "details": details,
        "suggested_action": "Adjudicate the flagged finding, then fix or document it",
    }
    if f.suggested_test:
        fd["suggested_test"] = f.suggested_test
    return fd


def _event_to_finding(event: dict) -> dict:
    """Convert one ledger event to the stable findings-schema shape."""
    return {
        "id": _stable_id(event),
        "kind": _FINDING_EVENT_MAP.get(event.get("event", ""), "other"),
        "detector": _event_detector(event),
        "ts": event.get("ts", ""),
        "severity": _severity(event),
        "summary": _summarize(event),
        "details": {k: v for k, v in event.items()
                     if k not in ("event", "ts")},
        "suggested_action": _suggest_action(event),
    }


def _cursor_ts() -> str | None:
    """Return the ts of the most recent `findings_consumed` event, or
    None if none exists. This is the agent's read-cursor."""
    events = ledger.read_all()
    for ev in reversed(events):
        if ev.get("event") == "findings_consumed":
            return ev.get("ts")
    return None


def collect(since: str | None = None,
            source: str = "ledger") -> dict:
    """Collect findings from the requested source, optionally filtered
    to events newer than `since`.

    since:
      - None: all findings (no cursor filter)
      - "last-turn": use the most recent findings_consumed cursor
      - <ISO8601>: explicit timestamp

    source:
      - "ledger": events from .sprint/ledger.jsonl
      - "scan": run prusik scan against project root and synthesize findings
      - "both": ledger findings + scan findings

    Returns the contract dict (see module docstring).
    """
    findings: list[dict] = []

    # Resolve `since`
    since_ts: str | None
    if since == "last-turn":
        since_ts = _cursor_ts()
    elif since is None:
        since_ts = None
    else:
        since_ts = since

    if source in ("ledger", "both"):
        events = ledger.read_all()
        for ev in events:
            if ev.get("event") not in _FINDING_EVENT_MAP:
                continue
            if since_ts and ev.get("ts", "") <= since_ts:
                continue
            findings.append(_event_to_finding(ev))

    if source in ("scan", "both"):
        # Scan synthesis: run the detector registry (built-ins + project-local
        # .claude/detectors/*) and synthesize a finding per result. v1.1 — every
        # detector (incl. custom) surfaces here, each tagged with its `detector`.
        # No cursor filter — scan output is always "current state".
        from prusik import scan as kit_scan
        from prusik import detectors as _detreg
        from prusik.detectors.base import ScanContext
        root = ledger.project_root()
        files, _stats = kit_scan._collect_files(
            root, file_limit=5000, include_test_reach=False)
        if files:
            now_ts = datetime.now(timezone.utc).isoformat()
            cfg = kit_scan._detector_config(root)
            registry = _detreg.load(root, cfg)
            registry.pop("test-reach", None)  # opt-in in scan mode
            for name in sorted(registry):
                try:
                    results = registry[name].detect(ScanContext(
                        root=root, files=files, config=cfg))
                except Exception as e:  # a detector must not break findings — but say so
                    print(f"[prusik] warning: detector {name!r} failed and was skipped "
                          f"({e!r}) — its findings are absent from this report.",
                          file=sys.stderr)
                    continue
                for f in results:
                    findings.append(_detector_finding_to_contract(f, now_ts))

    return {
        "schema_version": SCHEMA_VERSION,
        "stats": {
            "count": len(findings),
            "since": since_ts,
            "source": source,
        },
        "findings": findings,
    }


def mark_consumed(through_ts: str | None = None) -> None:
    """Advance the cursor by appending a findings_consumed event.

    If through_ts is None, uses 'now' — meaning subsequent --since
    last-turn returns no findings until new ones arrive. This is the
    agent's "I've processed these" signal.
    """
    ts = through_ts or datetime.now(timezone.utc).isoformat()
    ledger.append("findings_consumed", through_ts=ts)


def run(since: str | None = None,
        source: str = "ledger",
        consume: bool = False,
        json_output: bool = True) -> int:
    """CLI entry. Returns rc=0 always — findings are signal, not error;
    the CALLER (agent, CI) decides what to do with them.

    consume=True: append a findings_consumed event after emitting,
    advancing the cursor. The agent calls this when it commits to
    processing the returned findings.
    """
    out = collect(since=since, source=source)

    if json_output:
        print(json.dumps(out, indent=2, default=str))
    else:
        # Human-readable
        since_str = out['stats']['since']
        since_suffix = f' since {since_str}' if since_str else ''
        print(f"[prusik-findings] {out['stats']['count']} finding(s)"
              f"{since_suffix} (source: {source}, "
              f"schema: {SCHEMA_VERSION})\n")
        for f in out["findings"]:
            print(f"  ⚠ [{f['severity']}] {f['kind']}  {f['summary']}")
            print(f"      → {f['suggested_action']}")
            print(f"      ts: {f['ts']}, id: {f['id']}")
            print()

    if consume:
        mark_consumed()
        if not json_output:
            print("[prusik-findings] cursor advanced (findings_consumed event)")

    return 0
