"""Cross-builder interface-contract validation (v0.68.0, an adopter enabler #7).

When parallel builders work in separate `worktrees/<role>/` dirs, they can drift
on SHARED contracts the plan's `## Interfaces` declares — two builders defining
the same symbol (An adopter: a duplicate `AlreadyActiveMemberError`), a mock arity that
disagrees, a CSS/selector seam. Today that surfaces only at the expensive
post-integration sentinel (20-45 min). This is a cheap cross-worktree diff at
`building → reviewing` that catches the high-confidence class in seconds.

It works because each `worktrees/<role>/` holds ONLY that builder's written
files (the partial mirror — see consistency.builder_writes_within_plan), not a
full repo copy. So a symbol DEFINED in two worktrees is genuine drift: both
builders wrote it, and integration will collide or silently shadow.

v1 — duplicate top-level symbol (class / function / def) across worktrees, with
plan-declared symbols (named in `## Interfaces`/`## Build order`) ranked first as
highest-confidence drift. A SIGNAL, not a gate: it surfaces before the sentinel;
the reviewer adjudicates. Honest boundary: mock-arity and CSS/selector seams are
the documented #7b follow-up (they need per-stack signature/selector extraction).
"""

from __future__ import annotations

import re
from pathlib import Path

# Top-level class / def / function (Python + JS/TS). `^` (re.M) excludes nested
# defs; covers `class X`, `def x`, `async def x`, `export class X`,
# `export default function x`.
# group 1 = leading indent (top-level iff empty), 2 = kind, 3 = name. Indented
# defs (methods) ARE captured — needed for #7c arity checks — but a non-special
# duplicate is only reported when at least one def is top-level (v1 precision).
_DEF_RE = re.compile(
    r"^([ \t]*)(?:export\s+(?:default\s+)?)?(?:async\s+)?(class|def|function)\s+(\w+)",
    re.M)
_SRC_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs")
_CSS_SUFFIXES = (".css", ".scss", ".sass", ".less")
_TEMPLATE_SUFFIXES = (".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte")
_CSS_DEF_RE = re.compile(r"\.([a-zA-Z_][\w-]+)")
_CLASS_ATTR_RE = re.compile(r"""class(?:Name)?\s*=\s*["']([^"']+)["']""")

# Generic framework/test names that legitimately recur across builders and aren't
# contract drift.
_COMMON = frozenset({
    "main", "Config", "Meta", "Base", "setUp", "tearDown", "setup", "teardown",
    "handler", "index", "app", "router", "run", "App", "Props", "State", "Page",
    "Layout", "Component", "Model", "Schema", "Form", "View", "Mixin", "Default",
})


def _worktree_dirs(root: Path) -> list[Path]:
    wt = root / "worktrees"
    if not wt.exists():
        return []
    return [d for d in sorted(wt.iterdir()) if d.is_dir()]


def _is_distinctive(sym: str) -> bool:
    """A symbol worth flagging on collision — not a dunder, test, or generic
    framework name."""
    return (not sym.startswith("_")
            and not sym.lower().startswith("test")
            and sym not in _COMMON
            and len(sym) >= 4)


def _extract_paren(text: str, start: int) -> str | None:
    """Content of the first balanced (...) at/after `start` — a def's param list."""
    i = text.find("(", start)
    if i == -1:
        return None
    depth = 0
    for j in range(i, min(len(text), i + 4000)):
        c = text[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[i + 1:j]
    return None


def _split_top_commas(s: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for c in s:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _arity_of(param_str: str) -> tuple[int | None, bool]:
    """(required positional count excluding self/cls, variadic). Variadic when a
    `*`-prefixed param (or bare kw-only `*` separator) is present — then arity is
    unbounded and we DON'T flag (conservative, low-FP)."""
    params = _split_top_commas(param_str)
    if any(p.startswith("*") for p in params):
        return None, True
    count = 0
    for idx, p in enumerate(params):
        name = p.split(":")[0].split("=")[0].strip()
        if idx == 0 and name in ("self", "cls"):
            continue
        if "=" in p or not name:        # has a default → not required
            continue
        count += 1
    return count, False


def _extract_defs(worktree: Path) -> dict[str, list[dict]]:
    """symbol → list of {file, arity, variadic} (arity=None for classes / kw-only
    / unparsed). Skips test files (helpers recur legitimately)."""
    defs: dict[str, list[dict]] = {}
    for f in worktree.rglob("*"):
        if not f.is_file() or f.suffix not in _SRC_SUFFIXES:
            continue
        rel = str(f.relative_to(worktree))
        if "test" in rel.lower():
            continue
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for m in _DEF_RE.finditer(text):
            kind, name = m.group(2), m.group(3)
            top_level = m.group(1) == ""
            arity: int | None = None
            variadic = False
            if kind in ("def", "function"):
                params = _extract_paren(text, m.end())
                if params is not None:
                    arity, variadic = _arity_of(params)
            defs.setdefault(name, []).append(
                {"file": rel, "arity": arity, "variadic": variadic,
                 "top_level": top_level})
    return defs


def _plan_named(root: Path, feature: str) -> set[str]:
    """Symbols the plan declares as shared contracts (reuses the #2 parser)."""
    plan = root / "design" / feature / "plan.md"
    if not plan.exists():
        return set()
    from prusik.blast_plan import _named_symbols
    return _named_symbols(plan.read_text())


def duplicate_symbols(root: Path, feature: str) -> list[dict]:
    """Distinctive top-level symbols defined in ≥2 worktrees. Plan-declared
    symbols rank first (the contract was explicit, so the collision is drift)."""
    worktrees = _worktree_dirs(root)
    if len(worktrees) < 2:
        return []
    named = _plan_named(root, feature)
    by_symbol: dict[str, dict[str, list[dict]]] = {}
    for wt in worktrees:
        for sym, deflist in _extract_defs(wt).items():
            by_symbol.setdefault(sym, {})[wt.name] = deflist
    findings: list[dict] = []
    for sym, locs in by_symbol.items():
        if len(locs) < 2 or not _is_distinctive(sym):
            continue
        # #7c — arity drift: per worktree, the first concrete (non-variadic)
        # arity of this symbol's def. >1 distinct value = a guaranteed TypeError
        # (the mock/stub-vs-real signature mismatch).
        arities = {wt: next((d["arity"] for d in dl
                             if d["arity"] is not None and not d["variadic"]), None)
                   for wt, dl in locs.items()}
        concrete = {a for a in arities.values() if a is not None}
        arity_mismatch = len(concrete) > 1
        # v1 precision: a plain (same-arity, non-declared) duplicate is only worth
        # flagging when it's a TOP-LEVEL symbol; indented method collisions are
        # reported only when they carry the high-confidence signals (arity drift /
        # plan-declared), to avoid noise from incidental same-name methods.
        top_level = any(d["top_level"] for dl in locs.values() for d in dl)
        if not (arity_mismatch or (sym in named) or top_level):
            continue
        findings.append({
            "symbol": sym,
            "plan_declared": sym in named,
            "arity_mismatch": arity_mismatch,
            "arities": {wt: a for wt, a in sorted(arities.items())},
            "worktrees": {name: [d["file"] for d in dl]
                          for name, dl in sorted(locs.items())},
        })
    findings.sort(key=lambda f: (not f["arity_mismatch"],
                                 not f["plan_declared"], f["symbol"]))
    return findings


def _norm_tokens(cls: str) -> tuple[str, ...]:
    """A class's concept = its token set, ignoring naming convention (BEM `__`/
    `--`, kebab `-`, snake `_`). `client__table` and `client-table` both →
    ('client', 'table'), so the same concept under two conventions collides."""
    return tuple(sorted(t for t in re.split(r"[-_]+", cls.lower()) if t))


def _css_defs(worktree: Path) -> dict[str, list[str]]:
    defs: dict[str, list[str]] = {}
    for f in worktree.rglob("*"):
        if not f.is_file() or f.suffix not in _CSS_SUFFIXES:
            continue
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(f.relative_to(worktree))
        for m in _CSS_DEF_RE.finditer(text):
            defs.setdefault(m.group(1), []).append(rel)
    return defs


def _css_used(worktree: Path) -> dict[str, list[str]]:
    used: dict[str, list[str]] = {}
    for f in worktree.rglob("*"):
        if not f.is_file() or f.suffix not in _TEMPLATE_SUFFIXES:
            continue
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(f.relative_to(worktree))
        for m in _CLASS_ATTR_RE.finditer(text):
            for cls in m.group(1).split():
                if "{" in cls or "}" in cls or not cls:   # skip interpolation
                    continue
                used.setdefault(cls, []).append(rel)
    return used


def css_drift(root: Path) -> list[dict]:
    """Cross-worktree CSS naming drift (#7b): a class USED in a template that no
    CSS defines, but whose concept (token set) matches a DEFINED class under a
    different convention — the BEM-vs-flat seam (`client-table` vs `client__table`).
    Token-match keeps FP low: a framework utility (`flex`) has no same-token
    defined variant, so it's never flagged."""
    worktrees = _worktree_dirs(root)
    if not worktrees:
        return []
    defined: dict[str, dict[str, list[str]]] = {}
    used: dict[str, dict[str, list[str]]] = {}
    for wt in worktrees:
        for c, files in _css_defs(wt).items():
            defined.setdefault(c, {})[wt.name] = files
        for c, files in _css_used(wt).items():
            used.setdefault(c, {})[wt.name] = files
    by_norm: dict[tuple[str, ...], set[str]] = {}
    for c in defined:
        by_norm.setdefault(_norm_tokens(c), set()).add(c)
    findings: list[dict] = []
    for u, locs in sorted(used.items()):
        if u in defined:                       # used matches a real definition
            continue
        norm = _norm_tokens(u)
        variants = sorted(c for c in by_norm.get(norm, set()) if c != u)
        if norm and variants:
            findings.append({
                "used_class": u,
                "defined_as": variants,
                "used_in": {wt: sorted(f) for wt, f in sorted(locs.items())},
                "defined_in": {v: sorted(defined[v].keys()) for v in variants},
            })
    return findings


def advisory(root: Path, feature: str) -> str | None:
    """Non-blocking advisory for the building→reviewing gate (None when clean)."""
    dups = duplicate_symbols(root, feature)
    css = css_drift(root)
    if not dups and not css:
        return None
    lines: list[str] = []
    if dups:
        n_arity = sum(1 for d in dups if d["arity_mismatch"])
        head = (f"[prusik-gate] cross-builder ADVISORY — {len(dups)} symbol(s) "
                f"defined in >1 worktree (drift the sentinel catches ~30 min later)")
        if n_arity:
            head += f"; {n_arity} with an ARITY MISMATCH — a guaranteed TypeError"
        lines.append(head + ":")
        for d in dups[:8]:
            where = " · ".join(f"{wt}: {', '.join(files)}"
                               for wt, files in d["worktrees"].items())
            tags = []
            if d["arity_mismatch"]:
                tags.append(f"ARITY MISMATCH {d['arities']}")
            if d["plan_declared"]:
                tags.append("plan-declared")
            tag = f" [{'; '.join(tags)}]" if tags else ""
            lines.append(f"    · {d['symbol']}{tag} → {where}")
        lines.append("  Reconcile owners (one builder defines the shared symbol; "
                     "the other imports it) — and align the signature.")
    if css:
        lines.append(f"[prusik-gate] cross-builder ADVISORY — {len(css)} CSS "
                     f"class(es) used under a name no CSS defines, but defined "
                     f"under another convention (BEM-vs-flat seam):")
        for c in css[:8]:
            lines.append(f"    · used `{c['used_class']}` "
                         f"(in {', '.join(c['used_in'])}) ↔ defined "
                         f"`{', '.join(c['defined_as'])}`")
        lines.append("  Agree on the class string before integration "
                     "(the style won't apply otherwise).")
    return "\n".join(lines)


def _format_report(dups: list[dict], css: list[dict], feature: str) -> str:
    lines = [f"cross-check — {feature}"]
    if not dups and not css:
        lines.append("  ✓ no symbol defined in >1 worktree; no CSS naming drift.")
        return "\n".join(lines)
    if dups:
        lines.append(f"  ⚠ {len(dups)} symbol(s) defined in >1 worktree:")
        for d in dups:
            tags = []
            if d["arity_mismatch"]:
                tags.append(f"ARITY MISMATCH {d['arities']}")
            if d["plan_declared"]:
                tags.append("plan-declared")
            tag = f"  [{'; '.join(tags)}]" if tags else ""
            lines.append(f"    {d['symbol']}{tag}")
            for wt, files in d["worktrees"].items():
                lines.append(f"        {wt}: {', '.join(files)}")
    if css:
        lines.append(f"  ⚠ {len(css)} CSS class(es) with cross-builder naming drift:")
        for c in css:
            lines.append(f"    used `{c['used_class']}` ↔ defined "
                         f"`{', '.join(c['defined_as'])}`")
            for wt, files in c["used_in"].items():
                lines.append(f"        used in {wt}: {', '.join(files)}")
    lines.append("  (signal, not a gate — reconcile before integration.)")
    return "\n".join(lines)


def run(feature: str, root: Path | None = None, json_output: bool = False) -> int:
    from prusik import ledger
    root = root or ledger.project_root()
    dups = duplicate_symbols(root, feature)
    css = css_drift(root)
    ledger.append("cross_builder_check", feature=feature,
                  duplicates=len(dups), css_drift=len(css),
                  arity_mismatches=sum(1 for d in dups if d["arity_mismatch"]),
                  plan_declared=sum(1 for d in dups if d["plan_declared"]))
    if json_output:
        import json
        print(json.dumps({"feature": feature, "duplicates": dups,
                          "css_drift": css}, indent=2))
    else:
        print(_format_report(dups, css, feature))
    return 0
