"""Python dep extraction via ast — accurate, stdlib-only.

Emits plain dotted module names, plus two MARKED spec shapes that only
`discovery.build_graph` interprets (it has the root context this root-free plugin lacks) and
never lets reach the persisted graph (fb-27ad79ae220b):
  • a relative base as a dot-prefixed spec — `.`, `..pkg` (resolved against the file's package)
  • a per-imported-name candidate `?<base>|<name>` — kept as `base.name` only when that is a
    real project module/package (so `from pkg import module` finally records `pkg.module`,
    while attributes and externals like `typing.Any` add nothing)
"""

from __future__ import annotations

import ast
from pathlib import Path

CANDIDATE = "?"          # `?<base>|<name>` — build_graph keeps base.name iff it's a project module
CANDIDATE_SEP = "|"


def build(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    deps: set[str] = set()
    for node in ast.walk(tree):                      # every scope: function-local, try, TYPE_CHECKING
        if isinstance(node, ast.Import):
            for a in node.names:
                # Record the FULL dotted path (was `.split(".")[0]`, top-package
                # only — too coarse for module-granular blast-radius reach, the
                # graph-recall ceiling an adopter flagged). A coarse query still
                # matches via the prefix walk in discovery.blast_radius.
                deps.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            # absolute `from a.b import x` → base "a.b"; relative `from ..p import x` → "..p"
            base = "." * node.level + (node.module or "")
            deps.add(base)
            for a in node.names:
                if a.name != "*":                    # the imported NAME, never the `as` alias
                    deps.add(f"{CANDIDATE}{base}{CANDIDATE_SEP}{a.name}")
    return sorted(deps)
