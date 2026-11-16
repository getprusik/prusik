"""fb-f381d16021db — every prusik that runs in a project is the version the project is on, or
it refuses.

Which prusik ran used to be an accident: hooks and agents took the first `prusik` on PATH, and
drive's children could import a `prusik/` package from the project root. One drive mixed
0.245.0 and 0.247.0 and nothing noticed. Every entrypoint now checks the running version
against the project's pin (the manifest's surface version, or the source version in the prusik
repo) and exits 2 on a mismatch; a launched child also refuses unless it imported its parent's
package.

Every case runs the real entrypoint (`python -m prusik`) in a tmp project, and asserts what
must keep working next to what must refuse: an equal pin runs, the opt-out runs, the exempt
commands repair, a missing manifest stays unchecked.

moat-finding: fb-f381d16021db
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

import prusik
from prusik import __main__ as cli
from prusik import pin

_REPO = Path(__file__).resolve().parent.parent
_TEMPLATE_CFG = _REPO / "prusik" / "templates" / ".claude" / "sprint-config.yaml"
_RUNNING = prusik.__version__
_MAJ, _MIN, _PAT = pin.parse_version(_RUNNING)
_AHEAD_PIN = f"{_MAJ}.{_MIN - 1}.0"        # the engine is AHEAD of this pin
_BEHIND_PIN = f"{_MAJ}.{_MIN + 1}.0"       # the engine is BEHIND this pin
_READ = {"session_id": "t", "hook_event_name": "PreToolUse", "tool_name": "Read",
         "tool_input": {"file_path": "README.md"}}
_BRIEF = """## Goal
Add email receipts on successful checkout for customers.

## Success criteria
Receipt arrives within 10s of payment with no errors.

## Type
new_feature
"""


def _run(root: Path, *args: str, payload: dict | None = None, cwd: Path | None = None,
         env: dict | None = None) -> subprocess.CompletedProcess:
    full = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), **(env or {})}
    return subprocess.run([sys.executable, "-m", "prusik", *args],
                          input=json.dumps(payload) if payload is not None else "",
                          capture_output=True, text=True, cwd=str(cwd or root), env=full,
                          check=False, timeout=120)


def _project(tmp_path: Path, pin_version: str | None, *, opt_out: bool = False) -> Path:
    root = tmp_path / "proj"
    (root / ".claude").mkdir(parents=True)
    cfg = _TEMPLATE_CFG.read_text()
    if opt_out:
        cfg += "\nauto_refresh_on_skew: false\n"
    (root / ".claude" / "sprint-config.yaml").write_text(cfg)
    (root / ".sprint").mkdir()
    if pin_version is not None:
        _set_pin(root, pin_version)
    return root


def _manifest(root: Path) -> Path:
    return root / ".claude" / ".prusik-manifest.json"


def _set_pin(root: Path, version: str) -> None:
    mp = _manifest(root)
    m = json.loads(mp.read_text()) if mp.exists() else {"files": []}
    m["template_surface_version"] = version
    mp.write_text(json.dumps(m))


def _assert_refused(p: subprocess.CompletedProcess, *names: str) -> None:
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert "[prusik] refusing:" in p.stderr, p.stderr
    for n in (_RUNNING, os.path.abspath(prusik.__file__), *names):
        assert n in p.stderr, (n, p.stderr)


def _assert_ran(p: subprocess.CompletedProcess, rc: int = 0) -> None:
    assert "refusing" not in p.stderr, p.stderr
    assert p.returncode == rc, (p.returncode, p.stdout, p.stderr)


def _initialised(tmp_path: Path) -> Path:
    """A real `prusik init` project (a manifest with tracked files, so refresh can sync)."""
    root = tmp_path / "proj"
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _assert_ran(_run(root, "init"))
    assert pin.resolve(root)[0] == _RUNNING
    return root


# ── equal runs; behind and ahead refuse ─────────────────────────────────────────────

def test_equal_pin_runs_every_hook_and_command(tmp_path):
    root = _project(tmp_path, _RUNNING)
    _assert_ran(_run(root, "gate", "pre-tool", payload=_READ))
    _assert_ran(_run(root, "gate", "post-tool", payload=_READ))
    _assert_ran(_run(root, "gate", "stop", payload={"session_id": "t"}))
    ss = _run(root, "gate", "session-start", payload={"session_id": "t"})
    _assert_ran(ss)
    assert "TEMPLATE SKEW" not in ss.stdout
    _assert_ran(_run(root, "status"))


def test_engine_behind_the_pin_refuses_hooks_and_commands(tmp_path):
    root = _project(tmp_path, _BEHIND_PIN)
    for argv, payload in ((("gate", "pre-tool"), _READ), (("gate", "post-tool"), _READ),
                          (("gate", "session-start"), {}), (("status",), None)):
        p = _run(root, *argv, payload=payload)
        _assert_refused(p, _BEHIND_PIN, str(_manifest(root)), "behind the pin",
                        f"prusik=={_BEHIND_PIN}")
    # The opt-out covers only AHEAD: behind refuses even when the project opted out.
    (root / ".claude" / "sprint-config.yaml").write_text(
        _TEMPLATE_CFG.read_text() + "\nauto_refresh_on_skew: false\n")
    _assert_refused(_run(root, "gate", "pre-tool", payload=_READ), _BEHIND_PIN)


def test_engine_ahead_refuses_and_the_opt_out_runs(tmp_path):
    root = _project(tmp_path, _AHEAD_PIN)
    p = _run(root, "gate", "pre-tool", payload=_READ)
    _assert_refused(p, _AHEAD_PIN, str(_manifest(root)), "ahead of the pin",
                    "`prusik refresh`", "`! prusik refresh`", "restart Claude Code")
    _assert_refused(_run(root, "status"), _AHEAD_PIN)

    opted = _project(tmp_path / "o", _AHEAD_PIN, opt_out=True)
    _assert_ran(_run(opted, "gate", "pre-tool", payload=_READ))
    _assert_ran(_run(opted, "status"))
    # The user-chosen opt-out keeps its loud session-start banner.
    ss = _run(opted, "gate", "session-start", payload={"session_id": "t"})
    _assert_ran(ss)
    assert "TEMPLATE SKEW" in ss.stdout and _AHEAD_PIN in ss.stdout


# ── the commands that move or diagnose the pin still run ───────────────────────────────

def test_exempt_commands_run_while_mismatched(tmp_path):
    root = _project(tmp_path, _BEHIND_PIN)
    v = _run(root, "--version")
    _assert_ran(v)
    assert v.stdout.strip() == f"prusik {_RUNNING}"
    for flag in ("-h", "--help"):
        assert "usage:" in _run(root, flag).stdout
        assert "usage:" in _run(root, "gate", "pre-tool", flag).stdout
    _assert_ran(_run(root, "init", "--list-stacks"))
    _assert_ran(_run(root, "feedback", "--list"))
    doc = _run(root, "doctor")
    assert "refusing" not in doc.stderr and "Engine resolution" in doc.stdout, doc
    assert doc.returncode == 1, doc               # it RUNS, and reports the mismatch
    assert "running engine refused" in doc.stdout and _BEHIND_PIN in doc.stdout
    ref = _run(root, "refresh")
    assert "refusing" not in ref.stderr and "[prusik-refresh]" in ref.stdout + ref.stderr
    un = _run(root, "uninstall", "--force")
    assert "refusing" not in un.stderr and un.returncode != 2, un
    # The exemption is per command: a non-exempt command in the same project still refuses.
    _assert_refused(_run(_project(tmp_path / "b", _BEHIND_PIN), "status"), _BEHIND_PIN)


def test_exempt_list_is_declared_once_next_to_the_hook_commands():
    assert set(cli.PIN_EXEMPT) == {"--version", "-h", "--help", "init", "refresh", "doctor",
                                   "uninstall", "feedback"}
    assert cli.PIN_SYNCS_WHEN_AHEAD == (("gate", "sprint-start"),)
    src = (_REPO / "prusik" / "__main__.py").read_text()
    assert src.index("PIN_EXEMPT = ") - src.index("HOOK_COMMANDS = ") < 800


def test_refresh_while_ahead_moves_the_pin_and_the_next_hook_runs(tmp_path):
    root = _initialised(tmp_path)
    _set_pin(root, _AHEAD_PIN)
    _assert_refused(_run(root, "gate", "pre-tool", payload=_READ), _AHEAD_PIN)
    _assert_ran(_run(root, "refresh"))
    assert pin.resolve(root)[0] == _RUNNING
    _assert_ran(_run(root, "gate", "pre-tool", payload=_READ))


# ── what has no pin, and the source repo's pin ─────────────────────────────────────────

def test_no_manifest_and_not_the_source_repo_is_unchecked(tmp_path):
    root = _project(tmp_path, None)
    # A look-alike: a project whose name merely starts with prusik, with its own package.
    (root / "pyproject.toml").write_text('[project]\nname = "prusik-tools"\n')
    (root / "prusik").mkdir()
    (root / "prusik" / "__init__.py").write_text('__version__ = "0.1.0"\n')
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _assert_ran(_run(root, "gate", "pre-tool", payload=_READ, cwd=elsewhere))
    _assert_ran(_run(root, "status", cwd=elsewhere))
    assert pin.resolve(root) == (None, None, None)


def test_source_repo_pin_is_its_source_version(tmp_path):
    root = tmp_path / "src"
    (root / "prusik").mkdir(parents=True)
    (root / ".claude").mkdir()
    (root / "pyproject.toml").write_text('[build-system]\nrequires = []\n\n'
                                         '[project]\nname = "prusik"\ndynamic = ["version"]\n')
    init = root / "prusik" / "__init__.py"
    elsewhere = tmp_path / "elsewhere"          # never import the fake package from the cwd
    elsewhere.mkdir()
    init.write_text(f'__version__ = "{_BEHIND_PIN}"\n')
    p = _run(root, "status", cwd=elsewhere)
    _assert_refused(p, _BEHIND_PIN, str(init), "pip install -e")
    init.write_text(f'__version__ = "{_RUNNING}"\n')
    _assert_ran(_run(root, "status", cwd=elsewhere))
    assert pin.resolve(root) == (_RUNNING, init, "source")
    # A manifest, when present, is the pin even in a repo named prusik.
    _manifest(root).write_text(json.dumps({"template_surface_version": _RUNNING}))
    init.write_text(f'__version__ = "{_BEHIND_PIN}"\n')
    _assert_ran(_run(root, "status", cwd=elsewhere))


# ── a corrupt pin refuses, never runs unchecked ───────────────────────────────────────

def test_corrupt_or_unreadable_manifest_refuses(tmp_path):
    cases = {"not-json": "{not json", "array": "[]", "no-version": '{"files": []}',
             "bad-version": '{"template_surface_version": "latest"}'}
    for name, text in cases.items():
        root = _project(tmp_path / name, None)
        _manifest(root).write_text(text)
        p = _run(root, "gate", "pre-tool", payload=_READ)
        _assert_refused(p, str(_manifest(root)), "git checkout --")
        _assert_refused(_run(root, "status"), str(_manifest(root)))
        # The repair commands are not locked out by the corrupt pin.
        assert "refusing" not in _run(root, "refresh").stderr
    root = _project(tmp_path / "unreadable", _RUNNING)
    _manifest(root).chmod(0)
    try:
        if os.access(_manifest(root), os.R_OK):    # root ignores modes: no unreadable file
            _manifest(root).unlink()
            _manifest(root).mkdir()                 # a directory where the file must be
        _assert_refused(_run(root, "gate", "pre-tool", payload=_READ), "unreadable")
    finally:
        _manifest(root).chmod(0o644)


# ── stop cannot loop ───────────────────────────────────────────────────────────────

def test_stop_with_stop_hook_active_exits_zero_while_mismatched(tmp_path):
    active = {"session_id": "t", "transcript_path": str(tmp_path / "t.jsonl"),
              "stop_hook_active": True}
    # Control: an equal pin's continuing stop registers the session, then ends the turn.
    equal = _project(tmp_path / "eq", _RUNNING)
    _assert_ran(_run(equal, "gate", "stop", payload=active))
    assert (equal / ".sprint" / "sessions.json").exists()

    root = _project(tmp_path, _BEHIND_PIN)
    p = _run(root, "gate", "stop", payload=active)
    assert p.returncode == 0 and p.stdout == "", (p.returncode, p.stdout, p.stderr)
    # It ends the turn without acting: the mismatched engine registered no session.
    assert not (root / ".sprint" / "sessions.json").exists()
    # The first stop (not yet continuing) still refuses, so the mismatch is seen.
    first = {k: v for k, v in active.items() if k != "stop_hook_active"}
    _assert_refused(_run(root, "gate", "stop", payload=first), _BEHIND_PIN)


# ── a launched child imports its parent's prusik ────────────────────────────────────

def test_child_with_a_foreign_origin_refuses(tmp_path):
    root = _project(tmp_path, _RUNNING)
    foreign = str(tmp_path / "other" / "prusik")
    p = _run(root, "status", env={pin.ORIGIN_ENV: foreign})
    _assert_refused(p, foreign, pin.ORIGIN_ENV)
    # Origin is checked for every command, the exempt ones included, and with no pin at all.
    _assert_refused(_run(root, "--version", env={pin.ORIGIN_ENV: foreign}), foreign)
    _assert_refused(_run(_project(tmp_path / "u", None), "status",
                         env={pin.ORIGIN_ENV: foreign}), foreign)
    # The parent's own package is accepted.
    _assert_ran(_run(root, "status", env={pin.ORIGIN_ENV: pin.package_dir()}))


def test_launcher_child_never_runs_a_shadowing_package_in_its_cwd(tmp_path):
    """drive's children start in the project root. A `prusik/` package there (a copy of an
    engine) used to win the import. Through the launcher it is either kept off sys.path
    (PYTHONSAFEPATH, 3.11+) or imported and refused (origin check, 3.10)."""
    root = _project(tmp_path, _RUNNING)
    shutil.copytree(Path(pin.package_dir()), root / "prusik",
                    ignore=shutil.ignore_patterns("templates", "__pycache__"))
    argv, env = pin.child_command("status")
    assert argv[:3] == [sys.executable, "-m", "prusik"]
    assert env[pin.ORIGIN_ENV] == pin.package_dir() and env["PYTHONSAFEPATH"] == "1"
    env["CLAUDE_PROJECT_DIR"] = str(root)
    p = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, check=False,
                       timeout=120)
    if sys.version_info >= (3, 11):
        _assert_ran(p)
    else:
        _assert_refused(p, str(root / "prusik"))


def test_launch_env_is_one_hop(tmp_path):
    """A child drops what the launcher added, so its own children (a captured test suite)
    get the operator's environment: no inherited origin, the cwd import intact."""
    root = _project(tmp_path, _RUNNING)
    seen = tmp_path / "grandchild-env.json"
    probe = ("import json,os,sys; json.dump({k: os.environ.get(k) for k in "
             "('PRUSIK_ORIGIN', 'PYTHONSAFEPATH', 'PRUSIK_LAUNCH_SAFEPATH')}, "
             f"open({str(seen)!r}, 'w'))")
    argv, env = pin.child_command("prove", "--kind", "lint", "--", sys.executable, "-c", probe)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    p = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True, check=False,
                       timeout=120)
    assert "refusing" not in p.stderr, p.stderr
    assert json.loads(seen.read_text()) == {
        "PRUSIK_ORIGIN": None, "PYTHONSAFEPATH": os.environ.get("PYTHONSAFEPATH"),
        "PRUSIK_LAUNCH_SAFEPATH": None}, (p.stdout, p.stderr)
    # An operator's own PYTHONSAFEPATH is theirs: the launcher neither adds nor removes it.
    _, env2 = pin.child_command("status", environ={"PYTHONSAFEPATH": "x"})
    assert env2["PYTHONSAFEPATH"] == "x" and "PRUSIK_LAUNCH_SAFEPATH" not in env2
    own = {"PYTHONSAFEPATH": "x", pin.ORIGIN_ENV: "/p"}
    pin.consume_launch_env(own)
    assert own == {"PYTHONSAFEPATH": "x"}


def test_every_prusik_child_spawn_goes_through_the_launcher():
    """The shape the finding came from: an inline `sys.executable -m prusik` child."""
    pat = re.compile(r"""["']-m["']\s*,\s*["']prusik["']""")
    hits = [f"{p.relative_to(_REPO)}:{i}"
            for p in sorted((_REPO / "prusik").rglob("*.py")) if p.name != "pin.py"
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if pat.search(line)]
    assert hits == [], hits


# ── sprint-start moves the pin itself, only when ahead ────────────────────────────────

def _startable(root: Path, *, opt_out: bool = False) -> None:
    cfg_path = root / ".claude" / "sprint-config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    for spec in (cfg.get("pre_sprint_gates") or {}).values():
        spec["enabled"] = False
    if opt_out:
        cfg["auto_refresh_on_skew"] = False
    cfg_path.write_text(yaml.safe_dump(cfg))
    (root / "briefs").mkdir(exist_ok=True)
    (root / "briefs" / "feat.md").write_text(_BRIEF)


def test_sprint_start_while_ahead_auto_syncs_and_starts(tmp_path):
    root = _initialised(tmp_path)
    _startable(root)
    _set_pin(root, _AHEAD_PIN)
    p = _run(root, "gate", "sprint-start", "feat")
    _assert_ran(p)
    assert "Sprint started: feat" in p.stdout
    assert pin.resolve(root)[0] == _RUNNING
    assert json.loads((root / ".sprint" / "state.json").read_text())["feature"] == "feat"
    _assert_ran(_run(root, "gate", "pre-tool", payload=_READ))     # the pin moved


def test_sprint_start_refuses_ahead_with_the_opt_out_and_behind(tmp_path):
    root = _initialised(tmp_path)
    _startable(root, opt_out=True)
    _set_pin(root, _AHEAD_PIN)
    p = _run(root, "gate", "sprint-start", "feat")
    _assert_refused(p, _AHEAD_PIN, "ahead of the pin", "`prusik refresh`")
    assert not (root / ".sprint" / "state.json").exists()
    assert pin.resolve(root)[0] == _AHEAD_PIN                       # opt-out: no sync

    behind = _initialised(tmp_path / "b")
    _startable(behind)
    _set_pin(behind, _BEHIND_PIN)
    p = _run(behind, "gate", "sprint-start", "feat")
    _assert_refused(p, _BEHIND_PIN, "behind the pin")
    assert not (behind / ".sprint" / "state.json").exists()
    assert pin.resolve(behind)[0] == _BEHIND_PIN


# ── doctor shows the resolution ────────────────────────────────────────────────────

def _fake_prusik(tmp_path: Path, version: str) -> dict:
    bindir = tmp_path / f"bin-{version}"
    bindir.mkdir(parents=True)
    exe = bindir / "prusik"
    exe.write_text(f"#!/bin/sh\necho 'prusik {version}'\n")
    exe.chmod(0o755)
    return {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}


def test_doctor_fails_on_a_path_prusik_that_is_not_the_pin(tmp_path):
    root = _project(tmp_path, _RUNNING)
    stale = "0.1.0"
    p = _run(root, "doctor", env=_fake_prusik(tmp_path, stale))
    assert p.returncode == 1, (p.stdout, p.stderr)
    fake = str(tmp_path / f"bin-{stale}" / "prusik")
    assert f"PATH prusik {fake} is {stale}, not the pin {_RUNNING}" in p.stdout, p.stdout
    assert f"pin:      {_RUNNING} (from {_manifest(root)})" in p.stdout
    assert f"running:  {_RUNNING} (from {os.path.abspath(prusik.__file__)})" in p.stdout
    j = json.loads(_run(root, "doctor", "--json", env=_fake_prusik(tmp_path / "j", stale))
                   .stdout)
    assert j["engine"]["path_version"] == stale and j["engine"]["failures"]

    ok = _run(root, "doctor", env=_fake_prusik(tmp_path / "ok", _RUNNING))
    assert ok.returncode == 0 and "✗" not in ok.stdout, ok.stdout
    assert "the running and the PATH prusik match the pin" in ok.stdout
