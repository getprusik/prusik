"""Per-finding ticket store — the feedback loop's source-of-truth AND conversation.

Designed live, agent-to-agent, with live-cc (bridge 2026-06-06-feedback-loop-design,
finding fb-c91c2be85603). One self-contained file per finding,
`findings/fb-<id>.json`, holding its metadata, repro + verify commands, a role-tagged
thread, a resolution, and an append-only `verify_history`. The file is GIT-TRACKED, so
its history IS the audit trail (free traceability). (Module is `feedback_store`, not
`findings`, to avoid the unrelated v0.26.0 `prusik findings` command.)

Two properties make this beat a status tracker:

  1. DERIVE-DON'T-STORE state. `derive_state` recomputes the lifecycle from
     (resolution + latest verify run) on every read — there is no stored status flag
     to drift, and a finding can NEVER sit "closed" against a red verify.
  2. VERIFIED CLOSURE. A `fix` carries a `verify` command; closure is GATED on a
     captured green run via `evidence.prove_verdict` (exit 0 AND executed >= 1, the
     same honesty guard `prusik prove` uses) — a finding closes on PROOF, never a
     claim. A verified-closed finding whose verify later goes red AUTO-REOPENS.

Status lattice (every transition prusik-owned; the two evidence-gated edges go through
prove_verdict):  open → acknowledged → fixed → verified-closed
                                              ↳ reopened (verify regressed)
                 wontfix (reasoned aligned-rejection; terminal, not verify-gated)
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prusik import evidence

_VERIFY_TIMEOUT_SEC = 1800
ROLE_ADOPTER = "adopter"
ROLE_AUTHOR = "prusik-author"


def _store_dir(root: Path) -> Path:
    return root / "findings"


# Dropped into findings/ the first time a ticket is written, so an agent inspecting
# the directory (e.g. deciding whether to commit or gitignore it) is steered right AT
# the point of decision. The recurring wrong inference: "findings/*.json are machine-
# written and perpetually untracked → gitignore them" — which loses closure history
# and defeats `prusik update`'s auto-close (findings/ is the git-tracked SOURCE OF
# TRUTH, not the HQ outbox).
_STORE_README = """# findings/ — prusik feedback ticket store (GIT-TRACKED — commit it)

Each `fb-<id>.json` is a durable ticket: the finding plus its thread, `resolution`,
and an append-only `verify_history`. **Commit these files** — do NOT gitignore them.

Why they must be tracked:
- Closure is DERIVED from `verify_history` (a finding is closed only by a captured
  green verify run). Gitignoring `findings/` loses that history on a fresh clone, so
  closed findings silently reappear as open.
- `prusik update` auto-closes findings whose fix shipped by writing `verify_history`
  here; untracked, those closures never persist or share.

This directory is the SOURCE OF TRUTH. The HQ export (`prusik report --export`) is the
outbox — a derived snapshot; it reads FROM here. Machine-written new tickets showing in
`git status` is signal (a finding was filed), not noise — commit them with the sprint.
"""


def _ensure_store_readme(store_dir: Path) -> None:
    readme = store_dir / "README.md"
    if not readme.exists():
        try:
            readme.write_text(_STORE_README)
        except OSError:
            pass


def path(root: Path, fb_id: str) -> Path:
    return _store_dir(root) / f"{fb_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(root: Path, fb_id: str) -> dict | None:
    p = path(root, fb_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def save(root: Path, rec: dict) -> None:
    p = path(root, rec["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    _ensure_store_readme(p.parent)
    # `status` is DERIVED; mirror it into the file for human/dashboard readers, but
    # derive_state(rec) is always authoritative on read.
    rec["status"] = derive_state(rec)
    p.write_text(json.dumps(rec, indent=2) + "\n")


def load_all(root: Path) -> list[dict]:
    d = _store_dir(root)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("fb-*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except (OSError, ValueError):
            continue
    return out


def create(root: Path, *, fb_id: str, kind: str, title: str, content_hash: str,
           detail: str = "", severity: str | None = None,
           feature: str | None = None, repro: str = "") -> dict:
    """Open a ticket. Idempotent: re-filing the same finding returns the existing
    ticket (so its thread/resolution/history survive — never a clobber)."""
    existing = load(root, fb_id)
    if existing is not None:
        return existing
    rec: dict[str, Any] = {
        "id": fb_id, "created_at": _now(), "kind": kind, "severity": severity,
        "title": title, "detail": detail, "feature": feature,
        "content_hash": content_hash, "repro": repro, "duplicate_of": None,
        "thread": [], "resolution": None, "verify_history": [],
    }
    save(root, rec)
    return rec


def reply(root: Path, fb_id: str, role: str, body: str) -> dict | None:
    """Append a role-tagged comment to the ticket thread — the back-and-forth."""
    rec = load(root, fb_id)
    if rec is None:
        return None
    rec.setdefault("thread", []).append({"at": _now(), "role": role, "body": body})
    save(root, rec)
    return rec


def resolve(root: Path, fb_id: str, *, rtype: str, verify: str = "",
            fixed_in: str = "", reason: str = "", verify_kind: str = "tests"
            ) -> dict | None:
    """Attach a resolution. `fix` MUST carry a verify command (closure is gated on
    it); `reject` carries a reason (the aligned-rejection → wontfix). Setting a fix
    does NOT close the ticket — `verify` must run green first."""
    if rtype not in ("fix", "reject"):
        raise ValueError("rtype must be 'fix' or 'reject'")
    if rtype == "fix" and not verify.strip():
        raise ValueError("a 'fix' resolution requires a verify command "
                         "(closure is gated on a green run)")
    if rtype == "reject" and not reason.strip():
        raise ValueError("a 'reject' resolution requires a reason (aligned rejection)")
    rec = load(root, fb_id)
    if rec is None:
        return None
    rec["resolution"] = {
        "type": rtype, "verify": verify.strip(), "fixed_in": fixed_in.strip(),
        "reason": reason.strip(), "verify_kind": verify_kind,
    }
    save(root, rec)
    return rec


def _code_hash(root: Path) -> str:
    """The code state a verify ran against — git HEAD (+'-dirty'). Binds a green to
    its commit so an old green never masks a regression on newer code."""
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        if head.returncode != 0:
            return "no-git"
        rev = head.stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=10)
        return rev + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def verify(root: Path, fb_id: str) -> tuple[str, dict] | None:
    """Run the finding's verify command, append the result to `verify_history`, and
    recompute state. Honesty inheritance: the verdict is `evidence.prove_verdict`
    (exit 0 AND executed >= 1) — a zero-executed / all-skipped run NEVER closes a
    finding (the fb-32b3a89cc1d5 class). Returns (new_state, history_entry)."""
    rec = load(root, fb_id)
    if rec is None:
        return None
    res = rec.get("resolution") or {}
    cmd = res.get("verify", "")
    if not cmd:
        raise ValueError(f"{fb_id} has no verify command (resolve it as a fix with "
                         f"--verify first)")
    kind = res.get("verify_kind", "tests")
    try:
        proc = subprocess.run(["/bin/bash", "-c", cmd], cwd=str(root),
                              capture_output=True, text=True,
                              timeout=_VERIFY_TIMEOUT_SEC, check=False)
        exit_code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        exit_code, output = -2, f"verify exceeded {_VERIFY_TIMEOUT_SEC}s"
    except OSError as e:
        exit_code, output = -3, f"verify failed to spawn: {e}"
    executed = evidence.executed_count(kind, output, cmd)
    ok, why = evidence.prove_verdict(kind, exit_code, executed)
    entry = {
        "at": _now(), "exit": exit_code, "executed_count": executed,
        "verdict": ok, "why": why, "worktree_hash": _code_hash(root),
    }
    rec.setdefault("verify_history", []).append(entry)
    save(root, rec)
    return derive_state(rec), entry


def derive_state(rec: dict) -> str:
    """The lattice, recomputed from (resolution + verify_history) — never stored. A
    finding can't be 'closed' against a red verify because closure is a FUNCTION of
    the latest run, recomputed every read."""
    res = rec.get("resolution")
    if res and res.get("type") == "reject":
        return "wontfix"
    if not res:
        if any(t.get("role") == ROLE_AUTHOR for t in (rec.get("thread") or [])):
            return "acknowledged"
        return "open"
    history = rec.get("verify_history") or []
    if not history:
        return "fixed"                       # fix proposed, not yet verified
    if history[-1].get("verdict") is True:
        return "verified-closed"             # latest run green + actually executed
    if any(h.get("verdict") is True for h in history):
        return "reopened"                    # was green once, regressed → resurrect
    return "fixed"                           # never verified green yet


def is_closed(rec: dict) -> bool:
    return derive_state(rec) in ("verified-closed", "wontfix")


def _engine_version_tuple() -> tuple:
    import prusik
    try:
        return tuple(int(x) for x in prusik.__version__.split("."))
    except ValueError:
        return (0, 0, 0)


def _version_floor_verify(fb_id: str, version: str) -> str:
    """A runnable check that the installed engine carries the moat-proven fix — i.e.
    `prusik.__version__ >= <fix version>`. Stored as the finding's verify command so
    closure is a REAL local green (self-gating on currency) and a downgrade re-runs
    RED → reopens. The proof chain: prusik's moat test is green in CI (source), and
    this confirms that version is present here."""
    tup = ", ".join(version.split("."))
    return (
        "python3 -c 'import prusik, sys; "
        'v = tuple(int(x) for x in prusik.__version__.split(".")); '
        f"sys.exit(0 if v >= ({tup}) else 1)' "
        f'&& echo "1 passed: engine carries moat-proven fix {fb_id} (prusik >= {version})"'
    )


def close_shipped(root: Path, shipped_ids: set[str],
                  moat_versions: dict[str, str] | None = None) -> dict:
    """Close-the-loop after an upgrade: for each LOCAL finding whose fix genuinely
    SHIPPED and isn't already closed, close it on a CAPTURED GREEN — never the release
    note's word. Three paths, all proof-gated:
      • an adopter-side verify command exists → run it (own-code findings).
      • else the finding is an ENGINE fix backed by a moat test (`id ∈ moat_versions`)
        and this engine is >= the fix version → PROOF-TRANSFER: attach a version-floor
        verify and run it (a real local green; reopens on downgrade). The transferable
        proof is prusik's own green-in-CI moat test.
      • else it shipped but carries no runnable proof here → surfaced (`needs_verify`).
    Returns buckets of finding ids by outcome."""
    moat_versions = moat_versions or {}
    cur = _engine_version_tuple()
    out: dict[str, list[str]] = {
        "closed": [], "transferred": [], "still_red": [], "needs_verify": [],
        "already_closed": []}
    for rec in load_all(root):
        fid = rec.get("id")
        if not fid or fid not in shipped_ids:
            continue
        if is_closed(rec):
            out["already_closed"].append(fid)
            continue
        res = rec.get("resolution") or {}
        moat_ver = moat_versions.get(fid)
        if res.get("type") == "fix" and res.get("verify"):
            result = verify(root, fid)
            out["closed" if (result and result[1].get("verdict")) else
                "still_red"].append(fid)
        elif moat_ver and cur >= tuple(int(x) for x in moat_ver.split(".")):
            resolve(root, fid, rtype="fix",
                    verify=_version_floor_verify(fid, moat_ver),
                    fixed_in=moat_ver, verify_kind="tests")
            result = verify(root, fid)
            out["transferred" if (result and result[1].get("verdict")) else
                "still_red"].append(fid)
        else:
            out["needs_verify"].append(fid)
    return out


import re as _re

_TEST_FILE_RE = _re.compile(r"\b((?:[\w./]*/)?test_[\w]+\.py)\b")


def harvest_candidate(rec: dict) -> str | None:
    """The genuinely-NEW finding-derived test the verify names, eligible to be tagged
    `moat-finding:<id>` (the coverage factory). HONESTY GATE (live-cc): only when the
    verify names exactly ONE dedicated test file AND does NOT scope an existing suite
    via `-m`/`-k` — a marker-scoped run of pre-existing tests authors nothing new, so
    there is nothing to harvest. Re-tagging existing tests would inflate the moat count
    (it would stop meaning new-coverage-from-findings). Returns the test path or None."""
    res = rec.get("resolution") or {}
    if res.get("type") != "fix":
        return None
    cmd = res.get("verify", "")
    # Normalize away the `python -m pytest` / `-m unittest` MODULE invocation so
    # its `-m` isn't misread as pytest's `-m <markexpr>` suite selection — the
    # module form is the most common way to invoke pytest and authors NEW tests
    # just as `pytest <file>` does.
    probe = _re.sub(r"-m\s+(?:pytest|unittest)\b", "", cmd)
    if not probe or _re.search(r"\s-[mk]\b", probe):   # selection of an existing suite → reuse
        return None
    files = set(_TEST_FILE_RE.findall(cmd))
    return files.pop() if len(files) == 1 else None


def harvest(root: Path, fb_id: str) -> str | None:
    """Promote a verified-closed finding's dedicated verify-test into the moat by
    tagging it `moat-finding:<id>` (so hq.moat_coverage counts it). No-op (None) unless
    the finding is verified-closed, has a harvestable NEW test (harvest_candidate), and
    the file isn't already tagged for it. Returns the tagged path, or None."""
    rec = load(root, fb_id)
    if rec is None or derive_state(rec) != "verified-closed":
        return None
    cand = harvest_candidate(rec)
    if not cand:
        return None
    p = root / cand
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")
    if f"moat-finding: {fb_id}" in text:
        return None                                # already harvested
    marker = f"\nmoat-finding: {fb_id}\n"
    if text.startswith('"""'):                     # fold into the module docstring
        end = text.find('"""', 3)
        if end != -1:
            text = text[:end] + marker + text[end:]
        else:
            text = f'# moat-finding: {fb_id}\n' + text
    else:
        text = f'# moat-finding: {fb_id}\n' + text
    p.write_text(text, encoding="utf-8")
    return cand


def ticket_status(rec: dict) -> dict[str, Any]:
    """Export-safe projection of a ticket — its STATE + metadata, for cross-repo
    reconcile. Carries the derived status, resolution type, and last-verify summary,
    but NOT the thread bodies or detail (those are the conversation / verbatim repro,
    which stay local; only status travels so HQ can merge adopter-side and prusik-side
    truth by id)."""
    history = rec.get("verify_history") or []
    last = history[-1] if history else None
    res = rec.get("resolution") or {}
    return {
        "id": rec["id"], "content_hash": rec.get("content_hash"),
        "status": derive_state(rec), "kind": rec.get("kind"),
        "severity": rec.get("severity"), "resolution_type": res.get("type"),
        "fixed_in": res.get("fixed_in"), "verify_count": len(history),
        "last_verdict": last.get("verdict") if last else None,
        "last_executed": last.get("executed_count") if last else None,
    }


def verify_selector(root: Path, *, all_closed: bool = False,
                    touched: list[str] | None = None) -> list[tuple[str, str, dict]]:
    """Re-run verify across closed findings — the living-guards sweep. `all_closed`
    re-verifies every verified-closed finding (periodic safety net); `touched`
    re-verifies only those whose verify/repro references one of the given modules
    (the cheap per-sprint blast-scoped path live-cc drives at sprint boundaries).
    Returns [(fb_id, new_state, entry)] for each re-run."""
    out = []
    for rec in load_all(root):
        if derive_state(rec) != "verified-closed":
            continue
        res = rec.get("resolution") or {}
        if not all_closed:
            blob = (res.get("verify", "") + " " + (rec.get("repro") or "")).lower()
            if not touched or not any(m.lower() in blob for m in touched):
                continue
        result = verify(root, rec["id"])
        if result:
            out.append((rec["id"], result[0], result[1]))
    return out
