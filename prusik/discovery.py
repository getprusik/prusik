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
from prusik.discovery_plugins.python import CANDIDATE, CANDIDATE_SEP
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
        # parts BELOW root only: an ancestor named like a skip-dir (a project under
        # ~/build/ or worktrees/<role>/) must not prune everything (fb-2a4222ba7797)
        if any(part in IGNORE_DIRS for part in p.relative_to(root).parts):
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


# The conventional Python source root of a src-layout project (`src/pkg/mod.py` imported as
# `pkg.mod`) — fb-63d1c3a15af1.
SRC_ROOT = "src"


def _candidate(d: str) -> tuple[str, str]:
    """Decode the plugin's `?<base>|<name>` candidate marker — its one reader."""
    base, name = d[len(CANDIDATE):].split(CANDIDATE_SEP, 1)
    return base, name


def _python_parts(rel: str, source_root: str | None) -> list[str]:
    """Module-name parts for a root-relative path (with or without `.py`): relative to the
    Python source root when the path lives under it (`src/pkg/mod.py` → pkg.mod), else the
    project root; a trailing `__init__` names its package. fb-63d1c3a15af1 / fb-35f64503cc75."""
    parts = (rel[:-3] if rel.endswith(".py") else rel).strip("/").split("/")
    if source_root and len(parts) > 1 and parts[0] == source_root:
        parts = parts[1:]
    elif source_root and parts == [source_root]:
        parts = []
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return [x for x in parts if x]


def _python_module_set(rels: list[str], source_root: str | None = None) -> set[str]:
    """Dotted names of every module/package in the project: each `.py` file (`__init__.py` →
    its package) plus every ancestor directory of one — regular AND PEP 420 namespace packages
    (this repo's own `tests/` has no `__init__.py`, yet `from tests.x import …` is everywhere).
    Named relative to the source root when there is one."""
    mods: set[str] = set()
    for rel in rels:
        if not rel.endswith(".py"):
            continue
        parts = _python_parts(rel, source_root)
        if parts:
            mods.add(".".join(parts))
        dirs = _python_parts(rel.rsplit("/", 1)[0] if "/" in rel else "", source_root)
        mods.update(".".join(dirs[:i]) for i in range(1, len(dirs) + 1))
    return mods


def _resolve_python_relative(spec: str, rel: str, source_root: str | None = None) -> str | None:
    """`..pkg` in `a/b/c.py` → `a.pkg`. The file's package is its directory (a module's parent,
    an `__init__.py`'s own), named relative to the source root. A file with no package, and a
    climb past the top-level package (a Python error) — both dropped, never recorded bare."""
    level = len(spec) - len(spec.lstrip("."))
    pkg = _python_parts(rel.rsplit("/", 1)[0] if "/" in rel else "", source_root)
    keep = len(pkg) - (level - 1)
    if keep < 1:
        return None
    mod = spec[level:]
    return ".".join(pkg[:keep] + ([mod] if mod else []))


def _imports_src_absolutely(raw: list[str]) -> bool:
    """A raw Python dep (or a candidate's base) that IS `src` or under it — the project imports
    `src` as a package from the repo root, so `src/` is not a source root."""
    for d in raw:
        base = _candidate(d)[0] if d.startswith(CANDIDATE) else d
        if base == SRC_ROOT or base.startswith(SRC_ROOT + "."):
            return True
    return False


def _python_source_root(root: Path, python_raw: dict[str, list[str]]) -> str | None:
    """`src` when a top-level `src/` with no `__init__.py` holds Python files and nothing
    imports `src` absolutely — decided once per graph (recorded in its stats)."""
    src = root / SRC_ROOT
    if not src.is_dir() or (src / "__init__.py").exists():
        return None
    if not any(rel.startswith(SRC_ROOT + "/") for rel in python_raw):
        return None
    if any(_imports_src_absolutely(raw) for raw in python_raw.values()):
        return None
    return SRC_ROOT


def _resolve_python_deps(deps: list[str], rel: str, modules: set[str],
                         source_root: str | None = None) -> list[str]:
    """Turn the plugin's marked specs into clean dotted module names (fb-27ad79ae220b)."""
    out: set[str] = set()
    for d in deps:
        if d.startswith(CANDIDATE):
            base, name = _candidate(d)
            resolved = (_resolve_python_relative(base, rel, source_root)
                        if base.startswith(".") else base)
            if resolved and f"{resolved}.{name}" in modules:
                out.add(f"{resolved}.{name}")
        elif d.startswith("."):
            resolved = _resolve_python_relative(d, rel, source_root)
            if resolved:
                out.add(resolved)
        else:
            out.add(d)
    return sorted(out)


def build_graph(root: Path) -> dict:
    """The import graph, in memory (`dep_graph` persists it). One walk, materialized, feeds
    both the per-file extraction and the Python module set."""
    from prusik.ts_resolve import TsResolver, external_form
    ts = TsResolver(root)   # workspace + per-package tsconfig, loaded once
    forward: dict[str, list[str]] = {}
    reverse: dict[str, list[str]] = defaultdict(list)
    by_lang: dict[str, int] = defaultdict(int)
    sources = list(_iter_source_files(root))
    # Extract every file once, then decide the Python source root from the raw output (it
    # needs the whole project's imports), then resolve — no second walk, no second parse.
    extracted = [(str(src.relative_to(root)), build_for(src)) for src in sources]
    python_raw = {rel: r[1] for rel, r in extracted if r is not None and r[0] == "python"}
    source_root = _python_source_root(root, python_raw)
    py_modules = _python_module_set([rel for rel, _ in extracted], source_root)
    for rel, result in extracted:
        if result is None:
            continue
        lang, deps = result
        by_lang[lang] += 1
        if lang in ("typescript", "javascript"):
            # v0.54.1 (finding #6): resolve alias/relative imports to REAL files
            # so the TS module graph actually connects (workspace pkgs, tsconfig
            # paths, relative). Unresolvable/external → kept as an external node.
            deps = sorted({ts.resolve(rel, d) or external_form(d) for d in deps})
        elif lang == "python":
            deps = _resolve_python_deps(deps, rel, py_modules, source_root)
        forward[rel] = deps
        for d in deps:
            reverse[d].append(rel)
    return {
        "forward": forward,
        "reverse": {k: sorted(set(v)) for k, v in reverse.items()},
        "stats": {"total_files": len(sources), "by_language": dict(by_lang),
                  "python_source_root": source_root},
    }


def dep_graph(root: Path | None = None) -> int:
    root = root or project_root()
    graph = build_graph(root)
    total, by_lang = graph["stats"]["total_files"], graph["stats"]["by_language"]
    out = root / ".sprint" / "dep-graph.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph, indent=2))
    append("discovery_dep_graph", total_files=total, by_language=dict(by_lang))
    lang_summary = ", ".join(f"{k}={v}" for k, v in sorted(by_lang.items()))
    print(f"Dep graph written: {out.relative_to(root)} — {total} files ({lang_summary or 'no plugins matched'})")
    return 0


def _python_query_name(path: str, root: Path, source_root: str | None) -> str | None:
    """The module name a PATH query means, when the path is a Python module of the project
    (`<p>.py`, `<p>/__init__.py`, or a namespace dir `<p>/` holding `.py`; a trailing
    `__init__[.py]` means the package) — else None, so non-Python paths (a TS `src/config`)
    gain nothing. fb-63d1c3a15af1 / fb-35f64503cc75."""
    p = path.strip("/")
    p = p[:-3] if p.endswith(".py") else p
    pkg_dir = p[: -len("__init__")].rstrip("/") if p.split("/")[-1] == "__init__" else None
    target = root / (pkg_dir if pkg_dir is not None else p)
    is_module = (pkg_dir is not None and (target / "__init__.py").is_file()) or (
        pkg_dir is None and ((root / f"{p}.py").is_file() or (target / "__init__.py").is_file()
                             or (target.is_dir() and any(target.rglob("*.py")))))
    if not is_module:
        return None
    name = ".".join(_python_parts(pkg_dir if pkg_dir is not None else p, source_root))
    return name or None


def blast_radius(module_prefix: str, root: Path | None = None,
                 graph: dict | None = None) -> list[str]:
    """Given a module/directory prefix, return files that import from it. `graph` lets a
    caller query an in-memory graph (`build_graph`) without touching dep-graph.json."""
    root = root or project_root()
    if graph is None:
        graph_path = root / ".sprint" / "dep-graph.json"
        if not graph_path.exists():
            dep_graph(root)
        graph = json.loads(graph_path.read_text())
        # A cache built before the source-root decision existed is rebuilt, never misread as
        # "not src-layout"; a stored null IS a decision (fb-63d1c3a15af1).
        if "python_source_root" not in graph.get("stats", {}):
            dep_graph(root)
            graph = json.loads(graph_path.read_text())
    hits: set[str] = set()
    prefix_dot = module_prefix.replace("/", ".").rstrip(".")
    prefix_slash = module_prefix.rstrip("/")
    names = {prefix_dot}
    mapped = _python_query_name(module_prefix, root,
                                graph.get("stats", {}).get("python_source_root"))
    if mapped:
        names.add(mapped)                            # additive: today's query is kept
    for file, deps in graph.get("forward", {}).items():
        for d in deps:
            if any(d == n or d.startswith(n + ".") for n in names):
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
