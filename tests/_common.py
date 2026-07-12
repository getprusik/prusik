"""Shared test infrastructure (private — leading-underscore name means
pytest doesn't try to collect it).

Originally everything lived in a single monolithic tests/test_smoke.py.
v0.23.0 split that file by domain (test_phases, test_evidence,
test_binding, etc.); cross-section helpers landed here so each split
module imports from one place.

Add helpers here when MULTIPLE test files would otherwise duplicate
them. If a helper is only used by one test file, keep it there.
"""

from __future__ import annotations

import argparse  # noqa: F401  (re-exported)
import contextlib
import io
import json
import os
import re  # noqa: F401  (re-exported)
import shutil
import subprocess  # noqa: F401  (re-exported)
import sys
import tempfile
import time  # noqa: F401  (re-exported)
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Re-export commonly imported prusik modules so split files can do
# `from ._common import gate, phases, kit_init, ...` without
# re-listing the full import block in every file.
from prusik import schema, phases, triage, discovery, gate, watchdog, issues  # noqa: E402,F401
from prusik import init as kit_init  # noqa: E402,F401
from prusik import uninstall as kit_uninstall  # noqa: E402,F401
from prusik import toggle as kit_toggle  # noqa: E402,F401
from prusik import consistency  # noqa: E402,F401
from prusik import agents_doctor  # noqa: E402,F401
from prusik import refresh as kit_refresh  # noqa: E402,F401
from prusik import pause as kit_pause  # noqa: E402,F401
from prusik import permissions as kit_permissions  # noqa: E402,F401
from prusik import brief_lint as kit_brief_lint  # noqa: E402,F401
from prusik import fix_round as kit_fix_round  # noqa: E402,F401
from prusik import bridge as kit_bridge  # noqa: E402,F401
from prusik import detect as kit_detect  # noqa: E402,F401
from prusik import doctor as kit_doctor  # noqa: E402,F401
from prusik.ledger import digest as ledger_digest  # noqa: E402,F401


def _mktmp_project():
    tmp = Path(tempfile.mkdtemp(prefix="kit-test-"))
    os.chdir(tmp)
    os.environ["CLAUDE_PROJECT_DIR"] = str(tmp)
    return tmp


def _copy_sprint_config(tmp):
    (tmp / ".claude").mkdir()
    shutil.copy(
        Path(__file__).parent.parent / "prusik" / "templates" / ".claude" / "sprint-config.yaml",
        tmp / ".claude" / "sprint-config.yaml",
    )


def _wt_file(tmp, role, rel, content):
    """Write a file under worktrees/<role>/<rel>. Used by tests that
    exercise worktree-relative paths (gate / reviewer / etc.)."""
    p = tmp / "worktrees" / role / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def _write_ledger(tmp, events):
    """Write a list of dict events as ledger.jsonl. Used by tests that
    seed ledger state without invoking the FSM."""
    sp = tmp / ".sprint"
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "ledger.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )


def _capture_stdout(fn):
    """Run fn() and return its stdout as a string. Used for tests that
    assert on CLI output text without spawning a subprocess."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue()


def _capture_stderr(fn):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        fn()
    return buf.getvalue()


# A reusable minimum-valid brief body. Used by phases/brief tests to
# create briefs that pass brief-lint without each test reinventing the
# minimum schema.
_VALID_BRIEF = """## Goal
Add email receipts on successful checkout for customers.

## Success criteria
Receipt arrives within 10s of payment with no errors.

## Type
new_feature
"""
