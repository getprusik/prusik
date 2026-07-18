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


def test_adopter_token_glued_into_snake_case_identifier_is_caught(tmp_path):
    """Regression: an adopter token embedded in a snake_case / camelCase / plural
    identifier (`_<NAME>_SHAPE`, `_<name>_like_project`, `<name>s`, `<name>Shape`) MUST
    be caught by the real `scan()`. The old `\\b(token)\\b` matcher missed every one of
    these — `_` is a word char so `\\b` never fires at a `_`/token seam, and a letter
    suffix defeats the trailing `\\b` — which is exactly how adopter names leaked into
    public test identifiers (`_SAAVI_SHAPE`, `_c2c_like_project`, `saavis_…`) and passed
    the gate. Exercises the actual scanner, not a re-built pattern."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("boundary_check", _SCRIPT)
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    token = "ac" + "me"                        # runtime-built; no literal token in source
    (tmp_path / "prusik").mkdir()
    (tmp_path / "prusik" / "x.py").write_text(
        f"_{token.upper()}_SHAPE = 1\n"        # snake_case, all-caps
        f"def _{token}_like_project(): pass\n"  # snake_case
        f"xs = '{token}s'; y = '{token}Shape'\n"  # plural (letter suffix) + camelCase
        f"h = 'ab{token}def0123'\n")           # alnum-run coincidence — NOT a leak
    snippets = " | ".join(h["snippet"] for h in bc.scan(tmp_path, [token]))
    assert f"_{token.upper()}_SHAPE" in snippets
    assert f"_{token}_like_project" in snippets
    assert f"{token}s" in snippets and f"{token}Shape" in snippets
    assert f"ab{token}def" not in snippets     # embedded in an alnum run → not flagged
