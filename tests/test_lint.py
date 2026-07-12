"""Self-hygiene gate: prusik lint- and type-checks itself (v0.42.0+).

A discipline harness must hold its own code to a standard. Configs live in
pyproject.toml: [tool.ruff] (correctness/dead-code rules enforced, stylistic
latitude allowed) and [tool.mypy] (gradual typing — None-safety / wrong-type
bugs caught in annotated code). These tests make "ruff-clean" and
"mypy-clean" fail-closed contracts: a maintainer who introduces an unused
import, dead variable, or type error fails CI here, not in review.

Mirrors the CHANGELOG-version meta-test: the repo enforces its own interface
invariants in the same suite that gates feature work.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
_BIN = Path(sys.executable).parent


def test_repo_is_ruff_clean():
    ruff = _BIN / "ruff"
    if not ruff.exists():
        pytest.skip("ruff not installed (dev dependency); run `uv sync`")
    proc = subprocess.run(
        [str(ruff), "check", "prusik", "tests"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "ruff found lint violations in prusik/ or tests/. Fix them (many are "
        "auto-fixable with `ruff check --fix`) before shipping.\n\n"
        + proc.stdout + proc.stderr
    )


def test_repo_is_mypy_clean():
    mypy = _BIN / "mypy"
    if not mypy.exists():
        pytest.skip("mypy not installed (dev dependency); run `uv sync`")
    proc = subprocess.run(
        [str(mypy), "prusik"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "mypy found type errors in prusik/. Fix them before shipping "
        "(annotation gaps and None-safety are the common causes).\n\n"
        + proc.stdout + proc.stderr
    )
