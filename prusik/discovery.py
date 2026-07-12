"""Deterministic discovery tools (zero tokens).

- inventory.json: directories, file counts, manifests
- dep-graph.json: multi-language import graph (forward + reverse + by_language)
- map-fingerprint.json: snapshot of the dep-graph at the moment design/map.md
  was last written; used to detect map staleness on later sprints.

Language support is plugin-based (prusik/discovery_plugins/). Python uses ast;
JS/TS/Go use regex. Unsupported languages are silently skipped.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from prusik.discovery_plugins import build_for, supported_suffixes
from prusik.ledger import project_root, append

IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".sprint",
               "dist", "build", ".pytest_cache", ".ruff_cache", ".mypy_cache",
               ".claude", "worktrees", "target", "vendor", ".next"}


def _iter_source_files(root: Path):
    suffixes = set(supported_suffixes())
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in suffixes:
            continue
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        yield p


def inventory(root: Path | None = None) -> int:
    root = root or project_root()
    dirs = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name in IGNORE_DIRS or child.name.startswith("."):
            continue
        files = list(child.rglob("*"))
        dirs.append({
            "path": child.name,
            "file_count": sum(1 for f in files if f.is_file()),
            "has_tests": any("test" in str(f).lower() for f in files if f.is_file()),
        })
    manifests = [m for m in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml",
                             "requirements.txt", "Pipfile", "poetry.lock", "uv.lock",
                             "tsconfig.json", "deno.json")
                 if (root / m).exists()]
    data = {"root": str(root), "directories": dirs, "manifests": manifests}
    out = root / ".sprint" / "inventory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2))
    append("discovery_inventory", dir_count=len(dirs), manifests=manifests)
    print(f"Inventory written: {out.relative_to(root)} — {len(dirs)} dirs, {len(manifests)} manifest(s)")
    return 0


def dep_graph(root: Path | None = None) -> int:
    root = root or project_root()
    from prusik.ts_resolve import TsResolver, external_form
    ts = TsResolver(root)   # workspace + per-package tsconfig, loaded once
    forward: dict[str, list[str]] = {}
    reverse: dict[str, list[str]] = defaultdict(list)
    by_lang: dict[str, int] = defaultdict(int)
    total = 0
    for src in _iter_source_files(root):
        total += 1
        result = build_for(src)
        if result is None:
            continue
        lang, deps = result
        by_lang[lang] += 1
        rel = str(src.relative_to(root))
        if lang in ("typescript", "javascript"):
            # v0.54.1 (finding #6): resolve alias/relative imports to REAL files
            # so the TS module graph actually connects (workspace pkgs, tsconfig
            # paths, relative). Unresolvable/external → kept as an external node.
            deps = sorted({ts.resolve(rel, d) or external_form(d) for d in deps})
        forward[rel] = deps
        for d in deps:
            reverse[d].append(rel)
    graph = {
        "forward": forward,
        "reverse": {k: sorted(set(v)) for k, v in reverse.items()},
        "stats": {"total_files": total, "by_language": dict(by_lang)},
    }
    out = root / ".sprint" / "dep-graph.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, indent=2))
    append("discovery_dep_graph", total_files=total, by_language=dict(by_lang))
    lang_summary = ", ".join(f"{k}={v}" for k, v in sorted(by_lang.items()))
    print(f"Dep graph written: {out.relative_to(root)} — {total} files ({lang_summary or 'no plugins matched'})")
    return 0


def blast_radius(module_prefix: str, root: Path | None = None) -> list[str]:
    """Given a module/directory prefix, return files that import from it."""
    root = root or project_root()
    graph_path = root / ".sprint" / "dep-graph.json"
    if not graph_path.exists():
        dep_graph(root)
    graph = json.loads(graph_path.read_text())
    hits: set[str] = set()
    prefix_dot = module_prefix.replace("/", ".").rstrip(".")
    prefix_slash = module_prefix.rstrip("/")
    for file, deps in graph.get("forward", {}).items():
        for d in deps:
            if d == prefix_dot or d.startswith(prefix_dot + "."):
                hits.add(file)
            elif d.startswith("./" + prefix_slash) or d.startswith("../" + prefix_slash):
                hits.add(file)
    return sorted(hits)


def fingerprint_map(root: Path | None = None) -> int:
    """Snapshot the current dep-graph as the baseline for map.md staleness checks.

    Intended to be called immediately after the cartographer role writes
    design/map.md. Later `prusik gate map-freshness` compares the then-current
    dep-graph to this snapshot to compute drift.
    """
    root = root or project_root()
    map_path = root / "design" / "map.md"
    if not map_path.exists():
        print("[discovery] no design/map.md — run the cartographer role first")
        return 1
    # Auto-refresh the dep-graph FIRST, then snapshot the module set from the SAME fresh
    # file walk the map-freshness gate compares against (`current_modules`). Previously the
    # fingerprint read the CACHED dep-graph.json, which could be stale: a file merged in a
    # prior sprint wasn't in the graph yet, so a fingerprint run right after the cartographer
    # wrote map.md snapshotted a module set MISSING that file — and the gate (which walks the
    # tree fresh) then read the just-written fingerprint as already-drifted. Re-running the
    # cartographer couldn't clear it (it re-read the same stale cache); only a manual
    # `discovery all` rebuilt the graph (fb-dde6878ad04b). Refreshing here keeps every
    # downstream consumer (blast_radius, the freshness gate) current, and snapshotting from
    # `current_modules` makes a freshly-run fingerprint drift-free BY CONSTRUCTION.
    rc = dep_graph(root)
    if rc != 0:
        return rc
    graph = json.loads((root / ".sprint" / "dep-graph.json").read_text())
    modules = sorted(current_modules(root))
    fp = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "map_sha256": hashlib.sha256(map_path.read_bytes()).hexdigest(),
        "module_count": len(modules),
        "modules": modules,
        "by_language": graph.get("stats", {}).get("by_language", {}),
    }
    out = root / ".sprint" / "map-fingerprint.json"
    out.write_text(json.dumps(fp, indent=2))
    append("discovery_map_fingerprinted", module_count=len(modules))
    print(f"Map fingerprint written: {out.relative_to(root)} — {len(modules)} modules")
    return 0


def map_drift(root: Path | None = None) -> dict | None:
    """Return drift stats between a FRESH module walk and the map-fingerprint.

    {
      "drift_pct": float,           # 0.0 = unchanged, 100.0 = totally different
      "added": [...], "removed": [...], "common": int,
      "fingerprint_ts": ISO8601,
    }
    Returns None if either file is missing.
    """
    root = root or project_root()
    fp_path = root / ".sprint" / "map-fingerprint.json"
    if not fp_path.exists():
        return None
    fp = json.loads(fp_path.read_text())
    prev = set(fp.get("modules", []))
    # Compare against a FRESH file walk — the identical basis the fingerprint snapshots and
    # `feature_scoped_drift` uses — not the cached dep-graph.json (which could be stale, and
    # whose forward-keys are a SUBSET of source files, so a no-import file would read as
    # permanent drift). Keeps all three drift bases consistent (fb-dde6878ad04b).
    current = current_modules(root)
    union = prev | current
    if not union:
        return {"drift_pct": 0.0, "added": [], "removed": [], "common": 0,
                "fingerprint_ts": fp.get("ts")}
    added = sorted(current - prev)
    removed = sorted(prev - current)
    symdiff = len(added) + len(removed)
    return {
        "drift_pct": round(100 * symdiff / len(union), 1),
        "added": added[:20],
        "removed": removed[:20],
        "added_count": len(added),
        "removed_count": len(removed),
        "common": len(prev & current),
        "fingerprint_ts": fp.get("ts"),
    }


def current_modules(root: Path | None = None) -> set[str]:
    """The CURRENT source-module set (root-relative paths), computed by a fresh file walk
    — the same keys `dep_graph` would produce for its `forward` map, WITHOUT the import
    resolution or the dep-graph.json write. Cheap, side-effect-free, and reflects HEAD,
    so a file merged after the last `dep_graph` run is visible (fb-76ff51b273de)."""
    root = root or project_root()
    return {str(src.relative_to(root)) for src in _iter_source_files(root)}


def feature_scoped_drift(root: Path | None = None,
                         terms: list[str] | None = None) -> list[str] | None:
    """Drifted modules (added OR removed between the map fingerprint and a FRESH module
    walk) whose path matches any of `terms` (case-insensitive substring). Feature-scoped
    staleness: even when GLOBAL drift % is below the gate threshold, a change INSIDE the
    feature's own subsystem means scoping would read a stale map of exactly what it
    scopes (fb-76ff51b273de — a dependency merged into the feature's subsystem
    0-2d before a sprint whose 4d-old map passed the age/global-% checks). Compares
    against `current_modules` (fresh, side-effect-free), NOT the cached dep-graph.json,
    so a just-merged file is seen. Returns matching paths, [] if none match, or None when
    the fingerprint is missing (gate can't judge)."""
    root = root or project_root()
    fp_path = root / ".sprint" / "map-fingerprint.json"
    if not fp_path.exists():
        return None
    low_terms = [t.lower() for t in (terms or []) if t]
    if not low_terms:
        return []
    fp = json.loads(fp_path.read_text())
    prev = set(fp.get("modules", []))
    current = current_modules(root)
    drifted = (current - prev) | (prev - current)
    return sorted(m for m in drifted
                  if any(t in m.lower() for t in low_terms))


def dispatch(cmd: str) -> int:
    if cmd == "inventory":
        return inventory()
    if cmd == "dep-graph":
        return dep_graph()
    if cmd == "fingerprint-map":
        return fingerprint_map()
    if cmd == "all":
        inventory()
        dep_graph()
        return 0
    return 1
