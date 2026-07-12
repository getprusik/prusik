"""Append-only event ledger at .sprint/ledger.jsonl.

Durable audit trail across phase transitions, gate blocks, triage decisions,
and sprint completions. A fresh session can reconstruct state from this file
alone.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def project_root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return _canonical_worktree_root(Path(env))
    p = Path.cwd()
    while p != p.parent:
        if (p / ".sprint").exists() or (p / ".claude").exists():
            return _canonical_worktree_root(p)
        p = p.parent
    return Path.cwd()


_CANON_CACHE: dict[str, Path] = {}


def _canonical_worktree_root(root: Path) -> Path:
    """Resolve a LINKED git worktree to the sprint's canonical root.

    A linked worktree checks out `.claude/` (and any tracked dir) from the
    branch, so the cwd-walk above stops AT the worktree — not at the canonical
    root where `worktrees/`, `reports/`, and `.sprint/` actually live. A reviewer
    that runs `prusik gate capture` from inside `worktrees/solo` would then write
    evidence to `worktrees/solo/reports/` (invisible to the root gate) and hash a
    `worktrees/` set that doesn't exist under the worktree → a permanent
    tests=0 / "stale" bounce the operator could only escape by manually re-running
    from the repo root (fb-b587d8d9b71c). Canonicalizing here makes write,
    read, and the worktree-hash agree regardless of the reviewer's cwd.

    Cheap by construction: git is consulted ONLY when `.git` is a FILE (the
    linked-worktree marker). At the canonical root `.git` is a directory, so the
    common path never shells out. Redirects only when the resolved parent
    genuinely holds this sprint's `worktrees/` + `.sprint/` — never otherwise.

    Memoized per input path: `project_root()` is uncached and called twice per
    PreToolUse hook (load_sprint_config + current_sprint_state), so a reviewer
    subagent whose cwd is a worktree would otherwise spawn `git rev-parse` on
    EVERY tool call. The git topology of a path is static within a process, so the
    resolution is safe to cache by path — and caching here, not in project_root(),
    keeps the cwd-walk itself fresh (a `.sprint` that appears mid-process is still
    seen). Keyed by string path so distinct test tmp_paths never collide."""
    key = str(root)
    cached = _CANON_CACHE.get(key)
    if cached is not None:
        return cached
    resolved = _resolve_canonical(root)
    _CANON_CACHE[key] = resolved
    return resolved


def _resolve_canonical(root: Path) -> Path:
    gitfile = root / ".git"
    if not gitfile.is_file():
        return root
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(root), capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, ValueError):
        return root
    if r.returncode != 0 or not r.stdout.strip():
        return root
    common = Path(r.stdout.strip())
    if not common.is_absolute():
        common = (root / common).resolve()
    cand = common.parent
    if (cand / "worktrees").exists() and (cand / ".sprint").exists():
        return cand
    return root


def ledger_path(root: Path | None = None) -> Path:
    return (root or project_root()) / ".sprint" / "ledger.jsonl"


def append(event_type: str, **fields) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        **fields,
    }
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


_LEDGER_CACHE: dict[str, tuple[int, float, list[dict]]] = {}


def read_all() -> list[dict]:
    """Parsed ledger events, newest-last. Memoized on (size, mtime).

    27 call-sites mine the ledger (catch_quality, calibration, convergence,
    fix_round, consistency, …), several within a single command, and the file is
    append-only and UNBOUNDED — so re-parsing the whole `ledger.jsonl` on every
    call was O(project-age) work repeated N times per command, the dominant
    history-read cost as a project accrues sprints. The ledger only ever GROWS
    (append-only), so any write changes the byte size: keying the cache on
    (st_size, st_mtime) auto-invalidates on the next read after an `append` — in
    THIS process or another — with no explicit invalidation to forget. A fresh
    list copy is returned so a caller mutating the result can't poison the cache."""
    path = ledger_path()
    try:
        st = path.stat()
    except OSError:
        return []
    key = str(path)
    cached = _LEDGER_CACHE.get(key)
    if cached is not None and cached[0] == st.st_size and cached[1] == st.st_mtime:
        return list(cached[2])
    with open(path) as f:
        records = [json.loads(line) for line in f if line.strip()]
    _LEDGER_CACHE[key] = (st.st_size, st.st_mtime, records)
    return list(records)


def digest(by_size: bool = False) -> int:
    records = read_all()
    if not records:
        print("Ledger is empty.")
        return 0

    by_event: dict[str, list] = defaultdict(list)
    for r in records:
        by_event[r["event"]].append(r)

    print(f"Ledger: {len(records)} events across {len(by_event)} event types")
    for event, rs in sorted(by_event.items()):
        print(f"  {event:26s} {len(rs):4d}")

    sprints = by_event.get("sprint_complete", [])
    if sprints and by_size:
        # v0.5.0: --by-size groups durations by predicted.size.
        print("\nSprint outcomes by size (predicted size / mean actual duration_min):")
        buckets: dict[str, list[int]] = defaultdict(list)
        for s in sprints:
            size = s.get("predicted", {}).get("size") or "?"
            dur = s.get("actual", {}).get("duration_min")
            if isinstance(dur, (int, float)):
                buckets[size].append(int(dur))
        for size, durs in sorted(buckets.items()):
            mean = sum(durs) / len(durs)
            print(f"  {size:3s} n={len(durs):3d}  mean={mean:6.1f}  "
                  f"min={min(durs):4d}  max={max(durs):4d}")

    if sprints:
        print(f"\nSprint outcomes ({len(sprints)}):")
        escalations = sum(1 for s in sprints if s.get("actual", {}).get("escalated"))
        rate = 100 * escalations / len(sprints)
        print(f"  solo→team escalation rate: {escalations}/{len(sprints)} ({rate:.0f}%)")
        duration_errs, token_errs = [], []
        for s in sprints:
            p, a = s.get("predicted", {}), s.get("actual", {})
            if p.get("duration_min") and a.get("duration_min"):
                duration_errs.append(a["duration_min"] - p["duration_min"])
            if p.get("tokens") and a.get("tokens"):
                token_errs.append(a["tokens"] - p["tokens"])
        if duration_errs:
            mean_err = sum(duration_errs) / len(duration_errs)
            print(f"  mean duration error: {mean_err:+.1f} min "
                  f"(positive = underestimated)")
        if token_errs:
            mean_err = sum(token_errs) / len(token_errs)
            print(f"  mean token error:    {mean_err:+,.0f} tokens")
        print("\n  recent:")
        for s in sprints[-5:]:
            p, a = s.get("predicted", {}), s.get("actual", {})
            esc = " [escalated]" if a.get("escalated") else ""
            print(f"    {s.get('feature', '?'):24s} "
                  f"mode {p.get('mode', '?')} size {p.get('size', '?')} "
                  f"dur {p.get('duration_min', '?')}→{a.get('duration_min', '?')}{esc}")

    blocks = by_event.get("gate_blocked", [])
    if blocks:
        phase_counts = Counter(b.get("phase") for b in blocks)
        print(f"\nGate blocks: {len(blocks)} total")
        for phase, n in phase_counts.most_common(5):
            print(f"  {phase:16s} {n}")
        print("  recent (last 3):")
        for b in blocks[-3:]:
            print(f"    phase={b.get('phase')} tool={b.get('tool')}: {b.get('reason')}")

    incidents = by_event.get("watchdog_incident", [])
    if incidents:
        kinds = Counter(i.get("kind") for i in incidents)
        print(f"\nWatchdog incidents: {len(incidents)}")
        for kind, n in kinds.most_common():
            print(f"  {kind:20s} {n}")

    triages = by_event.get("triage_decision", [])
    if triages:
        modes = Counter(t.get("mode") for t in triages)
        print(f"\nTriage decisions: {dict(modes)}")

    fallbacks = by_event.get("reviewer_fallback_used", [])
    if fallbacks:
        by_role = Counter(f.get("role") for f in fallbacks)
        print(f"\nReviewer-artifact fallbacks (v0.5.0+): {len(fallbacks)} total")
        for role, n in by_role.most_common():
            print(f"  {role:22s} {n}")
        print("  (low counts → fallback is a safety net; high counts → agent "
              "prompts need tightening)")

    return 0
