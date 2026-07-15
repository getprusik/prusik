"""fb-80d0a26be528 — the per-failure classification gate.

Every residual failing test at build/review-exit must carry a MACHINE-verified
category (a git-stash-proven pre-existing / env-gap baseline entry), not a prose
'N pre-existing' claim and not a bare count. The gate blocks on any UNTAGGED red
(new-regression). These drive `_evidence_unsatisfied` end to end: a capture that
NAMES its failing tests is adjudicated per-failure, and the count bound is
authoritative only when names are absent (a terse run).

The adversarial case is the count-laundering one: proofs for tests A,B must NOT
tolerate a NEW failure in test C even though the count matches.

moat-finding: fb-80d0a26be528
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import date
from unittest import mock

from prusik import baseline, gate
from tests._common import _mktmp_project


def _args(**kw):
    base = dict(feature="feat", phase="reviewing", kind="tests", reset=False,
                baseline_domain=None, baseline_source=None,
                baseline_known_failures=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _setup(tmp):
    (tmp / ".sprint").mkdir(exist_ok=True)
    (tmp / ".sprint" / "state.json").write_text(
        '{"feature":"feat","phase":"reviewing"}')


def _capture_two_failures(tmp):
    """A tests capture that NAMES two failing tests and exits nonzero."""
    cmd = ('echo "FAILED tests/x.py::test_a - E"; '
           'echo "FAILED tests/x.py::test_b - E"; '
           'echo "2 failed in 0.1s"; exit 1')
    with mock.patch.object(gate, "_worktree_substantive_hash", return_value="H"):
        gate.capture(_args(command=[cmd]))
        return gate._evidence_unsatisfied(
            "reports/feat/reviewing.evidence.json", "feat", tmp)


def _tag(tmp, test_id, kind="pre-existing"):
    baseline.add_entry(tmp, test_id, proven_sha="deadbeef", note="proven",
                       days=30, today=date.today(), kind=kind)


def test_named_failures_recorded_at_capture():
    tmp = _mktmp_project()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        _setup(tmp)
        _capture_two_failures(tmp)
        import json
        ev = json.loads(
            (tmp / "reports" / "feat" / "reviewing.evidence.json").read_text())
        entry = ev["entries"][-1]
        assert entry["failing_tests"] == ["tests/x.py::test_a", "tests/x.py::test_b"]
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp)


def test_untagged_reds_block_the_phase():
    tmp = _mktmp_project()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        _setup(tmp)
        reason = _capture_two_failures(tmp)          # no baseline entries at all
        assert reason and "NO machine-verified category" in reason
        assert "test_a" in reason and "test_b" in reason
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp)


def test_all_reds_tagged_advances():
    tmp = _mktmp_project()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        _setup(tmp)
        _tag(tmp, "tests/x.py::test_a")
        _tag(tmp, "tests/x.py::test_b", kind="env-gap")   # mixed categories are fine
        reason = _capture_two_failures(tmp)
        assert reason is None, reason                      # every red carries a category
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp)


def test_count_laundering_is_refused_by_identity():
    # The adversarial case: two proofs, two failures, count matches — but the
    # proofs are for DIFFERENT tests than the ones failing now. A count bound would
    # tolerate it; per-failure identity must not.
    tmp = _mktmp_project()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        _setup(tmp)
        _tag(tmp, "tests/other.py::test_p")
        _tag(tmp, "tests/other.py::test_q")
        reason = _capture_two_failures(tmp)
        assert reason and "NO machine-verified category" in reason
        assert "test_a" in reason and "test_b" in reason
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp)


def test_partial_coverage_blocks_only_the_untagged():
    tmp = _mktmp_project()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        _setup(tmp)
        _tag(tmp, "tests/x.py::test_a")               # only a is tagged
        reason = _capture_two_failures(tmp)
        assert reason and "NO machine-verified category" in reason
        assert "test_b" in reason
        assert "test_a" not in reason                 # the tagged one isn't named
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp)


def test_id_matcher_never_covers_a_sibling_node():
    # a proof for test_a must NOT cover a new sibling test_ab / test_a2 by prefix.
    assert gate._test_id_matches("x.py::test_a", "x.py::test_a")
    assert not gate._test_id_matches("x.py::test_a", "x.py::test_ab")
    assert not gate._test_id_matches("x.py::test_a", "y.py::test_a")
