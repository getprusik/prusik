"""Dep-graph plugin registry.

Each plugin exposes a function `build(path: Path) -> list[str]` returning the
single-hop dependencies for one source file. Plugins are keyed by file suffix
so the top-level `dep_graph()` dispatches without regex gymnastics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from prusik.discovery_plugins import python as _python
from prusik.discovery_plugins import javascript as _javascript
from prusik.discovery_plugins import go as _go

# Suffix → (language_name, build_fn)
PLUGINS: dict[str, tuple[str, Callable[[Path], list[str]]]] = {
    ".py":   ("python", _python.build),
    ".js":   ("javascript", _javascript.build),
    ".jsx":  ("javascript", _javascript.build),
    ".ts":   ("typescript", _javascript.build),
    ".tsx":  ("typescript", _javascript.build),
    ".mjs":  ("javascript", _javascript.build),
    ".cjs":  ("javascript", _javascript.build),
    ".go":   ("go", _go.build),
}


def build_for(path: Path) -> tuple[str, list[str]] | None:
    """Return (language, deps) for a file, or None if no plugin covers it."""
    plugin = PLUGINS.get(path.suffix)
    if plugin is None:
        return None
    lang, fn = plugin
    try:
        return lang, fn(path)
    except Exception:
        return lang, []


def supported_suffixes() -> list[str]:
    return list(PLUGINS.keys())
