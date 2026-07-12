"""JavaScript/TypeScript dep extraction — regex-based.

Covers: ES imports, CommonJS require, dynamic import(). Good-enough for scoping;
not a real parser. For full accuracy, swap in a tree-sitter-based plugin.
"""

from __future__ import annotations

import re
from pathlib import Path

_PATTERNS = [
    # import ... from 'x'  |  import 'x'
    re.compile(r"""import\s+(?:[^'"]+?\s+from\s+)?['"]([^'"]+)['"]"""),
    # require('x')
    re.compile(r"""require\(\s*['"]([^'"]+)['"]\s*\)"""),
    # import('x') — dynamic
    re.compile(r"""import\(\s*['"]([^'"]+)['"]\s*\)"""),
    # export ... from 'x'
    re.compile(r"""export\s+(?:\*|\{[^}]*\})\s+from\s+['"]([^'"]+)['"]"""),
]


def build(path: Path) -> list[str]:
    try:
        text = path.read_text()
    except (UnicodeDecodeError, OSError):
        return []
    deps: set[str] = set()
    # Strip line/block comments cheaply to reduce false positives.
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    for pat in _PATTERNS:
        for m in pat.findall(text):
            # Keep FULL specifiers for relative + alias imports so the dep-graph
            # can resolve them to real files (ts_resolve). Bare externals collapse
            # to their package root. (v0.54.1, finding #6: previously `@scope/pkg`
            # was stripped here, losing the subpath needed for resolution.)
            if m.startswith(".") or m.startswith("@"):
                deps.add(m)
            else:
                deps.add(m.split("/")[0])
    return sorted(deps)
