"""Full-suite hard gate v2 (v0.82.0, field finding #14): a learned test-count baseline
distinguishes a real full-suite proof from a subset; opt-in block at building exit."""

from __future__ import annotations

import os
import shutil

import yaml

from tests._common import _mktmp_project, _write_ledger  # noqa: F401,E402
from prusik import gate, suite_baseline


def test_baseline_max_and_looks_full():
    d = _mktmp_project()
    (d / '.sprint').mkdir(exist_ok=True)
    try:
        assert suite_baseline.load(d) == 0
        assert suite_baseline.update(d, 5) == 5
        assert suite_baseline.update(d, 3) == 5          # max — subset can't lower
        assert suite_baseline.update(d, 906) == 906
        assert suite_baseline.looks_full(906, 906)
        assert suite_baseline.looks_full(820, 906)       # within 0.9 tolerance
        assert not suite_baseline.looks_full(5, 906)     # subset
        assert suite_baseline.looks_full(5, 0)           # no baseline → seeds
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _setup(proj, *, require, prove_executed, baseline=906):
    os.environ["CLAUDE_PROJECT_DIR"] = str(proj)
    (proj / '.sprint').mkdir(exist_ok=True)
    suite_baseline.update(proj, baseline)
    (proj / ".claude").mkdir(exist_ok=True)
    (proj / ".claude" / "sprint-config.yaml").write_text(
        yaml.safe_dump({"require_full_suite_at_build": require}))
    evts = [{"ts": "2026-06-05T10:00:00+00:00", "event": "phase_advance",
             "to_phase": "building", "feature": "feat"}]
    if prove_executed is not None:
        evts.append({"ts": "2026-06-05T11:00:00+00:00", "event": "prove_run",
                     "kind": "tests", "proven": True, "executed": prove_executed})
    _write_ledger(proj, evts)


def test_block_on_subset_when_required():
    proj = _mktmp_project()
    try:
        _setup(proj, require=True, prove_executed=5)     # 5 of ~906 = subset
        assert gate._full_suite_gate("building", "reviewing", "feat") == 2
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_block_when_no_proof_and_required():
    proj = _mktmp_project()
    try:
        _setup(proj, require=True, prove_executed=None)  # no prove at all
        assert gate._full_suite_gate("building", "reviewing", "feat") == 2
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_pass_on_full_suite_proof():
    proj = _mktmp_project()
    try:
        _setup(proj, require=True, prove_executed=900)   # ~full
        assert gate._full_suite_gate("building", "reviewing", "feat") is None
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_advisory_only_when_not_required():
    proj = _mktmp_project()
    try:
        _setup(proj, require=False, prove_executed=5)    # subset, but opt-in OFF
        assert gate._full_suite_gate("building", "reviewing", "feat") is None  # advise, don't block
    finally:
        shutil.rmtree(proj, ignore_errors=True)


# ── full-suite evidence at the INTEGRATION gate (fb-90cfcfa8b918) ──
# moat-finding: fb-90cfcfa8b918

def _setup_reviewing(proj, *, require, prove_executed, baseline=906):
    os.environ["CLAUDE_PROJECT_DIR"] = str(proj)
    (proj / '.sprint').mkdir(exist_ok=True)
    suite_baseline.update(proj, baseline)
    (proj / ".claude").mkdir(exist_ok=True)
    (proj / ".claude" / "sprint-config.yaml").write_text(
        yaml.safe_dump({"require_full_suite_at_build": require}))
    evts = [{"ts": "2026-06-05T12:00:00+00:00", "event": "phase_advance",
             "to_phase": "reviewing", "feature": "feat"}]
    if prove_executed is not None:
        evts.append({"ts": "2026-06-05T12:30:00+00:00", "event": "prove_run",
                     "kind": "tests", "proven": True, "executed": prove_executed})
    _write_ledger(proj, evts)


def test_integration_blocks_on_touched_set_when_required():
    """The exact scenario: 133 touched tests proven green, full suite ~906 → subset →
    integration must NOT proceed on touched-set evidence."""
    proj = _mktmp_project()
    try:
        _setup_reviewing(proj, require=True, prove_executed=133)
        assert gate._full_suite_gate("reviewing", "integrating", "feat") == 2
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_integration_passes_on_full_suite():
    proj = _mktmp_project()
    try:
        _setup_reviewing(proj, require=True, prove_executed=906)
        assert gate._full_suite_gate("reviewing", "integrating", "feat") is None
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_integration_advisory_only_when_not_required():
    proj = _mktmp_project()
    try:
        _setup_reviewing(proj, require=False, prove_executed=133)   # subset → advisory, not block
        assert gate._full_suite_gate("reviewing", "integrating", "feat") is None
    finally:
        shutil.rmtree(proj, ignore_errors=True)
