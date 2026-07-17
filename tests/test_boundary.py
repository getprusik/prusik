"""Regression guard for the open-core boundary (scripts/boundary_check.py).

Asserts the public engine surface carries ZERO adopter identity. Driven by the
private registry (hq/products.local.json); when that registry is absent (a fresh
clone / a public checkout with no hq/), the check cannot run and the test SKIPS
with a visible reason rather than passing silently — the fail-closed enforcement
lives in the pre-commit hook on the maintainer machine, which has the registry.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO / "scripts" / "boundary_check.py"
_REGISTRY = _REPO / "hq" / "products.local.json"


def test_public_surface_has_no_adopter_identity():
    if not _REGISTRY.is_file():
        pytest.skip(f"no private registry at {_REGISTRY} — boundary check needs it "
                    "to know which adopter names to scan for (enforced by pre-commit "
                    "on the maintainer machine)")
    proc = subprocess.run([sys.executable, str(_SCRIPT), "--json"],
                          cwd=str(_REPO), capture_output=True, text=True)
    # rc 0 clean · 1 leak · 2 registry missing/malformed
    assert proc.returncode == 0, (
        f"boundary-check failed (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")


def test_glued_and_alt_separator_adopter_forms_are_caught():
    """A compound registry label must be caught in ANY written form — glued,
    underscored, or spaced. Plain `\\blabel\\b` / `\\bfrag\\b` MISS the glued form;
    _flex closes that identity-leak hole. Tokens are built at runtime so this test
    carries no literal adopter identity (which the boundary check would itself flag)."""
    import importlib.util
    import re
    spec = importlib.util.spec_from_file_location("boundary_check", _SCRIPT)
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    frag, tail = "c2" + "c", "invoic" + "ing"   # never the literal token in source
    label = frag + "-" + tail
    pat = re.compile(r"\b(" + bc._flex(label) + r"|" + bc._flex(frag) + r")\b", re.I)
    assert pat.search(frag + tail)            # GLUED — the hole this closes
    assert pat.search(frag + "_" + tail)      # underscore
    assert pat.search(label)                  # hyphen
    assert pat.search(frag + " " + tail)      # spaced
    assert pat.search("a " + frag + " ref")   # bare fragment still matches
    assert not pat.search("fb-" + frag + "def123456")   # hex fragment ≠ leak
