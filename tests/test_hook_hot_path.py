"""Hook hot path — pay only for what the hook runs (platform plan P0, sprint 1).

Every tool call spawns `prusik gate pre-tool` and `post-tool`. Measured 2026-10-25: ~90ms
median, of which only ~27ms was the interpreter — prusik owned the rest: building the full
108-subparser CLI before dispatching (~22ms), importing modules the hook never executes
(~12ms), and the pure-Python YAML loader (~3.6ms). A slow gate gets disabled, and a disabled
gate protects nothing — so the cost is cut AND pinned.

The deterministic guards are the CI gate (no parser built; per-hook prusik, third-party and
stdlib import allowlists and spawn budgets, measured per payload class and phase; lazy imports
that can't fail open). The wall-clock ratio is local only: a deliberately coarse
large-regression catch — min-of-N, normalized by the same interpreter's own startup. It skips
under CI because stop sits at the budget on shared runners, so noise alone turned main red
(fb-e10892101b85).
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

from prusik import __main__ as cli
from prusik import gate, phases

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_CFG = _ROOT / "prusik" / "templates" / ".claude" / "sprint-config.yaml"
_PRESETS = sorted((_ROOT / "prusik" / "templates" / ".claude" / "sprint-config-presets")
                  .glob("*.yaml"))
_LAZY = ("schema", "consistency", "discovery")

# Measured allowlists (design/hook-hot-path/scope.md "## Allowlists").
_PRE_TOOL = {"prusik", "prusik.__main__", "prusik.gate", "prusik.ledger", "prusik.phases",
             "prusik.platform_adapter", "prusik.gate_class", "prusik.main_writer",
             "prusik.evidence", "prusik.fix_round"}
_POST_TOOL = {"prusik", "prusik.__main__", "prusik.gate", "prusik.ledger", "prusik.phases",
              "prusik.gate_class", "prusik.evidence"}
# Stop runs once per turn (measured 2026-10-26 per phase): exit-artifact checks pull pause /
# schema / consistency; session registration (fb-a1b7db140a5e) adds usage + platform_adapter.
_STOP = {"prusik", "prusik.__main__", "prusik.gate", "prusik.ledger", "prusik.phases",
         "prusik.gate_class", "prusik.evidence", "prusik.pause", "prusik.schema",
         "prusik.consistency", "prusik.usage", "prusik.platform_adapter"}
# Public stdlib top-levels above the baseline: the union measured on macOS and Linux across
# 3.10–3.13 (2026-10-30). The names differ by version (ipaddress and urllib up to 3.12, glob
# and pwd in 3.13), so a list measured on one interpreter reds the others.
_POST_TOOL_STDLIB = {"argparse", "contextlib", "errno", "fnmatch", "gettext", "glob", "grp",
                     "hashlib", "ipaddress", "math", "ntpath", "pathlib", "pwd", "shlex",
                     "urllib", "warnings"}
_PRE_TOOL_STDLIB = _POST_TOOL_STDLIB | {"ast", "copy", "dataclasses", "dis", "importlib",
                                        "inspect", "linecache", "opcode", "token", "tokenize",
                                        "typing", "weakref"}
# Stop alone may spawn, so it alone may import subprocess and what subprocess pulls in.
_STOP_STDLIB = _PRE_TOOL_STDLIB | {"fcntl", "locale", "select", "selectors", "signal",
                                   "subprocess", "threading"}
_REVIEWING_STOP_SPAWNS = 2                     # the git calls measured in reviewing


# ---- fast dispatch: exact argv only, single source of hook names ------------------------

def test_hook_names_declared_once():
    assert cli.HOOK_COMMANDS == ("pre-tool", "post-tool", "stop", "session-start")


@pytest.mark.parametrize("hook", ["pre-tool", "post-tool", "stop", "session-start"])
def test_exact_hook_argv_dispatches_without_building_the_parser(hook, monkeypatch):
    called = []

    def no_parser(*a, **k):
        raise AssertionError("the CLI parser must not be built for a hook")

    monkeypatch.setattr(argparse, "ArgumentParser", no_parser)
    monkeypatch.setattr(gate, hook.replace("-", "_"), lambda args=None: called.append(hook) or 0)
    monkeypatch.setattr(sys, "argv", ["prusik", "gate", hook])
    assert cli.main() == 0
    assert called == [hook]


def test_non_exact_argv_falls_through_to_the_full_parser(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prusik", "gate", "pre-tool", "--help"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 0 and "usage:" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["prusik", "gate", "pre-tool", "extra"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 2                                  # argparse's own rejection


def test_parser_still_registers_every_hook(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["prusik", "gate", "--help"])
    with pytest.raises(SystemExit):
        cli.main()
    out = capsys.readouterr().out
    assert all(h in out for h in cli.HOOK_COMMANDS)


# ---- per-hook allowlists and spawn budgets, measured in a fresh subprocess via the console entry

# The audit hook records spawns without importing anything, so it can't change the module set
# under test.
_PROBE = r"""
import json, os, sys
spawns = []
_SPAWN_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.fork", "os.forkpty",
                 "os.posix_spawn", "os.spawn"}
sys.addaudithook(lambda event, _: spawns.append(event) if event in _SPAWN_EVENTS else None)
from prusik.__main__ import main
sys.argv = ["prusik", "gate", os.environ["HOOK"]]
try:
    main()
except SystemExit:
    pass
json.dump({"modules": sorted(sys.modules), "spawns": spawns}, open(os.environ["OUT"], "w"))
"""


def _project(tmp_path: Path, phase: str | None, *, fix_round: bool = False) -> Path:
    (tmp_path / ".claude").mkdir()
    shutil.copy(_TEMPLATE_CFG, tmp_path / ".claude" / "sprint-config.yaml")
    (tmp_path / ".sprint").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    if phase:
        (tmp_path / ".sprint" / "state.json").write_text(
            json.dumps({"phase": phase, "feature": "feat"}))
    if fix_round:
        (tmp_path / ".sprint" / "fix-round.json").write_text(
            json.dumps({"feature": "feat", "round": 1, "started_at": "2026-10-25T00:00:00"}))
    return tmp_path


def _probe(root: Path, hook: str, payload: dict | None,
           code: str = _PROBE) -> tuple[set[str], list[str]]:
    out = root / "mods.json"
    env = {**os.environ, "HOOK": hook, "OUT": str(out), "CLAUDE_PROJECT_DIR": str(root)}
    subprocess.run([sys.executable, "-c", code], input=json.dumps(payload or {}), text=True,
                   capture_output=True, cwd=root, env=env, check=False, timeout=60)
    measured = json.loads(out.read_text())
    return set(measured["modules"]), measured["spawns"]


def _third_party(mods: set[str], baseline: set[str]) -> set[str]:
    tops = {m.split(".")[0] for m in mods - baseline}
    return {t for t in tops if t not in sys.stdlib_module_names and t != "prusik"}


def _stdlib(mods: set[str], baseline: set[str]) -> set[str]:
    # Leading-underscore C accelerators differ by build; their public module is what's pinned.
    tops = {m.split(".")[0] for m in mods - baseline}
    return {t for t in tops if t in sys.stdlib_module_names and not t.startswith("_")}


def _baseline(root: Path) -> set[str]:
    # The one allowed third-party dependency is yaml — including whatever yaml itself loads
    # (libyaml's Cython runtime), so the baseline is "interpreter + yaml", not a name list.
    return _probe(root, "none", None,
                  code="import json,os,sys,yaml; yaml.load('a: 1', Loader=getattr("
                       "yaml,'CSafeLoader',yaml.SafeLoader)); json.dump({'modules': "
                       "sorted(sys.modules), 'spawns': []}, open(os.environ['OUT'],'w'))")[0]


def _assert_hook_cost(root: Path, hook: str, payload: dict, prusik_allowed: set[str],
                      stdlib_allowed: set[str], *, max_spawns: int = 0,
                      code: str = _PROBE) -> set[str]:
    """One check for every hook run: what it imports and what it spawns. Returns the prusik
    modules it imported."""
    mods, spawns = _probe(root, hook, payload, code)
    baseline = _baseline(root)
    prusik_mods = {m for m in mods if m == "prusik" or m.startswith("prusik.")}
    assert prusik_mods <= prusik_allowed, sorted(prusik_mods - prusik_allowed)
    assert not _third_party(mods, baseline)
    extra_stdlib = _stdlib(mods, baseline) - stdlib_allowed
    assert not extra_stdlib, sorted(extra_stdlib)
    assert len(spawns) <= max_spawns, spawns
    return prusik_mods


def _pre(tool: str, inp: dict) -> dict:
    return {"session_id": "probe", "hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": inp}


_PRE_CASES = [
    (None, False, _pre("Read", {"file_path": "README.md"})),
    ("building", False, _pre("Read", {"file_path": "README.md"})),
    ("building", False, _pre("Edit", {"file_path": "worktrees/solo/a.py"})),     # allowed
    ("building", False, _pre("Edit", {"file_path": "src/app.py"})),              # denied
    ("building", False, _pre("Bash", {"command": "echo hi > out.txt"})),        # denied redirect
    ("building", False, _pre("Bash", {"command": "git commit -m x"})),          # mutating git
    ("scoping", False, _pre("Edit", {"file_path": "src/app.py"})),
    ("reviewing", False, _pre("Edit", {"file_path": "src/app.py"})),
    ("reviewing", True, _pre("Edit", {"file_path": "worktrees/solo/a.py"})),     # active fix round
    ("reviewing", True, _pre("Bash", {"command": "echo hi > out.txt"})),
    # fb-1e3ecda9d011: a brief write runs the briefing recorder — no new module import
    (None, False, _pre("Edit", {"file_path": "briefs/new-thing.md"})),
    ("building", False, _pre("Edit", {"file_path": "briefs/other-thing.md"})),
    (None, False, _pre("Bash", {"command": "echo x > briefs/new-thing.md"})),
]


@pytest.mark.parametrize("phase,fix_round,payload", _PRE_CASES)
def test_pre_tool_imports_only_its_allowlist(tmp_path, phase, fix_round, payload):
    root = _project(tmp_path, phase, fix_round=fix_round)
    _assert_hook_cost(root, "pre-tool", payload, _PRE_TOOL, _PRE_TOOL_STDLIB)


@pytest.mark.parametrize("phase,payload", [
    ("building", {"session_id": "p", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                  "tool_input": {"command": "pytest -q"},
                  "tool_response": {"stdout": "1 failed"}}),
    ("building", {"session_id": "p", "hook_event_name": "PostToolUse", "tool_name": "Edit",
                  "tool_input": {"file_path": "worktrees/solo/a.py"}, "tool_response": {}}),
    (None, {"session_id": "p", "hook_event_name": "PostToolUse", "tool_name": "Read",
            "tool_input": {"file_path": "README.md"}, "tool_response": {}}),
])
def test_post_tool_imports_only_its_allowlist(tmp_path, phase, payload):
    root = _project(tmp_path, phase)
    _assert_hook_cost(root, "post-tool", payload, _POST_TOOL, _POST_TOOL_STDLIB)


_STOP_PAYLOAD = {"session_id": "p", "hook_event_name": "Stop",
                 "transcript_path": "/nonexistent/p.jsonl"}


@pytest.mark.parametrize("phase", [None, "solo_execute", "reviewing", "integrating"])
def test_stop_imports_only_its_allowlist(tmp_path, phase):
    root = _project(tmp_path, phase)
    budget = _REVIEWING_STOP_SPAWNS if phase == "reviewing" else 0
    prusik_mods = _assert_hook_cost(root, "stop", _STOP_PAYLOAD, _STOP, _STOP_STDLIB,
                                    max_spawns=budget)
    assert "prusik.usage" in prusik_mods                     # registration actually ran


# The guards must reject the regressions wall clock used to catch: a check that can't fail
# would leave CI with no hook-cost gate at all.
# moat-finding: fb-e10892101b85
def _regressed(statement: str) -> str:
    return _PROBE.replace("sys.argv =", f"{statement}\nsys.argv =")


def test_a_new_heavy_stdlib_import_fails_the_cost_check(tmp_path):
    root = _project(tmp_path, None)
    payload = _pre("Read", {"file_path": "README.md"})
    with pytest.raises(AssertionError, match="asyncio"):
        _assert_hook_cost(root, "pre-tool", payload, _PRE_TOOL, _PRE_TOOL_STDLIB,
                          code=_regressed("import asyncio"))


@pytest.mark.parametrize("statement,event", [
    ("import subprocess; subprocess.run(['true'])", "subprocess.Popen"),
    ("os.system('true')", "os.system"),
])
def test_a_new_spawn_fails_the_cost_check(tmp_path, statement, event):
    # Stop's allowlist already holds subprocess, so only the spawn budget can catch this.
    root = _project(tmp_path, None)
    with pytest.raises(AssertionError, match=event):
        _assert_hook_cost(root, "stop", _STOP_PAYLOAD, _STOP, _STOP_STDLIB,
                          code=_regressed(statement))


# ---- lazy imports can't fail open -------------------------------------------------------

def _gate_tree() -> ast.Module:
    return ast.parse((_ROOT / "prusik" / "gate.py").read_text())


def test_heavy_modules_are_not_imported_at_gate_module_level():
    for node in _gate_tree().body:
        if isinstance(node, ast.ImportFrom) and node.module == "prusik":
            bound = {a.asname or a.name for a in node.names}
            assert not bound & set(_LAZY), f"gate.py:{node.lineno} imports {bound & set(_LAZY)}"


def test_every_lazy_use_is_bound_by_a_leading_local_import():
    # A local import binds the name for the WHOLE function: placed in one branch, a use on
    # another path raises UnboundLocalError — in a hook deny branch that fails OPEN.
    for fn in _gate_tree().body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id in _LAZY}
        if not used:
            continue
        body = fn.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]                                    # docstring
        leading: set[str] = set()
        for stmt in body:
            if not isinstance(stmt, (ast.Import, ast.ImportFrom)):
                break
            if isinstance(stmt, ast.ImportFrom) and stmt.module == "prusik":
                leading |= {a.asname or a.name for a in stmt.names}
        assert used <= leading, (f"{fn.name} (gate.py:{fn.lineno}) uses {sorted(used)} but its "
                                 f"leading import block binds only {sorted(leading)}")


def test_every_function_local_prusik_import_resolves():
    for node in ast.walk(_gate_tree()):
        if isinstance(node, ast.ImportFrom) and node.module == "prusik" and node.col_offset:
            for a in node.names:
                assert importlib.util.find_spec(f"prusik.{a.name}"), \
                    f"gate.py:{node.lineno}: prusik.{a.name} does not resolve"


# ---- YAML: C loader when available, identical results, same failure type ------------------

def _configs() -> list[Path]:
    return [_TEMPLATE_CFG, *_PRESETS, _ROOT / ".claude" / "sprint-config.yaml"]


def _load_via_phases(tmp_path: Path, text: str):
    (tmp_path / ".claude").mkdir(exist_ok=True)
    (tmp_path / ".claude" / "sprint-config.yaml").write_text(text)
    return phases.load_sprint_config(tmp_path)


@pytest.mark.parametrize("cfg", _configs(), ids=lambda p: p.name if p.parent.name != ".claude"
                         or p.parent.parent != _ROOT else "repo-config")
def test_config_loads_identically_to_the_pure_python_safe_loader(tmp_path, cfg):
    text = cfg.read_text()
    assert _load_via_phases(tmp_path, text) == yaml.load(text, Loader=yaml.SafeLoader)


def test_c_loader_is_used_when_available(tmp_path, monkeypatch):
    seen = []
    real = yaml.load
    monkeypatch.setattr(yaml, "load", lambda s, Loader: seen.append(Loader) or real(s, Loader))
    _load_via_phases(tmp_path, "a: 1\n")
    if getattr(yaml, "__with_libyaml__", False):
        assert seen == [yaml.CSafeLoader]


def test_malformed_config_raises_the_same_error_type_as_the_safe_loader(tmp_path):
    bad = "phases:\n  - name: scoping\n    writable: [unclosed\n"
    with pytest.raises(yaml.YAMLError) as ref:
        yaml.load(bad, Loader=yaml.SafeLoader)
    with pytest.raises(yaml.YAMLError) as got:
        _load_via_phases(tmp_path, bad)
    assert type(got.value).__name__ == type(ref.value).__name__


def test_fallback_without_libyaml_gives_identical_results(tmp_path, monkeypatch):
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)
    text = _TEMPLATE_CFG.read_text()
    assert _load_via_phases(tmp_path, text) == yaml.load(text, Loader=yaml.SafeLoader)


# ---- coarse wall-clock catch (min-of-N, machine-normalized), local only -------------------

def _wall_clock_skipped(env: Mapping[str, str]) -> bool:
    return bool(env.get("CI"))


_LOCAL_ONLY = pytest.mark.skipif(_wall_clock_skipped(os.environ),
                                 reason="wall clock is a local check on shared runners "
                                        "(fb-e10892101b85); the cost checks above gate CI")


def test_wall_clock_budget_is_local_only():
    assert _wall_clock_skipped({"CI": "true"})
    assert not _wall_clock_skipped({})
    for test in (test_pre_tool_owned_overhead_stays_within_coarse_budget,
                 test_stop_owned_overhead_stays_within_coarse_budget):
        assert _LOCAL_ONLY.mark in test.pytestmark


def _min_ms(cmd: list[str], root: Path, payload: str, n: int = 15, warmup: int = 3) -> float:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    best = float("inf")
    for i in range(warmup + n):
        t0 = time.perf_counter()
        subprocess.run(cmd, input=payload, text=True, capture_output=True, cwd=root, env=env,
                       check=False, timeout=60)
        if i >= warmup:
            best = min(best, (time.perf_counter() - t0) * 1000)
    return best


@_LOCAL_ONLY
def test_pre_tool_owned_overhead_stays_within_coarse_budget(tmp_path):
    root = _project(tmp_path, None)
    payload = json.dumps(_pre("Read", {"file_path": "README.md"}))
    hook = [sys.executable, "-c", "import sys; from prusik.__main__ import main; "
            "sys.argv=['prusik','gate','pre-tool']; sys.exit(main())"]
    bare = _min_ms([sys.executable, "-c", "pass"], root, "")
    owned = _min_ms(hook, root, payload) - bare
    assert owned <= 1.75 * bare, f"prusik-owned {owned:.1f}ms > 1.75 x bare {bare:.1f}ms"


@_LOCAL_ONLY
def test_stop_owned_overhead_stays_within_coarse_budget(tmp_path):
    root = _project(tmp_path, None)
    payload = json.dumps(_STOP_PAYLOAD)
    hook = [sys.executable, "-c", "import sys; from prusik.__main__ import main; "
            "sys.argv=['prusik','gate','stop']; sys.exit(main())"]
    bare = _min_ms([sys.executable, "-c", "pass"], root, "")
    owned = _min_ms(hook, root, payload) - bare
    assert owned <= 1.75 * bare, f"prusik-owned {owned:.1f}ms > 1.75 x bare {bare:.1f}ms"
