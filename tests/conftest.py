"""Test-suite global-state hygiene (v0.11.0 #5).

`_mktmp_project()` in test_smoke.py mutates process-global state — it calls
`os.chdir(tmp)` and sets `os.environ["CLAUDE_PROJECT_DIR"]`. ~176 of ~246
teardowns only `shutil.rmtree(tmp)` and never restore cwd/env, leaving the
process cwd pointed at a deleted directory and CLAUDE_PROJECT_DIR stale.
The suite passes today only because most call sites pass explicit roots —
but it is a latent ordering-dependent flake (the audit's finding).

This autouse fixture snapshots cwd + the env vars the suite mutates before
each test and restores them after, so a leaked chdir/env from one test
cannot bleed into the next. Minimum-viable "stop the bleeding": it touches
zero test bodies and does not attempt the (large, separate) file split.
"""

import atexit
import os
import shutil
import tempfile

import pytest

# --- Hermetic environment (audit P1) -------------------------------------
# Redirect HOME + TMPDIR to a throwaway dir and drop any leaked
# CLAUDE_PROJECT_DIR, so the suite never:
#   (a) touches the real ~/.claude — `prusik bridge on` writes
#       ~/.claude/prusik/bridges/<slug>; previously tests polluted real $HOME;
#   (b) collides with the engine's /tmp + /private/tmp always-writable rule —
#       temp project dirs under /tmp made 8 writable-path tests false-fail
#       (and would on any CI whose TMPDIR is /tmp);
#   (c) inherits a real project root via CLAUDE_PROJECT_DIR.
# /var/tmp is chosen deliberately: it is OUTSIDE both /tmp rules and has no
# .claude/.sprint ancestor, so project_root() falls back to cwd as tests expect.
os.environ.pop("CLAUDE_PROJECT_DIR", None)
try:
    _HERMETIC = tempfile.mkdtemp(prefix="prusik-pytest-", dir="/var/tmp")
except OSError:  # /var/tmp unavailable — fall back to a repo-local dir
    _HERMETIC = tempfile.mkdtemp(
        prefix="prusik-pytest-",
        dir=str(__import__("pathlib").Path(__file__).resolve().parent.parent))
# realpath so HOME has no symlink component (/var/tmp → /private/var/tmp on
# macOS); otherwise Path(p).resolve() in is_path_writable wouldn't match the
# un-resolved expanduser'd always_writable patterns.
_HERMETIC = os.path.realpath(_HERMETIC)
os.environ["HOME"] = _HERMETIC
os.environ["TMPDIR"] = _HERMETIC
tempfile.tempdir = _HERMETIC  # override the cached default so mkdtemp() uses it
atexit.register(lambda: shutil.rmtree(_HERMETIC, ignore_errors=True))

# Env vars the kit/test harness mutates and that must not leak between tests.
_TRACKED_ENV = ("CLAUDE_PROJECT_DIR", "PRUSIK_BRIDGE_PATH")


@pytest.fixture(autouse=True)
def _restore_global_state(tmp_path_factory):
    try:
        cwd = os.getcwd()
    except OSError:
        # A prior test left cwd at a deleted dir — recover to a stable root.
        cwd = str(tmp_path_factory.getbasetemp())
        os.chdir(cwd)
    env_snapshot = {k: os.environ.get(k) for k in _TRACKED_ENV}
    try:
        yield
    finally:
        try:
            os.chdir(cwd)
        except OSError:
            os.chdir(str(tmp_path_factory.getbasetemp()))
        for k, v in env_snapshot.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
