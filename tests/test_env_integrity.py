"""The conftest env-integrity gate detects drift, not just passes when clean.

Adversarial coverage for the two 2026-07-25 drift classes: a declared dev dep
missing/mismatched in the interpreter, and a toolchain that cannot start under
the hermetic HOME. The gate already ran green at session start (or this file
would never have been collected); these tests prove it FAILS when it should.
"""

from conftest import _dev_group_violations, _hermetic_toolchain_failure


def test_dev_group_clean_env_yields_no_violations():
    # the real pyproject dev group against the running interpreter
    assert _dev_group_violations() == []


def test_dev_group_flags_a_missing_dist():
    out = _dev_group_violations(["no-such-dist-prusik-gate-xyz>=1.0"])
    assert len(out) == 1 and "NOT installed" in out[0]


def test_dev_group_flags_a_version_mismatch():
    # pytest is installed, but never at <0.1 — must flag, not pass
    out = _dev_group_violations(["pytest<0.1"])
    assert len(out) == 1 and "installed" in out[0]


def test_dev_group_skips_inapplicable_markers():
    # declared for a python that will never run this suite → not a violation
    assert _dev_group_violations(["pytest<0.1; python_version < '3'"]) == []


def test_dev_group_skips_include_group_entries():
    assert _dev_group_violations([{"include-group": "other"}]) == []


def test_toolchain_probe_green_on_this_interpreter():
    assert _hermetic_toolchain_failure() is None


def test_toolchain_probe_flags_a_broken_interpreter():
    # /usr/bin/false ignores args and exits 1 — the shape of a toolchain that
    # cannot start; the gate must report, never wave through
    msg = _hermetic_toolchain_failure(python="/usr/bin/false")
    assert msg is not None and "exits 1" in msg
