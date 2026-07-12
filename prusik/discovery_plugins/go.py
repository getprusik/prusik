"""Go dep extraction — regex-based over import blocks."""

from __future__ import annotations

import re
from pathlib import Path

_SINGLE = re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""", re.MULTILINE)
_BLOCK = re.compile(r"""import\s*\(\s*([^)]+)\s*\)""", re.DOTALL)
_INSIDE = re.compile(r"""['"]([^'"]+)['"]""")


def build(path: Path) -> list[str]:
    try:
        text = path.read_text()
    except (UnicodeDecodeError, OSError):
        return []
    deps: set[str] = set()
    for m in _SINGLE.findall(text):
        deps.add(m)
    for block in _BLOCK.findall(text):
        for m in _INSIDE.findall(block):
            deps.add(m)
    return sorted(deps)
