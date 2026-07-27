"""The scaffolded gate hooks invoke bare `prusik`, resolved against the PATH of
the shell that launches Claude Code — NOT the PATH of the init run. A venv-only
install whose venv isn't on that PATH made EVERY hook exit 127 (command not
found) with no warning anywhere: the harness read as fully wired while erroring
on every tool call. init and doctor now surface the drift via
preflight.hook_resolution_warning().

fb-41f8936e3a85.

moat-finding: fb-41f8936e3a85
"""

from __future__ import annotations

import sys

from prusik.preflight import hook_resolution_warning


def test_unresolvable_prusik_is_a_loud_warning():
    # the exact adopter shape: `../venv/bin/prusik init` without activation —
    # nothing named `prusik` on PATH → hooks will die 127 in a CC session
    msg = hook_resolution_warning(which=None)
    assert msg.startswith("⚠") and "127" in msg and "PATH" in msg


def test_venv_local_resolution_is_an_informational_note():
    # activated venv: `prusik` resolves, but only inside the venv — hooks work
    # only when claude is launched from that activated environment
    msg = hook_resolution_warning(which="/home/u/venv/bin/prusik",
                                  prefix="/home/u/venv",
                                  base_prefix="/usr")
    assert msg.startswith("·") and "virtualenv" in msg


def test_global_resolution_is_silent():
    # pipx / uv tool / system install: resolves outside any venv → no note
    assert hook_resolution_warning(which="/usr/local/bin/prusik",
                                   prefix="/usr", base_prefix="/usr") == ""


def test_venv_run_with_global_binary_is_silent():
    # running from a venv, but a PATH-global prusik exists outside it —
    # hooks resolve fine regardless of activation
    assert hook_resolution_warning(which="/usr/local/bin/prusik",
                                   prefix="/home/u/venv",
                                   base_prefix="/usr") == ""


def test_live_defaults_never_crash():
    # whatever this box's real env is, the check must return a str, not raise
    assert isinstance(hook_resolution_warning(), str)
    assert sys.base_prefix  # sanity: the fields the default path reads exist
