"""Python dep extraction via ast — accurate, stdlib-only."""

from __future__ import annotations

import ast
from pathlib import Path


def build(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    deps: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                # Record the FULL dotted path (was `.split(".")[0]`, top-package
                # only — too coarse for module-granular blast-radius reach, the
                # graph-recall ceiling an adopter flagged). A coarse query still
                # matches via the prefix walk in discovery.blast_radius.
                deps.add(a.name)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            # absolute `from a.b import x` → "a.b". Relative imports (level>0)
            # need the file's package context to resolve to a real module — a
            # follow-on (mirrors the TS resolver, v0.54.1); skipped here, not
            # mis-recorded as a bare relative name.
            deps.add(node.module)
    return sorted(deps)
