"""fb-48e9135bf2bc — `gate capture --baseline-*` must persist into the evidence
entry, and a lint/types baseline must fail with an ACCURATE reason (not the
generic 'false-clean' that sent the reviewer chasing a persistence bug).

The tests-baseline path (the finding's stated core) works — these lock it so it
can't regress. Full lint/type baseline SUPPORT is a separate, scoped feature (the
lint evidence records files-checked, not a violation count to bound against).

moat-finding: fb-48e9135bf2bc
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from unittest import mock

from prusik import baseline, gate
from tests._common import _mktmp_project


def _args(**kw):
    base = dict(feature="feat", phase="reviewing", kind="tests",
                command=['echo "1 failed, 3 passed in 0.1s"; exit 1'], reset=False,
                baseline_domain=None, baseline_source=None,
                baseline_known_failures=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _setup(tmp):
    (tmp / ".sprint").mkdir(exist_ok=True)
    (tmp / ".sprint" / "state.json").write_text(
        '{"feature":"feat","phase":"reviewing"}')


def test_capture_persists_baseline_into_evidence():
    tmp = _mktmp_project()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        _setup(tmp)
        gate.capture(_args(baseline_known_failures=2, baseline_domain="integration",
                           baseline_source="reviewing-fix-round-2"))
        ev = json.loads((tmp / "reports" / "feat" / "reviewing.evidence.json").read_text())
        entry = ev["entries"][-1]
        assert entry["baseline"] == {
            "domain": "integration", "source": "reviewing-fix-round-2",
            "known_failures_count": 2}, entry.get("baseline")
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp)


def test_baseline_covers_failure_accepts_tests_but_not_lint():
    e = {"nonempty_primitive": {"kind": "tests"},
         "baseline": {"domain": "d", "source": "s", "known_failures_count": 2},
         "observed_failures": 2}
    with mock.patch.object(baseline, "active", return_value=[1, 2, 3]), \
            mock.patch.object(baseline, "load", return_value=[]):
        assert gate._baseline_covers_failure(e, Path(".")) is not None
        lint = {**e, "nonempty_primitive": {"kind": "lint"}}
        assert gate._baseline_covers_failure(lint, Path(".")) is None


def test_lint_baseline_gives_accurate_reason_not_false_clean():
    tmp = _mktmp_project()
    cwd = os.getcwd()
    try:
        os.chdir(tmp)
        _setup(tmp)
        with mock.patch.object(gate, "_worktree_substantive_hash", return_value="H"):
            gate.capture(_args(kind="lint", baseline_domain="lint-scope",
                               baseline_source="s", baseline_known_failures=1,
                               command=['echo "E501 line too long"; exit 1']))
            reason = gate._evidence_unsatisfied(
                "reports/feat/reviewing.evidence.json", "feat", tmp)
        assert reason and "only supported for kind=tests" in reason
        assert "false-clean" not in reason  # the misleading message is gone
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp)
