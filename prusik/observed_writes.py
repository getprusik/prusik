"""Observed writes: what a Bash call actually changed in the shared tree (v0.247.0,
fb-aa7a6121fcee).

The pre-tool gate reads a command's TEXT to decide what it would write. Some writes no text
reading names: a python heredoc, `python -c "open(...)"`, a script file. This module reads
the tree STATE instead. The hooks snapshot the main tree before and after each Bash call,
diff the two, and report every changed path the phase may not write, and every change made
while another session holds the single-writer lease (fb-16c859a406b9). `gate advance`
compares the tree with the snapshot taken when the phase began, so a write the per-call
check missed still blocks leaving the phase (fb-c1d2d8dcabac).

The snapshot spawns nothing: an `os.scandir` walk plus sha256, with a persisted hash cache
keyed by (mtime_ns, size), so only files whose stat changed are re-hashed. Membership in
"the project" is decided only for the files that changed, with one `git check-ignore`.

Accepted misses (stated in CHANGELOG too):
  - A write that restores both mtime_ns and size exactly is not seen (`candidates`).
  - Under a foreign lease, writes to always-writable dirs (`.sprint`, `reports`, `briefs`,
    `findings`, `scripts/verify`, `.runtime`) are not reported. Every session writes them by
    design, and they are pruned from the walk (`snapshot`).
  - A background Bash call's writes made after the host returned are outside that call's
    window. In a sprint the advance check still sees them.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


Entry = list  # [mtime_ns: int, size: int, sha256: str | None]

HOOK_SNAPSHOT_DEADLINE_S = 5.0
# A pending pre-call snapshot older than this has lost its post check. Claude Code's Bash
# tool accepts a timeout of at most 600000 ms (tool schema, verified 2026-11-16 on 2.1.325),
# and a call past its timeout is moved to the background and its PostToolUse fires then, so
# no paired check legitimately waits longer than 600 s; 900 s leaves margin.
ORPHAN_AFTER_S = 900
HASH_MAX_BYTES = 64 * 1024 * 1024

_GLOB_CHARS = frozenset("{*?[")


class TreeUnreadable(Exception):
    """The tree state could not be read. `str(exc)` is the human reason."""


# ---- paths ---------------------------------------------------------------------------------

def _state_dir(root: Path) -> Path:
    from prusik import gate            # the one definition of the dir (gate reads it too)
    return Path(root).joinpath(*gate._OBSERVED_DIR)


def _pending_dir(root: Path) -> Path:
    return _state_dir(root) / "pending"


def _atomic_write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


# ---- snapshot ------------------------------------------------------------------------------

def _hash(path: str, size: int, is_link: bool) -> str | None:
    try:
        if is_link:
            return hashlib.sha256(os.readlink(path).encode("utf-8", "surrogateescape")).hexdigest()
        if size > HASH_MAX_BYTES:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _pruned_roots(config: dict, feature: str | None) -> set[str]:
    """Root-relative dirs D for every relative always-writable pattern `D/**`."""
    from prusik import phases
    out: set[str] = set()
    for pat in phases.always_writable_patterns(config, feature):
        if not pat.endswith("/**") or pat.startswith(("/", "~", "$")):
            continue
        d = pat[:-3].strip("/")
        if d and not (_GLOB_CHARS & set(d)):
            out.add(d)
    return out


def snapshot(root: Path, cache: dict[str, Entry] | None, *, deadline_s: float | None,
             config: dict | None = None, feature: str | None = None) -> dict[str, Entry]:
    """{rel: [mtime_ns, size, sha256 | None]} for every regular file and symlink in the
    shared tree. Symlinks are taken with lstat and hashed by their target string. Prunes, at
    any depth, `gate._MAIN_TREE_PRUNE | gate._DERIVED_DIRS` names and any directory holding
    a `.git` entry other than the root. Prunes, root-relative, every always-writable `D/**`
    dir. Lease-case miss: writes to those dirs under a foreign lease are not seen (they are
    every session's bookkeeping by design). A sha is reused from `cache` when
    (mtime_ns, size) match. Past `deadline_s` the hashes computed so far are saved to the
    cache and TreeUnreadable is raised."""
    from prusik import gate, phases
    root = Path(root)
    if config is None:
        config = phases.load_sprint_config(root) or {}
    if feature is None:
        feature = (phases.current_sprint_state(root) or {}).get("feature")
    names = gate._MAIN_TREE_PRUNE | gate._DERIVED_DIRS
    pruned = _pruned_roots(config, feature)
    cache = cache or {}
    out: dict[str, Entry] = {}
    started = time.monotonic()
    n = 0
    stack: list[tuple[str, str]] = [(str(root), "")]
    while stack:
        abs_dir, rel_dir = stack.pop()
        try:
            with os.scandir(abs_dir) as it:
                entries = list(it)
        except FileNotFoundError:
            if not rel_dir:
                raise TreeUnreadable(f"cannot scan the project root {root}") from None
            continue                                  # removed while the walk ran
        except OSError as e:
            where = rel_dir or str(root)
            raise TreeUnreadable(f"cannot scan {where}: {e.strerror or e}") from None
        if rel_dir and any(ent.name == ".git" for ent in entries):
            continue                                  # nested repo or submodule
        for ent in entries:
            rel = f"{rel_dir}/{ent.name}" if rel_dir else ent.name
            try:
                if ent.is_dir(follow_symlinks=False):
                    if ent.name not in names and rel not in pruned:
                        stack.append((ent.path, rel))
                    continue
                is_link = ent.is_symlink()
                if not (is_link or ent.is_file(follow_symlinks=False)):
                    continue                          # fifo, socket, device
                st = ent.stat(follow_symlinks=False)
            except OSError:
                out[rel] = [0, 0, None]               # unreadable: recorded, never skipped
                continue
            key = [st.st_mtime_ns, st.st_size]
            prior = cache.get(rel)
            if prior and prior[0] == key[0] and prior[1] == key[1] and prior[2] is not None:
                out[rel] = [key[0], key[1], prior[2]]
            else:
                out[rel] = [key[0], key[1], _hash(ent.path, st.st_size, is_link)]
            n += 1
            if deadline_s is not None and time.monotonic() - started > deadline_s:
                try:
                    save_cache(root, {**cache, **out})
                except OSError:
                    pass
                raise TreeUnreadable(f"tree snapshot exceeded {deadline_s} s after {n} files")
    return out


# ---- diff and membership -------------------------------------------------------------------

def candidates(before: dict[str, Entry], after: dict[str, Entry]) -> list[str]:
    """Sorted paths that appeared, disappeared, or whose (mtime_ns, size) differs.
    Accepted miss: a write that restores both mtime_ns and size exactly is not seen."""
    out = []
    for p in before.keys() | after.keys():
        b, a = before.get(p), after.get(p)
        if b is None or a is None or b[0] != a[0] or b[1] != a[1]:
            out.append(p)
    return sorted(out)


def _content_changed(before: dict[str, Entry], after: dict[str, Entry]) -> list[str]:
    out = []
    for p in candidates(before, after):
        b, a = before.get(p), after.get(p)
        if b is None or a is None or b[2] is None or a[2] is None or b[2] != a[2]:
            out.append(p)
    return out


def changed(before: dict[str, Entry], after: dict[str, Entry], root: Path, config: dict,
            feature: str | None) -> list[str]:
    """Sorted candidates whose content changed (appeared, disappeared, differing or None
    sha) and that are project members. Raises TreeUnreadable."""
    moved = _content_changed(before, after)
    if not moved:
        return []
    keep = members(root, moved, config, feature)
    return [p for p in moved if p in keep]


def matches_baseline(paths: list[str], now: dict[str, Entry],
                     baseline: dict[str, Entry]) -> set[str]:
    """The paths whose content now equals the phase-entry baseline: the same non-None sha,
    or absent from both. Such a path is not damage: a revert, an untouched dirty-at-entry
    file, or a sanctioned assembly folded into the baseline."""
    out = set()
    for p in paths:
        n, b = now.get(p), baseline.get(p)
        if n is None and b is None:
            out.add(p)
        elif n is not None and b is not None and n[2] is not None and n[2] == b[2]:
            out.add(p)
    return out


def gated_dirs(config: dict) -> frozenset[str]:
    """The literal first segment of every phase `writable` entry and `exit_artifacts[].path`,
    minus glob/brace segments, `worktrees` and `.sprint`."""
    out = set()
    for spec in (config or {}).get("phases", []) or []:
        if not isinstance(spec, dict):
            continue
        pats = list(spec.get("writable", []) or [])
        pats += [a.get("path") for a in (spec.get("exit_artifacts", []) or [])
                 if isinstance(a, dict)]
        for pat in pats:
            if not isinstance(pat, str):
                continue
            seg = pat.split("/", 1)[0]
            if not seg or seg.startswith("~") or _GLOB_CHARS & set(seg):
                continue
            if seg in ("worktrees", ".sprint", ".", ".."):
                continue
            out.add(seg)
    return frozenset(out)


def _in_git(root: Path) -> bool:
    p = Path(root).absolute()
    for d in (p, *p.parents):
        if os.path.lexists(os.path.join(d, ".git")):
            return True
    return False


def members(root: Path, paths: list[str], config: dict, feature: str | None) -> set[str]:
    """The paths that are project content: under a gated dir; else every path in a non-git
    project; else tracked or untracked-not-ignored, by one `git check-ignore`. A failed
    check raises TreeUnreadable, never a silent pass."""
    gated = gated_dirs(config)
    out = {p for p in paths if p.split("/", 1)[0] in gated}
    rest = [p for p in paths if p not in out]
    if not rest:
        return out
    if not _in_git(root):
        return out | set(rest)
    import subprocess                  # only here: the snapshot path never spawns
    try:
        proc = subprocess.run(["git", "check-ignore", "-z", "--stdin"], cwd=str(root),
                              input="\0".join(rest) + "\0", capture_output=True, text=True,
                              timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise TreeUnreadable(f"git check-ignore failed: {e}") from None
    if proc.returncode not in (0, 1):
        err = (proc.stderr or "").strip().splitlines()
        raise TreeUnreadable(f"git check-ignore failed: rc {proc.returncode}"
                             + (f": {err[0][:160]}" if err else ""))
    ignored = {p for p in proc.stdout.split("\0") if p}
    return out | {p for p in rest if p not in ignored}


# ---- hash cache ----------------------------------------------------------------------------

def _cache_path(root: Path) -> Path:
    return _state_dir(root) / "hashes.json"


def load_cache(root: Path) -> dict[str, Entry]:
    try:
        data = json.loads(_cache_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(root: Path, snap: dict[str, Entry]) -> None:
    _atomic_write(_cache_path(root), snap)


# ---- pending pre-call snapshots ------------------------------------------------------------

def pending_key(session: str | None, call_id: str) -> str:
    raw = f"{session or 'nosession'}-{call_id}"
    return "".join(c if (c.isascii() and (c.isalnum() or c in "_-")) else "_" for c in raw)


def write_pending(root: Path, key: str, record: dict) -> None:
    _atomic_write(_pending_dir(root) / f"{key}.json", record)


def _read_unlink(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        raw = None
    try:
        path.unlink()
    except FileNotFoundError:
        return None                    # another process paired or reported it first
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {"error": "pending snapshot unreadable"}
    return data


def pop_pending(root: Path, key: str) -> dict | None:
    """Read and unlink this call's pending record. None when there is none; a corrupt one
    is `{"error": "pending snapshot unreadable"}`."""
    return _read_unlink(_pending_dir(root) / f"{key}.json")


def _age_s(record_ts: object, path: Path, now: float) -> float:
    if isinstance(record_ts, str):
        from datetime import datetime
        try:
            return now - datetime.fromisoformat(record_ts).timestamp()
        except ValueError:
            pass
    try:
        return now - path.stat().st_mtime
    except OSError:
        return 0.0


def pop_orphans(root: Path, older_than_s: float) -> list[tuple[str, dict]]:
    """Every pending record older than `older_than_s` (by its `ts`, or file mtime when
    unreadable), read and unlinked, as (key, record-or-error). Never deletes a younger one.
    The caller reports every one returned: there is no silent sweep."""
    d = _pending_dir(root)
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json"))
    except OSError:
        return []
    now = time.time()
    out = []
    for name in names:
        path = d / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        ts = data.get("ts") if isinstance(data, dict) else None
        if _age_s(ts, path, now) <= older_than_s:
            continue
        rec = _read_unlink(path)
        if rec is not None:
            out.append((name[:-5], rec))
    return out


# ---- phase-entry baseline ------------------------------------------------------------------

def _baseline_path(root: Path) -> Path:
    return _state_dir(root) / "baseline.json"


def write_baseline(root: Path, feature: str, phase: str, entered_at: str,
                   snap: dict[str, Entry]) -> None:
    """Unlink any existing baseline FIRST, so a failed write leaves none (which refuses the
    next advance) rather than the previous entry's (which would look valid)."""
    clear_baseline(root)
    _atomic_write(_baseline_path(root), {"feature": feature, "phase": phase, "entered_at": entered_at,
                         "snapshot": snap})


def clear_baseline(root: Path) -> None:
    """Remove the phase-entry baseline (missing is fine)."""
    _baseline_path(root).unlink(missing_ok=True)


def _read_baseline(root: Path) -> tuple[dict | None, str | None]:
    try:
        raw = _baseline_path(root).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, "unreadable"
    if not isinstance(data, dict) or not isinstance(data.get("snapshot"), dict):
        return None, "unreadable"
    return data, None


def load_baseline(root: Path, feature: str, phase: str,
                  entered_at: str | None) -> tuple[dict | None, str | None]:
    """(snapshot, None), or (None, "missing" | "unreadable" | "mismatch"). With
    `entered_at=None` only (feature, phase) is checked (the hot-path form)."""
    data, reason = _read_baseline(root)
    if data is None:
        return None, reason
    if data.get("feature") != feature or data.get("phase") != phase:
        return None, "mismatch"
    if entered_at is not None and data.get("entered_at") != entered_at:
        return None, "mismatch"
    return data["snapshot"], None


def fold_baseline(root: Path, paths: list[str]) -> None:
    """Set each path's baseline entry to its current state (removed when absent): the
    fix-round assembly's copies are sanctioned prusik writes. A no-op unless a baseline
    exists for the active sprint's (feature, phase). Raises on I/O failure."""
    from prusik import phases
    root = Path(root)
    state = phases.current_sprint_state(root) or {}
    data, _ = _read_baseline(root)
    if (data is None or not state or data.get("feature") != state.get("feature")
            or data.get("phase") != state.get("phase")):
        return
    snap = data["snapshot"]
    for rel in paths:
        p = root / rel
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            snap.pop(rel, None)
            continue
        is_link = os.path.islink(p)
        sha = _hash(str(p), st.st_size, is_link)
        if sha is None and not is_link and st.st_size <= HASH_MAX_BYTES:
            raise OSError(f"cannot read {rel} to fold it into the phase baseline")
        snap[rel] = [st.st_mtime_ns, st.st_size, sha]
    _atomic_write(_baseline_path(root), data)

