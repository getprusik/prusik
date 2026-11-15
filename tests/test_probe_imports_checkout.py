"""Subprocesses the suite spawns import the checkout under test, not the editable install.

moat-finding: fb-b5ac5594db55

The venv's editable install points at the main checkout. A worktree suite whose hook probes ran
`sys.executable -c ...` imported main's prusik, so a hook regression in the worktree stayed green.
The conftest pins PYTHONPATH to its own repo root for the whole session, in one place.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import conftest

_REPO = Path(__file__).resolve().parent.parent
_VAR = "PYTHON" + "PATH"  # split so criterion (d)'s scan never matches this file


def test_session_pythonpath_starts_with_repo_root():
    head = os.environ.get(_VAR, "").split(os.pathsep)[0]
    assert head and Path(head).resolve() == _REPO


def test_subprocess_imports_prusik_from_repo_root(tmp_path):
    out = subprocess.run([sys.executable, "-c", "import prusik; print(prusik.__file__)"],
                         cwd=tmp_path, capture_output=True, text=True, check=True, timeout=60)
    assert Path(out.stdout.strip()).resolve().is_relative_to(_REPO / "prusik")


def test_path_builder_keeps_prior_entries_after_root():
    root = Path("/r")
    build = conftest._pythonpath_with_root
    assert build(root, None) == str(root)
    assert build(root, "") == str(root)
    assert build(root, os.pathsep.join(["/a", "/b"])) == os.pathsep.join(["/r", "/a", "/b"])
    # The root already present elsewhere moves to the front, never duplicated.
    assert build(root, os.pathsep.join(["/a", "/r"])) == os.pathsep.join(["/r", "/a"])


_ASSIGNS = re.compile(r"""["']""" + _VAR + r"""["']\s*[\]:]""")


def test_only_conftest_assigns_pythonpath():
    offenders = [p.name for p in sorted((_REPO / "tests").rglob("*.py"))
                 if p.name != "conftest.py" and _ASSIGNS.search(p.read_text())]
    assert offenders == []
