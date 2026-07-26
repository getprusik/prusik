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
import subprocess
import sys
import tempfile
from pathlib import Path

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

# --- Environment-integrity gate ------------------------------------------
# Two env-drift classes bit the suite on 2026-07-25:
#   (1) a declared dev dep (types-PyYAML) missing from the interpreter — the
#       declaration in pyproject and the runtime env had silently diverged;
#   (2) toolchain deps (pygments, packaging) resolvable ONLY via the USER
#       site-packages, which the hermetic HOME redirect above strips — nested
#       `python -m pytest` subprocesses crashed at import, surfacing as
#       misleading downstream assertion failures, not as the real cause.
# Both are properties of the environment, not of any one test, so they are
# gated here at session start: fail closed with the exact remediation.


def _dev_group_violations(specs=None):
    """Declared-vs-installed check for the pyproject `dev` dependency group —
    the declaration is the single source of truth; this interpreter must
    satisfy it. Returns human-readable violations (empty = clean)."""
    try:
        import tomllib
    except ModuleNotFoundError:          # py3.10 — dev group carries tomli
        import tomli as tomllib
    from importlib import metadata

    from packaging.requirements import Requirement

    if specs is None:
        root = Path(__file__).resolve().parent.parent
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        specs = data.get("dependency-groups", {}).get("dev", [])
    out = []
    for spec in specs:
        if not isinstance(spec, str):    # {include-group: ...} — not a requirement
            continue
        req = Requirement(spec)
        if req.marker is not None and not req.marker.evaluate():
            continue                     # declared for a different platform/python
        try:
            installed = metadata.version(req.name)
        except metadata.PackageNotFoundError:
            out.append(f"{req.name}: declared '{spec}' but NOT installed")
            continue
        if req.specifier and not req.specifier.contains(installed, prereleases=True):
            out.append(f"{req.name}: declared '{spec}' but installed {installed}")
    return out


def _hermetic_toolchain_failure(python=sys.executable):
    """Behavioral probe at the ACTUAL boundary: spawn `python -m pytest` the
    way the suite's nested-subprocess tests do — under the already-redirected
    HOME/TMPDIR, cwd outside the repo. A fresh interpreter re-derives the user
    site-packages path from $HOME, so a toolchain dep that lives only in the
    user site dies HERE with its real traceback instead of deep inside a test.
    Returns a failure message, or None if the toolchain is self-contained."""
    try:
        proc = subprocess.run([python, "-m", "pytest", "--version"],
                              capture_output=True, text=True, cwd=_HERMETIC,
                              timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"toolchain probe failed to run: {e}"
    if proc.returncode == 0:
        return None
    tail = "\n".join(((proc.stdout or "") + (proc.stderr or "")).strip()
                     .splitlines()[-15:])
    return (f"`{python} -m pytest --version` exits "
            f"{proc.returncode} under the hermetic HOME (user site-packages "
            f"stripped). A pytest dependency likely resolves only from the "
            f"user site — reinstall it into the interpreter's own "
            f"site-packages.\n{tail}")


def _env_integrity_gate():
    problems = _dev_group_violations()
    toolchain = _hermetic_toolchain_failure()
    if toolchain:
        problems.append(toolchain)
    if problems:
        detail = "\n  - ".join(problems)
        raise pytest.UsageError(
            f"environment drift — the suite refuses to run on an interpreter "
            f"that diverges from pyproject.toml:\n  - {detail}\n"
            f"Remediation: {sys.executable} -m pip install --group dev "
            f"(or `uv sync`), then re-run.")


_env_integrity_gate()

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
