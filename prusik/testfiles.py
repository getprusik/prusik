"""The one rule for "is this path a test file?", shared by every caller.

A path is a test path when it sits under a `tests/` or `test/` directory, or under a JS/TS
`__tests__/` or `e2e/` directory, or its name is `test_*.py`, `*_test.py` or `conftest.py`,
or a JS/TS `*.test.*` / `*.spec.*` / `*.e2e.*` file. Paths are project-relative with `/`
separators (a Windows-style `\\` is normalised first).

Given the tree the path lives in (`root`), a name-matched Python file outside any test
directory whose directory is a package (holds `__init__.py`) is a module, not a test:
`prusik/test_reach.py` is source that only looks like a test by name.
"""

from __future__ import annotations

import re
from pathlib import Path

_TEST_DIRS = frozenset(("tests", "test", "__tests__", "e2e"))
_TEST_NAME = re.compile(
    r"^(?:test_[\w-]+\.py"
    r"|[\w-]+_test\.py"
    r"|conftest\.py"
    r"|[\w.-]+\.(?:test|spec|e2e)\.[cm]?[jt]sx?)$")


def is_test_path(rel: str, root: Path | None = None) -> bool:
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return False
    if any(d in _TEST_DIRS for d in parts[:-1]):
        return True
    name = parts[-1]
    if not _TEST_NAME.match(name):
        return False
    if root is not None and name.endswith(".py") and name != "conftest.py" and parts[:-1]:
        return not (Path(root).joinpath(*parts[:-1]) / "__init__.py").is_file()
    return True
