"""TypeScript/JavaScript module-graph edge resolution (v0.54.1, finding #6).

prusik's JS/TS dep extraction captured import SPECIFIERS but left them literal —
`@an adopter/shared`, `@/components` stayed as alias strings, so the dep-graph had all
the file nodes but hollow internal edges (no real module graph → map_freshness /
blast-radius untrustworthy on TS). This resolves a specifier to the REAL project
file via the three mechanisms a TS monorepo actually uses:

  1. workspace package imports (`@scope/<pkg>[/sub]`) → <pkg-dir>/src/[sub|index]
     (pnpm-workspace.yaml / package.json "workspaces" → each package's name)
  2. tsconfig `paths` (per-package, e.g. `@/*` → ./src/*), baseUrl-relative
  3. relative (`./`, `../`) with TS extension / index resolution

External bare imports (react, …) and not-found specifiers return None — the
caller keeps them as external/raw nodes (so an unresolved edge is never dropped).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

# Extension + index resolution order (TS before JS; .d.ts handled by suffix).
_EXTS = (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", ".json")


def _load_jsonc(path: Path) -> dict:
    """tsconfig.json is JSONC. Most are valid JSON, so try that FIRST — a naive
    comment strip would clobber glob strings like `"@/*"` / `"**/*.ts"` (the
    `/*`…`*/` looks like a block comment). Only on a genuine parse failure fall
    back to stripping `//` line comments + trailing commas (NOT block comments,
    for the same glob-safety reason)."""
    try:
        text = path.read_text()
    except OSError:
        return {}
    for candidate in (text, _strip_line_comments(text)):
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else {}
        except ValueError:
            continue
    return {}


def _strip_line_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        # drop a // comment only when it's not inside a string (cheap heuristic:
        # even number of quotes before it)
        idx = line.find("//")
        if idx != -1 and line[:idx].count('"') % 2 == 0:
            line = line[:idx]
        out.append(line)
    text = "\n".join(out)
    return re.sub(r",(\s*[}\]])", r"\1", text)        # trailing commas


class TsResolver:
    """Resolves TS/JS import specifiers to real project files. Construct once per
    dep-graph run (workspace packages + per-package tsconfig paths are cached)."""

    def __init__(self, root: Path):
        self.root = root
        self._packages = self._load_workspace_packages()   # {pkg_name: rel_dir}
        self._ts_paths: dict[str, list[tuple[str, list[str]]]] = {}

    # ---- public ----

    def resolve(self, importer_rel: str, spec: str) -> str | None:
        """Real project-relative file for `spec` imported from `importer_rel`,
        or None if external/unresolvable."""
        if spec.startswith("."):
            return self._relative(importer_rel, spec)
        hit = self._workspace(spec)
        if hit:
            return hit
        return self._tsconfig(importer_rel, spec)

    # ---- workspace packages ----

    def _load_workspace_packages(self) -> dict[str, str]:
        pkgs: dict[str, str] = {}
        for g in self._workspace_globs():
            for d in self.root.glob(g):
                pj = d / "package.json"
                if d.is_dir() and pj.is_file():
                    try:
                        name = json.loads(pj.read_text()).get("name")
                    except (ValueError, OSError):
                        name = None
                    if name:
                        pkgs[name] = str(d.relative_to(self.root))
        return pkgs

    def _workspace_globs(self) -> list[str]:
        ws = self.root / "pnpm-workspace.yaml"
        if ws.is_file():
            try:
                data = yaml.safe_load(ws.read_text()) or {}
                pkgs = data.get("packages")
                if isinstance(pkgs, list):
                    return [str(p) for p in pkgs]
            except yaml.YAMLError:
                pass
        pj = self.root / "package.json"
        if pj.is_file():
            try:
                w = json.loads(pj.read_text()).get("workspaces")
            except (ValueError, OSError):
                w = None
            if isinstance(w, dict):
                w = w.get("packages")
            if isinstance(w, list):
                return [str(p) for p in w]
        return []

    def _workspace(self, spec: str) -> str | None:
        for name, pkgdir in self._packages.items():
            if spec == name or spec.startswith(name + "/"):
                sub = spec[len(name):].lstrip("/")
                base = self.root / pkgdir / "src"
                return self._try(base / sub if sub else base)
        return None

    # ---- tsconfig paths (per importing package) ----

    def _owning_pkg_dir(self, importer_rel: str) -> str | None:
        best = None
        for pkgdir in self._packages.values():
            if importer_rel == pkgdir or importer_rel.startswith(pkgdir + "/"):
                if best is None or len(pkgdir) > len(best):
                    best = pkgdir
        return best

    def _paths_for(self, pkgdir: str) -> list[tuple[str, list[str]]]:
        if pkgdir in self._ts_paths:
            return self._ts_paths[pkgdir]
        co = _load_jsonc(self.root / pkgdir / "tsconfig.json").get("compilerOptions") or {}
        base_url = co.get("baseUrl", ".")
        out: list[tuple[str, list[str]]] = []
        for prefix, targets in (co.get("paths") or {}).items():
            if isinstance(targets, list):
                out.append((prefix, [str(Path(base_url) / t) for t in targets]))
        self._ts_paths[pkgdir] = out
        return out

    def _tsconfig(self, importer_rel: str, spec: str) -> str | None:
        pkgdir = self._owning_pkg_dir(importer_rel)
        if pkgdir is None:
            return None
        for prefix, targets in self._paths_for(pkgdir):
            if prefix.endswith("/*") and spec.startswith(prefix[:-1]):
                rest = spec[len(prefix) - 1:]
                for t in targets:
                    hit = self._try(self.root / pkgdir / t.replace("*", rest))
                    if hit:
                        return hit
            elif prefix == spec:
                for t in targets:
                    hit = self._try(self.root / pkgdir / t)
                    if hit:
                        return hit
        return None

    # ---- relative ----

    def _relative(self, importer_rel: str, spec: str) -> str | None:
        return self._try((self.root / importer_rel).parent / spec)

    # ---- file/extension/index resolution ----

    def _try(self, base: Path) -> str | None:
        s = str(base)
        candidates = [base] + [Path(s + e) for e in _EXTS] \
            + [base / f"index{e}" for e in _EXTS]
        for c in candidates:
            try:
                if c.is_file():
                    return str(c.resolve().relative_to(self.root.resolve()))
            except (OSError, ValueError):
                continue
        return None


def external_form(spec: str) -> str:
    """Fallback node for an unresolved specifier — relative kept raw; a scoped
    package collapsed to `@scope/pkg`; a bare package to its root."""
    if spec.startswith("."):
        return spec
    if spec.startswith("@"):
        parts = spec.split("/", 2)
        return "/".join(parts[:2]) if len(parts) >= 2 else spec
    return spec.split("/")[0]
