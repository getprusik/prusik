"""The open-core boundary gate must find the private registry from a linked worktree.

The pre-commit hook probed `$(git rev-parse --show-toplevel)/../prusik-hq/…`. In a sprint
worktree the toplevel IS the worktree, so the probe missed, the hook printed "SKIPPED" and
let every worktree commit through with rc 0. `test_boundary.py` probed only `<repo>/hq/`
and so always skipped here. One `discover_registry` now resolves the MAIN checkout through
git's common dir; these tests drive the real hook through real `git commit`s in a real
main-tree + sibling-HQ + linked-worktree layout.

moat-finding: fb-92d4815981be
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SCRIPTS = ("boundary_check.py", "pre-commit-boundary.sh", "install-hooks.sh")
_TOKEN = "zq" + "vendorx"          # runtime-built; never a literal registry label in source


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if k != "PRUSIK_HQ_REGISTRY" and not k.startswith("GIT_")}
    # Hermetic git: no global/system hooksPath or identity can change what runs.
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", **extra)
    return env


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, env=env or _env())


def _layout(tmp: Path, registry: bool = True) -> tuple[Path, Path]:
    """<tmp>/engine (main checkout, hooks installed from the worktree),
    <tmp>/prusik-hq/hq/products.local.json, and engine/worktrees/solo."""
    main = tmp / "engine"
    (main / "scripts").mkdir(parents=True)
    for name in _SCRIPTS:
        shutil.copy2(_REPO / "scripts" / name, main / "scripts" / name)
    (main / "prusik").mkdir()
    (main / "prusik" / "core.py").write_text("x = 1\n")
    (main / ".gitignore").write_text("worktrees/\n")
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t.t")
    _git(main, "config", "user.name", "t")
    _git(main, "add", ".")
    assert _git(main, "commit", "-qm", "base").returncode == 0
    if registry:
        reg = tmp / "prusik-hq" / "hq" / "products.local.json"
        reg.parent.mkdir(parents=True)
        reg.write_text(json.dumps({"products": [{"label": _TOKEN}]}))
    wt = main / "worktrees" / "solo"
    assert _git(main, "worktree", "add", "-q", str(wt), "-b", "sprint/x").returncode == 0
    inst = subprocess.run(["bash", str(wt / "scripts" / "install-hooks.sh")], cwd=wt,
                          capture_output=True, text=True, env=_env())
    assert inst.returncode == 0, inst.stderr
    return main, wt


def _commit(tree: Path, rel: str, text: str, env: dict[str, str] | None = None):
    f = tree / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    _git(tree, "add", rel, env=env)
    return _git(tree, "commit", "-qm", "change", env=env)


def _bc():
    spec = importlib.util.spec_from_file_location("boundary_check",
                                                  _REPO / "scripts" / "boundary_check.py")
    bc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bc)
    return bc


def test_install_hooks_from_worktree_links_the_common_hook(tmp_path):
    main, _ = _layout(tmp_path)
    hook = main / ".git" / "hooks" / "pre-commit"
    assert hook.is_symlink()
    assert hook.resolve() == (main / "scripts" / "pre-commit-boundary.sh").resolve()


def test_worktree_commit_with_leak_is_blocked(tmp_path):
    _, wt = _layout(tmp_path)
    proc = _commit(wt, "prusik/leak.py", f"who = '{_TOKEN}'\n")
    assert proc.returncode != 0, proc.stderr
    assert "BLOCKED" in proc.stderr and "SKIPPED" not in proc.stderr
    assert "leak.py" not in _git(wt, "show", "--stat", "HEAD").stdout


def test_main_tree_commit_with_leak_is_blocked(tmp_path):
    main, _ = _layout(tmp_path)
    proc = _commit(main, "prusik/leak.py", f"who = '{_TOKEN}'\n")
    assert proc.returncode != 0
    assert "BLOCKED" in proc.stderr and "SKIPPED" not in proc.stderr


def test_clean_worktree_commit_passes(tmp_path):
    _, wt = _layout(tmp_path)
    proc = _commit(wt, "prusik/ok.py", "y = 2\n")
    assert proc.returncode == 0, proc.stderr
    assert "SKIPPED" not in proc.stderr


def test_worktree_scans_its_own_tree_not_main(tmp_path):
    """A leak sitting only in the MAIN checkout must not block a clean worktree commit:
    the hook scans the tree being committed."""
    main, wt = _layout(tmp_path)
    (main / "prusik" / "leak.py").write_text(f"who = '{_TOKEN}'\n")
    proc = _commit(wt, "prusik/ok.py", "y = 2\n")
    assert proc.returncode == 0, proc.stderr


def test_no_registry_skips_from_main_and_worktree(tmp_path):
    main, wt = _layout(tmp_path, registry=False)
    for tree in (main, wt):
        proc = _commit(tree, f"prusik/{tree.name}.py", f"who = '{_TOKEN}'\n")
        assert proc.returncode == 0, proc.stderr
        assert "SKIPPED" in proc.stderr


def test_env_named_missing_registry_blocks(tmp_path):
    _, wt = _layout(tmp_path)
    env = _env(PRUSIK_HQ_REGISTRY=str(tmp_path / "nope.json"))
    proc = _commit(wt, "prusik/ok.py", "y = 2\n", env=env)
    assert proc.returncode != 0
    assert "BLOCKED" in proc.stderr and "SKIPPED" not in proc.stderr


def test_env_named_malformed_registry_blocks(tmp_path):
    _, wt = _layout(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"products": []}))
    proc = _commit(wt, "prusik/ok.py", "y = 2\n", env=_env(PRUSIK_HQ_REGISTRY=str(bad)))
    assert proc.returncode != 0
    assert "BLOCKED" in proc.stderr and "SKIPPED" not in proc.stderr


def test_discover_registry_order(tmp_path, monkeypatch):
    bc = _bc()
    main, wt = _layout(tmp_path)
    sibling = tmp_path / "prusik-hq" / "hq" / "products.local.json"
    monkeypatch.delenv("PRUSIK_HQ_REGISTRY", raising=False)
    assert bc.discover_registry(wt) == sibling      # from the worktree, via the common dir
    assert bc.discover_registry(main) == sibling
    monkeypatch.setenv("PRUSIK_HQ_REGISTRY", str(tmp_path / "x.json"))
    assert bc.discover_registry(wt) == tmp_path / "x.json"   # env: authoritative, even missing
    monkeypatch.delenv("PRUSIK_HQ_REGISTRY")
    shutil.rmtree(tmp_path / "prusik-hq")
    assert bc.discover_registry(wt) is None
    inner = wt / "hq" / "products.local.json"
    inner.parent.mkdir()
    inner.write_text("{}")
    assert bc.discover_registry(wt) == inner


def test_locate_registry_cli(tmp_path):
    _, wt = _layout(tmp_path)
    script = str(wt / "scripts" / "boundary_check.py")
    found = subprocess.run([sys.executable, script, "--root", str(wt), "--locate-registry"],
                           capture_output=True, text=True, env=_env())
    assert found.returncode == 0
    assert found.stdout.strip() == str(tmp_path / "prusik-hq" / "hq" / "products.local.json")
    shutil.rmtree(tmp_path / "prusik-hq")
    none = subprocess.run([sys.executable, script, "--root", str(wt), "--locate-registry"],
                          capture_output=True, text=True, env=_env())
    assert none.returncode == 3 and none.stdout == ""
