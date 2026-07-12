"""`prusik gate capture` already runs the command through `bash -c`. A user-added
`bash -c …` wrapper double-wraps it and the argv join drops the inner quoting, so
the wrong thing runs SILENTLY and the identical wrong result trips a convergence
stall. Guard it: fail closed with actionable guidance.

moat-finding: fb-9f107742fe4d
moat-finding: fb-32b3a89cc1d5
"""

from __future__ import annotations

import pytest

from prusik.gate import _shell_wrapper_misuse


@pytest.mark.parametrize("cmd", [
    ["bash", "-c", "pnpm", "contracts:check"],     # an adopter's exact (unquoted) form
    ["bash", "-c", "pnpm contracts:check"],         # quoted, still double-wrapped
    ["sh", "-c", "make test"],
    ["zsh", "-lc", "pnpm test"],
    ["/bin/bash", "-c", "x"],                        # absolute path → basename match
    ["dash", "-c", "true"],
])
def test_redundant_shell_wrapper_is_rejected(cmd):
    msg = _shell_wrapper_misuse(cmd)
    assert msg is not None
    assert "already runs your command through `bash -c`" in msg
    assert "pass it directly" in msg and "ONE quoted arg" in msg


@pytest.mark.parametrize("cmd", [
    ["pnpm", "contracts:check"],                     # the correct direct form
    ["pnpm", "--filter=@an adopter/backend", "test"],
    ["bash", "scripts/test.sh"],                      # `bash <file>` is NOT `bash -c`
    ["pytest", "-q"],
    ["pnpm", "a", "&&", "pnpm", "b"],                # operator survives the join → fine
    ["python", "-c", "print(1)"],                    # not a shell → run as-is
    ["node", "-e", "process.exit(0)"],
])
def test_legitimate_commands_pass(cmd):
    assert _shell_wrapper_misuse(cmd) is None


def test_capture_returns_2_on_wrapper(tmp_path, monkeypatch):
    """End-to-end: capture() fails closed (rc 2) and does NOT spawn the command."""
    import subprocess
    from types import SimpleNamespace

    from prusik import gate, ledger
    monkeypatch.setattr(ledger, "project_root", lambda: tmp_path)
    spawned = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: spawned.append(a) or SimpleNamespace())
    args = SimpleNamespace(command=["bash", "-c", "pnpm", "contracts:check"],
                           kind="lint", feature="f", phase="regression")
    rc = gate.capture(args)
    assert rc == 2
    assert spawned == []          # guarded BEFORE running the wrong thing


def _capture_cmd_str(cmd, tmp_path, monkeypatch):
    """Run the REAL gate.capture and return the exact string it fed to `bash -c`."""
    import subprocess
    from types import SimpleNamespace

    from prusik import gate, ledger
    monkeypatch.setattr(ledger, "project_root", lambda: tmp_path)
    seen = {}

    def fake_run(argv, **kw):
        seen["cmd_str"] = argv[2]            # ["/bin/bash", "-c", <cmd_str>]
        return SimpleNamespace(returncode=0, stdout="3 passed", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    gate.capture(SimpleNamespace(command=cmd, kind="tests",
                                 feature="f", phase="regression"))
    return seen["cmd_str"]


def test_marker_expression_quoting_is_preserved(tmp_path, monkeypatch):
    """fb-32b3a89cc1d5: `pytest -m "not browser_smoke"` must reach bash with the
    marker grouped, not collapsed to a bare `browser_smoke` path."""
    assert _capture_cmd_str(["pytest", "-m", "not browser_smoke"], tmp_path,
                            monkeypatch) == "pytest -m 'not browser_smoke'"


def test_single_arg_compound_stays_a_raw_shell_line(tmp_path, monkeypatch):
    assert _capture_cmd_str(["pnpm a && pnpm b"], tmp_path, monkeypatch) == \
        "pnpm a && pnpm b"


def test_env_prefix_multi_arg_unharmed(tmp_path, monkeypatch):
    assert _capture_cmd_str(["NODE_ENV=test", "pytest"], tmp_path, monkeypatch) == \
        "NODE_ENV=test pytest"
