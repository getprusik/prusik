"""prusik feedback — structured findings capture at the source (Phase 3, Pillar C).

The SCALE backbone for adopter feedback (the bridge is retained only for high-touch
design partners): an agent or operator files a structured finding to
`.sprint/feedback.jsonl` (append-only, zero hot-path). It rides the EXISTING
export → collect rails to the HQ findings-spine — no live author, no per-channel
poll. The record schema here IS the canonical spine record (capture end); the
export carries it anonymized, HQ aggregates it (dedup by `content_hash`,
recurrence across adopters, `status` lifecycle, cycle-time). The loop closes via
release notes referencing the finding `id`, not a live reply.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from prusik import __version__

KINDS = ("bug", "friction", "request", "observation", "question")
SEVERITIES = ("low", "med", "high")


def _store(root: Path) -> Path:
    return root / ".sprint" / "feedback.jsonl"


def content_hash(kind: str, title: str) -> str:
    """Dedup key: same kind+title → same hash, so the HQ spine can collapse
    re-files and count recurrence across adopters."""
    norm = f"{kind}:{' '.join(title.lower().split())}"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def build_record(kind: str, title: str, *, ts: str, detail: str = "",
                 severity: str | None = None, version: str = __version__,
                 phase: str | None = None, feature: str | None = None) -> dict:
    """The canonical findings-spine record. Stable `id` + `content_hash` (dedup),
    `status` (lifecycle, HQ-owned), and the context prusik knows automatically."""
    ch = content_hash(kind, title)
    return {
        "id": f"fb-{ch}",
        "ts": ts,
        "kind": kind,
        "severity": severity,
        "title": title.strip(),
        "detail": detail.strip(),
        "prusik_version": version,
        "phase": phase,
        "feature": feature,
        "content_hash": ch,
        "status": "open",        # HQ owns triage → shipped/wontfix
    }


def canonical_root(root: Path) -> Path:
    """Feedback belongs to the shared PROJECT root, never an ephemeral builder
    worktree. Running `prusik feedback` from inside `…/worktrees/<role>/` (where
    builders run) resolves the project root to the worktree, so the finding would
    be stranded there — invisible to the export, and lost when the worktree is
    cleaned. Resolve up past `worktrees/<role>/`. (An adopter dogfooding, 2026-06-06:
    the channel's first live use filed from a worktree → stranded.)"""
    parts = root.resolve().parts
    if "worktrees" in parts:
        return Path(*parts[:parts.index("worktrees")])
    return root


def _load_file(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def append(root: Path, rec: dict) -> bool:
    """Append one record to the canonical project root, IDEMPOTENTLY: a re-file of
    the same finding (same `id`) is a no-op, so the append-only store stays bounded
    to one line per DISTINCT finding. Without this, an agent that re-files the same
    friction across rewinds/fix-rounds would grow the file unboundedly while the
    spine (which dedups by id at READ) showed no change — this keeps the FILE
    consistent with the read and caps its growth at the source. Zero ceremony, never
    raises — feedback must never break a sprint. Returns True when the finding is
    stored (written now OR already present), False only on write error."""
    root = canonical_root(root)
    fid = rec.get("id")
    try:
        store = _store(root)
        if fid and any(r.get("id") == fid for r in _load_file(store)):
            return True                       # already captured — idempotent no-op
        (root / ".sprint").mkdir(parents=True, exist_ok=True)
        with open(store, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return True
    except OSError:
        return False


def load(root: Path) -> list[dict]:
    return _load_file(_store(canonical_root(root)))


def load_all(root: Path) -> list[dict]:
    """Feedback from the project root AND any builder worktrees, deduped by id —
    so a finding filed from inside a worktree still transports (the export reads
    the root). Recovers already-stranded findings; the write side now writes to
    the canonical root, so this is also defense-in-depth."""
    root = canonical_root(root)
    seen: set[str] = set()
    out: list[dict] = []
    paths = [_store(root)]
    wt = root / "worktrees"
    if wt.exists():
        paths.extend(sorted(wt.glob("*/.sprint/feedback.jsonl")))
    for p in paths:
        for rec in _load_file(p):
            fid = rec.get("id")
            if fid and fid in seen:
                continue
            if fid:
                seen.add(fid)
            out.append(rec)
    return out


def store_bytes(root: Path) -> int:
    """Total on-disk size of the feedback store(s) — bloat TELEMETRY. The idempotent
    writes keep it bounded; this MEASURES rather than assumes, so HQ can see if any
    adopter's append-only store ever grows and decide on EVIDENCE whether retention
    is warranted (instrument before you act). Export-safe: a byte count carries no
    names/paths/content. Sums the canonical root store + any builder-worktree stores
    (the same set load_all reads)."""
    root = canonical_root(root)
    paths = [_store(root)]
    wt = root / "worktrees"
    if wt.exists():
        paths.extend(sorted(wt.glob("*/.sprint/feedback.jsonl")))
    total = 0
    for p in paths:
        try:
            if p.exists():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def file_feedback(root: Path, kind: str, title: str, *, detail: str = "",
                  severity: str | None = None, repro: str = "") -> dict:
    """Build + append a finding, auto-filling sprint context. Emits a best-effort
    `feedback_filed` ledger event so the finding is visible in the adopter's own
    ledger-derived views too."""
    from datetime import datetime

    from prusik import phases
    state = phases.current_sprint_state() or {}
    rec = build_record(
        kind, title, detail=detail, severity=severity,
        ts=datetime.now().astimezone().isoformat(),
        phase=state.get("phase"), feature=state.get("feature"))
    append(root, rec)
    try:
        from prusik import ledger
        if (root / ".sprint").exists():
            ledger.append("feedback_filed", kind=kind, finding_id=rec["id"],
                          severity=severity or "", title=title)
    except Exception:  # noqa: BLE001 — feedback must never break a sprint
        pass
    # Open the per-finding TICKET (findings/fb-<id>.json) — the source-of-truth +
    # conversation + verified-close lifecycle (designed with live-cc). feedback.jsonl
    # stays the append-only creation index; the ticket is the git-tracked record.
    try:
        from prusik import feedback_store
        feedback_store.create(
            canonical_root(root), fb_id=rec["id"], kind=kind, title=title,
            content_hash=rec["content_hash"], detail=detail, severity=severity,
            feature=state.get("feature"), repro=repro)
    except Exception as e:  # noqa: BLE001 — never BREAKS filing, but must not HIDE a lost ticket
        print(f"[prusik] warning: finding {rec['id']} was filed but its durable ticket "
              f"could not be written ({e!r}) — it may not track or auto-close on update. "
              f"Check findings/ and re-file if it is missing.", file=sys.stderr)
    _push_to_sink(root, rec)
    return rec


def _push_to_sink(root: Path, rec: dict) -> str | None:
    """Opt-in low-latency delivery. Telemetry is pull-only (never auto-transmit
    incidental data). Feedback is DIFFERENT: filing a finding IS the consent to
    send it — so when the adopter has opted in by configuring `feedback_sink`,
    we deliver the ANONYMIZED finding to that drop the instant it's filed, instead
    of making it wait for the next export/pull. Same anonymization as the export
    (detail + feature stay LOCAL); off unless the adopter sets a sink. Returns the
    drop path written, or None (not configured / unwritable — best-effort, never
    breaks `prusik feedback`)."""
    try:
        from prusik import phases, report
        config = phases.load_sprint_config(root) or {}
        sink = config.get("feedback_sink")
        if not sink:
            return None
        # Honor the same trust opt-in as the export: a partner who carries verbatim
        # detail in their export carries it on the low-latency path too.
        anon = report._anonymize_feedback(
            [rec], include_detail=bool(config.get("feedback_include_detail")))
        if not anon:
            return None
        item = anon[0]
        item["adopter"] = report._product_hash(root)
        item["first_seen"] = rec.get("ts")
        sink_dir = Path(sink).expanduser()
        sink_dir.mkdir(parents=True, exist_ok=True)
        drop = sink_dir / f"{item['adopter']}.fb.jsonl"
        if item.get("id") and any(r.get("id") == item["id"] for r in _load_file(drop)):
            return str(drop)                  # already delivered — idempotent, bounded
        with open(drop, "a", encoding="utf-8") as f:
            f.write(json.dumps(item) + "\n")
        return str(drop)
    except Exception:  # noqa: BLE001 — delivery is best-effort, never fatal
        return None


_TICKET_VERBS = {"show", "reply", "resolve", "verify", "migrate", "harvest"}


def migrate_to_tickets(root: Path) -> tuple[int, int]:
    """Backfill the legacy `.sprint/feedback.jsonl` index into per-finding tickets
    `findings/fb-<id>.json`, so an existing backlog becomes loop-managed (live-cc,
    an adopter had 13 findings stranded in the old index with no tickets). Idempotent —
    `create` returns an existing ticket untouched (never clobbers a thread/resolution).
    Returns (newly_created, total_seen)."""
    from prusik import feedback_store
    new = total = 0
    for r in load_all(root):
        fid = r.get("id")
        if not fid:
            continue
        total += 1
        if feedback_store.load(root, fid) is None:
            new += 1
        feedback_store.create(
            root, fb_id=fid, kind=r.get("kind", "friction"),
            title=r.get("title", ""), content_hash=r.get("content_hash", ""),
            detail=r.get("detail", ""), severity=r.get("severity"),
            feature=r.get("feature"))
    return new, total


def run(args) -> int:
    from prusik import ledger
    root = ledger.project_root()
    # Ticket verbs: `prusik feedback <verb> <fb-id> ...` (the per-finding loop).
    if getattr(args, "title", None) in _TICKET_VERBS:
        return _run_ticket_verb(args, canonical_root(root))
    if getattr(args, "list", False):
        recs = load(root)
        if not recs:
            print("[prusik-feedback] no findings filed yet.")
            return 0
        print(f"[prusik-feedback] {len(recs)} finding(s) (ride the next export to HQ):")
        for r in recs:
            sev = f" [{r['severity']}]" if r.get("severity") else ""
            print(f"  {r['id']}  {r['kind']:11s}{sev}  {r.get('status', 'open'):7s}  "
                  f"{r['title']}")
        return 0
    if not getattr(args, "title", None):
        print("[prusik-feedback] give a title — e.g. "
              "`prusik feedback \"scoped coverage false-fails\" --kind bug`",
              file=sys.stderr)
        return 2
    rec = file_feedback(root, args.kind, args.title,
                        detail=getattr(args, "detail", "") or "",
                        severity=getattr(args, "severity", None),
                        repro=getattr(args, "repro", "") or "")
    print(f"[prusik-feedback] filed {rec['id']} ({rec['kind']}) — "
          f"ticket findings/{rec['id']}.json opened; rides the next export to HQ.")
    return 0


def _run_ticket_verb(args, root: Path) -> int:
    """show / reply / resolve / verify on a per-finding ticket (feedback_store)."""
    from prusik import feedback_store as fs
    verb, fid = args.title, getattr(args, "ref", None)

    if verb == "migrate":
        new, total = migrate_to_tickets(root)
        print(f"[prusik-feedback] migrated {new} new ticket(s) into findings/ "
              f"({total} finding(s) in the index; existing tickets untouched).")
        return 0

    if verb == "verify" and (getattr(args, "all_closed", False)
                             or getattr(args, "touched", None) is not None):
        results = fs.verify_selector(root, all_closed=getattr(args, "all_closed", False),
                                     touched=getattr(args, "touched", None))
        if not results:
            print("[prusik-feedback] no matching verified-closed findings to re-run.")
            return 0
        for rid, state, entry in results:
            mark = "✓ holds" if state == "verified-closed" else f"⚠ {state}"
            print(f"  {rid}: {mark} (exit {entry['exit']}, "
                  f"{entry['executed_count']} executed)")
        return 0

    if not fid:
        print(f"[prusik-feedback] `feedback {verb}` needs an fb-<id>.", file=sys.stderr)
        return 2
    if fs.load(root, fid) is None:
        print(f"[prusik-feedback] no ticket {fid} (findings/{fid}.json)", file=sys.stderr)
        return 2

    if verb == "show":
        return _show_ticket(fs, root, fid)
    if verb == "reply":
        if not args.body:
            print("[prusik-feedback] reply needs --body", file=sys.stderr)
            return 2
        fs.reply(root, fid, args.role, args.body)
        print(f"[prusik-feedback] {args.role} → {fid}: {args.body[:60]}")
        return 0
    if verb == "resolve":
        if args.reject:
            fs.resolve(root, fid, rtype="reject", reason=args.reason)
            print(f"[prusik-feedback] {fid} → wontfix (rejected): {args.reason}")
        elif args.fix:
            if not args.verify_cmd:
                print("[prusik-feedback] --fix REQUIRES --verify-cmd (closure is "
                      "gated on a green run)", file=sys.stderr)
                return 2
            fs.resolve(root, fid, rtype="fix", verify=args.verify_cmd,
                       fixed_in=args.fixed_in, verify_kind=args.verify_kind)
            print(f"[prusik-feedback] {fid} → fixed (unverified) — run "
                  f"`prusik feedback verify {fid}` to close on proof.")
        else:
            print("[prusik-feedback] resolve needs --fix or --reject", file=sys.stderr)
            return 2
        return 0
    if verb == "verify":
        result = fs.verify(root, fid)
        if result is None:
            return 2
        state, entry = result
        ok = "✓ verified-closed" if state == "verified-closed" else f"⚠ {state}"
        print(f"[prusik-feedback] {fid}: {ok} — {entry['why']}")
        if state == "verified-closed":
            vrec = fs.load(root, fid)
            cand = fs.harvest_candidate(vrec) if vrec else None
            if cand and f"moat-finding: {fid}" not in (
                    (root / cand).read_text() if (root / cand).exists() else ""):
                print(f"  ↳ harvest: {cand} is a new finding-derived test — "
                      f"`prusik feedback harvest {fid}` to tag it into the moat.")
        return 0 if state == "verified-closed" else 1
    if verb == "harvest":
        tagged = fs.harvest(root, fid)
        if tagged:
            print(f"[prusik-feedback] harvested {fid} → tagged {tagged} "
                  f"(moat-finding:{fid}); hq.moat_coverage now counts it.")
            return 0
        print(f"[prusik-feedback] nothing to harvest for {fid} — not verified-closed, "
              f"no dedicated new test, or already tagged (honest moat: one tag per "
              f"genuinely-new finding-derived test).")
        return 0
    return 2


def _show_ticket(fs, root: Path, fid: str) -> int:
    rec = fs.load(root, fid)
    state = fs.derive_state(rec)
    print(f"━━ {fid}  [{rec.get('severity') or '-'}] {rec['kind']}  →  {state}")
    print(f"   {rec['title']}")
    if rec.get("detail"):
        print(f"   detail: {rec['detail'][:200]}")
    res = rec.get("resolution")
    if res:
        if res["type"] == "fix":
            print(f"   resolution: FIX {res.get('fixed_in') or ''} · "
                  f"verify: {res['verify']}")
        else:
            print(f"   resolution: REJECT — {res['reason']}")
    for t in rec.get("thread", []):
        print(f"   • [{t['at'][:16]}] {t['role']}: {t['body']}")
    for h in rec.get("verify_history", []):
        v = "green" if h["verdict"] else "RED"
        print(f"   ⟳ [{h['at'][:16]}] verify {v} (exit {h['exit']}, "
              f"{h['executed_count']} executed, @{h['worktree_hash']})")
    return 0
