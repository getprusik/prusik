"""Observed writes, end to end (v0.259.0; fb-aa7a6121fcee, fb-c1d2d8dcabac, fb-16c859a406b9).

Every case drives the REAL hooks as the host does: `prusik gate pre-tool` in a subprocess,
then the actual command in a subprocess, then `prusik gate post-tool` (or the
PostToolUseFailure payload a non-zero exit fires, as recorded in platform_adapter.py), with
one `session_id` + `tool_use_id`. The sprint is started and advanced through the real
`prusik gate sprint-start` / `advance` CLI, so the phase-entry baseline is the one prusik
really takes. Assertions read the hook's JSON output and the project ledger, never module
internals. The unit-level tests of each primitive live in tests/test_observed_writes_unit.py.

Config: the shipped template, with three test-only edits made in `_config` (exit artifacts
and pre-sprint gates dropped, so advance checks ONLY observed writes; `prusik/**` added to
building's writable set, so there is a path that is writable in building and not in
reviewing).
"""

from __future__ import annotations

import ast
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parents[1]
_TEMPLATE_CFG = _REPO / "prusik" / "templates" / ".claude" / "sprint-config.yaml"
PY = sys.executable
F = "feat"
AGENT = "agent-session-b"
HOLDER = "holder-session-aaaaaaaaaaaa"
PREFIX = "[prusik-observed-write]"


# ---- rig -----------------------------------------------------------------------------------

def _config() -> dict:
    cfg = yaml.safe_load(_TEMPLATE_CFG.read_text())
    cfg["pre_sprint_gates"] = {}
    for spec in cfg["phases"]:
        spec.pop("exit_artifacts", None)
        spec.pop("trivial_exit_artifacts", None)
        if spec["name"] == "building":
            spec["writable"] = [*spec["writable"], "prusik/**"]
    return cfg


def _env(root: Path, **extra: str) -> dict:
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(root), **extra}
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.pop("PRUSIK_ADAPTER", None)
    return env


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root,
                   check=True, capture_output=True)


@pytest.fixture
def proj(tmp_path) -> Path:
    """A git repo with the template-derived config, a valid brief, and tracked files.
    No sprint yet."""
    root = tmp_path / "p"
    root.mkdir()
    (root / ".claude").mkdir()
    (root / ".claude" / "sprint-config.yaml").write_text(yaml.safe_dump(_config(),
                                                                         sort_keys=False))
    (root / ".gitignore").write_text("build/\ndesign/\n.sprint/\n")
    (root / "README.md").write_text("hello\n")
    (root / "docs.md").write_text("docs\n")
    (root / "prusik").mkdir()
    (root / "prusik" / "x.py").write_text("x = 1\n")
    (root / "prusik" / "gate.py").write_text("g = 1\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    (root / "briefs").mkdir()
    (root / "briefs" / f"{F}.md").write_text(
        "## Goal\nAdd retry logic to payment processor flow.\n\n## Success criteria\n"
        "99% of transient errors retried within 5s.\n\n## Type\nnew_feature\n")
    return root


def _gate(root: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-m", "prusik", "gate", *args], cwd=root,
                          env=env or _env(root), capture_output=True, text=True, timeout=120)


def _start(root: Path, to: str = "building") -> None:
    r = _gate(root, "sprint-start", F)
    assert r.returncode == 0, r.stderr
    for phase in ("planning", "building", "reviewing"):
        if to == "scoping":
            return
        r = _gate(root, "advance", phase, "--feature", F)
        assert r.returncode == 0, (phase, r.stderr)
        if phase == to:
            return


@pytest.fixture
def building(proj) -> Path:
    _start(proj, "building")
    return proj


def _events(root: Path, name: str | None = None) -> list[dict]:
    path = root / ".sprint" / "ledger.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [r for r in rows if name is None or r.get("event") == name]


def _pending(root: Path) -> list[str]:
    d = root / ".sprint" / "observed-writes" / "pending"
    return sorted(os.listdir(d)) if d.is_dir() else []


def _payload(root: Path, command: str, cid: str | None, session: str) -> dict:
    p = {"session_id": session, "tool_name": "Bash", "tool_input": {"command": command},
         "cwd": str(root)}
    if cid is not None:
        p["tool_use_id"] = cid
    return p


def _pre(root: Path, command: str, *, cid: str | None = "toolu_1", session: str = AGENT,
         env: dict | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run(
        [PY, "-m", "prusik", "gate", "pre-tool"], cwd=root, env=env or _env(root),
        input=json.dumps({**_payload(root, command, cid, session),
                          "hook_event_name": "PreToolUse"}),
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return r


def _post(root: Path, command: str, rc: int, *, cid: str | None = "toolu_1",
          session: str = AGENT, env: dict | None = None) -> dict | None:
    """post-tool with the payload the host sends: PostToolUse on success; on a non-zero
    exit PostToolUseFailure with `error` and no `tool_response` (verified shape)."""
    body = _payload(root, command, cid, session)
    if rc == 0:
        body |= {"hook_event_name": "PostToolUse",
                 "tool_response": {"stdout": "", "stderr": "", "interrupted": False}}
    else:
        body |= {"hook_event_name": "PostToolUseFailure", "error": f"Exit code {rc}",
                 "is_interrupt": False}
    r = subprocess.run([PY, "-m", "prusik", "gate", "post-tool"], cwd=root,
                       env=env or _env(root), input=json.dumps(body), capture_output=True,
                       text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None


def _call(root: Path, command: str, *, cid: str | None = "toolu_1", session: str = AGENT,
          env: dict | None = None, expect_rc: int = 0,
          checked: bool = True) -> tuple[str, dict | None]:
    """One Bash call through pre-tool → the command → post-tool. Returns the post-tool
    additionalContext ('' when silent) and the hook output object. `checked` asserts that
    pre-tool did (True) or did not (False) record this call's pre-call snapshot, so a
    silent result can't come from a check that never ran."""
    pre = _pre(root, command, cid=cid, session=session, env=env)
    assert not pre.stdout.strip(), f"pre-tool denied the call: {pre.stdout}"
    key = f"{session}-{cid or 'no-tool-use-id'}.json"
    assert (key in _pending(root)) is checked, (key, _pending(root))
    run = subprocess.run(["/bin/sh", "-c", command], cwd=root, env=env or _env(root),
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == expect_rc, (run.returncode, run.stderr)
    out = _post(root, command, run.returncode, cid=cid, session=session, env=env)
    return (out or {}).get("additionalContext", ""), out


def _heredoc(body: str) -> str:
    return f"{PY} - <<'EOF'\n{body}\nEOF"


def _write_cmd(rel: str, text: str = "pwned\\n") -> str:
    return _heredoc(f"open({rel!r}, 'w').write('{text}')")


def _lease(root: Path, *, session: str = HOLDER, age_min: float = 0,
           delegates: list[str] | None = None) -> None:
    now = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    lease = {"session": session, "feature": None, "phase": None,
             "acquired_at": now.isoformat(), "refreshed_at": now.isoformat()}
    if delegates:
        lease["delegates"] = delegates
    (root / ".sprint").mkdir(exist_ok=True)
    (root / ".sprint" / "main-writer.json").write_text(json.dumps(lease))


def _baseline(root: Path) -> dict:
    return json.loads((root / ".sprint" / "observed-writes" / "baseline.json").read_text())


def _reported(root: Path) -> list[dict]:
    return _events(root, "observed_write_reported")


# ---- happy path: the brief's adversarial set ------------------------------------------------

# moat-finding: fb-40a8a578d8e2 (a python heredoc in an inline-hosted sprint)
@pytest.mark.parametrize("shape", ["heredoc", "dash_c", "script_file"])
def test_unwritable_write_no_command_text_names_is_reported(building, tmp_path, shape):
    if shape == "heredoc":
        cmd = _write_cmd("README.md")
    elif shape == "dash_c":
        cmd = f"""{PY} -c "open('README.md','a').write('x')" """
    else:
        script = tmp_path / "w.py"                      # outside the project tree
        script.write_text("open('README.md', 'w').write('from a script\\n')\n")
        cmd = f"{PY} {script}"
    text, out = _call(building, cmd)
    assert out["hookEventName"] == "PostToolUse"
    assert text.startswith(PREFIX)
    assert "phase 'building' may not write" in text and "README.md" in text
    assert "git checkout -- <path>" in text                     # the way out is named
    ev = _reported(building)
    assert len(ev) == 1
    assert ev[0]["unwritable"] == ["README.md"] and ev[0]["phase"] == "building"
    assert ev[0]["session"] == AGENT and ev[0]["call_id"] == "toolu_1"
    assert ev[0]["orphan"] is False
    assert _pending(building) == []                             # paired and popped


def test_write_to_a_writable_path_is_silent(building):
    text, out = _call(building, _write_cmd("prusik/x.py"))
    assert out is None and text == ""
    assert _reported(building) == []


def test_already_dirty_file_left_untouched_is_not_reported(building):
    (building / "README.md").write_text("dirty before the call\n")
    text, _ = _call(building, _write_cmd("prusik/x.py"))
    assert "README.md" not in text
    assert _reported(building) == []


def test_already_dirty_file_rewritten_is_reported(building):
    (building / "README.md").write_text("dirty before the call\n")
    text, _ = _call(building, _write_cmd("README.md", "rewritten again\\n"))
    assert "README.md" in text and "phase 'building'" in text
    assert _reported(building)[0]["unwritable"] == ["README.md"]


def test_touch_without_content_change_is_not_reported(building):
    (building / "README.md").write_text("dirty before the call\n")
    text, out = _call(building, f"""{PY} -c "import os; os.utime('README.md', ns=(1, 10**18))" """)
    assert os.stat(building / "README.md").st_mtime_ns == 10**18     # the touch happened
    assert out is None and text == ""
    assert _reported(building) == []


def test_new_untracked_not_ignored_file_is_reported(building):
    text, _ = _call(building, _write_cmd("notes.txt"))
    assert "notes.txt" in text
    assert _reported(building)[0]["unwritable"] == ["notes.txt"]


def test_new_file_in_a_gitignored_dir_is_not_reported(building):
    text, out = _call(building, _heredoc("import os; os.makedirs('build', exist_ok=True); "
                                         "open('build/x', 'w').write('o')"))
    assert (building / "build" / "x").exists()
    assert out is None and text == ""


def test_new_file_in_a_gitignored_gated_dir_is_reported_fb_c1d2d8dcabac(building):
    # design/ is gitignored, but phases name it, so it is shared-tree content.
    text, _ = _call(building, _heredoc(
        "import os; os.makedirs('design/sprint-drive', exist_ok=True); "
        "open('design/sprint-drive/scope.md', 'w').write('x')"))
    assert "design/sprint-drive/scope.md" in text
    assert _reported(building)[0]["unwritable"] == ["design/sprint-drive/scope.md"]


def test_deleted_tracked_file_is_reported(building):
    text, _ = _call(building, f"""{PY} -c "import os; os.remove('docs.md')" """)
    assert not (building / "docs.md").exists()
    assert "docs.md" in text
    assert _reported(building)[0]["unwritable"] == ["docs.md"]


# moat-finding: fb-331f8487c548 (file verbs the text reader does not read)
# moat-finding: fb-a0dd3c924838 (a command whose first word expands to nothing)
# moat-finding: fb-9b48baaee117 (/usr/bin/time as a wrapper after a redirect or assignment)
_UNREAD_SHAPES = {
    "ln": "ln -s README.md README.lnk",
    "rsync": "rsync README.md README.copy",
    "tar_c": "tar cf out.tar README.md",
    "tar_x": "tar xf {tmp}/a.tar",
    "unzip": "unzip -oq {tmp}/a.zip",
    "patch": "patch -s -p0 -i {tmp}/new.diff",
    "curl_o": "curl -s -o curl.out file:///etc/hosts",
    "mkdir_then_file": "mkdir nd && cp README.md nd/f",
    "empty_subst": "$(:) rm README.md",
    "empty_subst_redirect": "$(:) 2>&1 rm README.md",
    "unset_var": "$prusik_unset_var rm README.md",
    "unset_var_redirect": "$prusik_unset_var > f rm README.md",
    "time_after_redirect": "> o time rm README.md",
    "time_after_assignment": "a=1 time rm README.md",
}


@pytest.mark.parametrize("shape", sorted(_UNREAD_SHAPES))
def test_write_the_text_reader_misses_is_denied_or_reported(building, tmp_path, shape):
    """The bash reader does not name these writes from the command text. The tree diff must
    still catch every one: the call is either refused before it runs or reported after.
    A silent pass is the bug. (An empty `mkdir`/`rmdir` changes no content and is not a
    write; a file put in the new dir is, shape mkdir_then_file.)"""
    import tarfile
    import zipfile
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "README.md").write_text("from tar\n")
    with tarfile.open(tmp_path / "a.tar", "w") as tf:
        tf.add(tmp_path / "x" / "README.md", arcname="README.md")
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as zf:
        zf.writestr("unzipped.txt", "from zip\n")
    (tmp_path / "new.diff").write_text(
        "--- /dev/null\n+++ patched.txt\n@@ -0,0 +1 @@\n+from patch\n")
    cmd = _UNREAD_SHAPES[shape].format(tmp=tmp_path)
    pre = _pre(building, cmd)
    if pre.stdout.strip():
        decision = json.loads(pre.stdout)["hookSpecificOutput"]["permissionDecision"]
        assert decision == "deny", pre.stdout
        return
    run = subprocess.run(["/bin/sh", "-c", cmd], cwd=building, env=_env(building),
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, (shape, run.stderr)
    out = _post(building, cmd, run.returncode)
    text = (out or {}).get("additionalContext", "")
    assert text.startswith(PREFIX), (shape, text)
    ev = _reported(building)
    assert len(ev) == 1 and ev[0]["unwritable"], (shape, ev)


def test_revert_is_not_damage(building):
    """Decision 13. The revert the message asks for must not itself be reported. The gate
    refuses `git checkout -- README.md` in building (so it leaves no pending snapshot);
    a revert the gate allows (a python restore of the original bytes) goes through pre and
    post and produces no unwritable report."""
    text, _ = _call(building, _write_cmd("README.md"))
    assert "README.md" in text
    refused = _pre(building, "git checkout -- README.md", cid="toolu_git")
    assert '"permissionDecision": "deny"' in refused.stdout
    assert _pending(building) == []                    # a refused call takes no snapshot
    text, out = _call(building, _write_cmd("README.md", "hello\\n"), cid="toolu_revert")
    assert (building / "README.md").read_text() == "hello\n"
    assert out is None and text == ""
    assert len(_reported(building)) == 1               # only the original write


# ---- write-then-fail (PC-4, DEV-005) ---------------------------------------------------------

_FAILING_WRITE = (f"""{PY} -c "open('{{}}','a').write('y'); raise SystemExit(3)" """)


def test_write_then_nonzero_exit_is_reported_on_that_call_sprint(building):
    text, out = _call(building, _FAILING_WRITE.format("README.md"), expect_rc=3)
    assert out["hookEventName"] == "PostToolUseFailure"
    assert "README.md" in text and "phase 'building' may not write" in text
    assert _reported(building)[0]["unwritable"] == ["README.md"]
    assert _pending(building) == []


def test_write_then_nonzero_exit_is_reported_on_that_call_lease(proj):
    _lease(proj)
    text, out = _call(proj, _FAILING_WRITE.format("prusik/x.py"), expect_rc=3)
    assert out["hookEventName"] == "PostToolUseFailure"
    assert f"session {HOLDER[:12]}… holds the single-writer lease" in text
    assert "prusik/x.py" in text
    ev = _reported(proj)
    assert ev[0]["holder"] == HOLDER and ev[0]["paths"] == ["prusik/x.py"]


def _age_pending(root: Path, seconds: float) -> None:
    for name in _pending(root):
        path = root / ".sprint" / "observed-writes" / "pending" / name
        rec = json.loads(path.read_text())
        rec["ts"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        path.write_text(json.dumps(rec))


def _orphan_after_s() -> int:
    from prusik import observed_writes
    return observed_writes.ORPHAN_AFTER_S


def test_orphan_sprint_case_is_reported_on_the_next_checked_call(building):
    cmd = _write_cmd("README.md")
    _pre(building, cmd, cid="toolu_orphan")
    subprocess.run(["/bin/sh", "-c", cmd], cwd=building, env=_env(building), check=True)
    # post-tool never runs for this call
    assert _pending(building) == [f"{AGENT}-toolu_orphan.json"]
    _age_pending(building, _orphan_after_s() + 1)
    text, _ = _call(building, "ls", cid="toolu_next")
    assert "the check for an earlier call" in text and "call toolu_orphan" in text
    assert "phase 'building' may not write" in text and "README.md" in text
    assert "Later calls' writes may be included." in text
    failed = [e for e in _events(building, "observed_write_check_failed")
              if e.get("stage") == "orphan"]
    assert len(failed) == 1
    rep = _reported(building)
    assert len(rep) == 1 and rep[0]["orphan"] is True and rep[0]["call_id"] == "toolu_orphan"
    assert _pending(building) == []                       # reported, then removed


def test_orphan_lease_case_is_reported_on_the_next_checked_call(proj):
    _lease(proj)
    cmd = _write_cmd("prusik/x.py")
    _pre(proj, cmd, cid="toolu_orphan")
    subprocess.run(["/bin/sh", "-c", cmd], cwd=proj, env=_env(proj), check=True)
    _age_pending(proj, _orphan_after_s() + 1)
    text, _ = _call(proj, "ls", cid="toolu_next")
    assert "call toolu_orphan" in text
    assert f"were written under session {HOLDER[:12]}…'s lease" in text
    assert "prusik/x.py" in text
    rep = _reported(proj)
    assert len(rep) == 1 and rep[0]["orphan"] is True and rep[0]["holder"] == HOLDER
    assert _pending(proj) == []


def test_young_pending_is_neither_deleted_nor_reported_by_another_call(building):
    cmd = _write_cmd("README.md")
    _pre(building, cmd, cid="toolu_inflight")
    subprocess.run(["/bin/sh", "-c", cmd], cwd=building, env=_env(building), check=True)
    text, out = _call(building, "ls", cid="toolu_other")
    assert out is None and text == ""
    assert _pending(building) == [f"{AGENT}-toolu_inflight.json"]   # still waiting to pair
    assert not [e for e in _events(building, "observed_write_check_failed")
                if e.get("stage") == "orphan"]
    # ...and when it does pair, its write is reported on its own call.
    out = _post(building, cmd, 0, cid="toolu_inflight")
    assert "README.md" in out["additionalContext"]


def test_no_code_path_sweeps_pending_files_silently():
    for mod in ("observed_writes", "gate"):
        tree = ast.parse((_REPO / "prusik" / f"{mod}.py").read_text())
        names = {n.name for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert not [n for n in names if "sweep" in n.lower()], (mod, names)


# ---- lease and the run condition ------------------------------------------------------------

def test_foreign_lease_reports_a_write_and_not_a_read(proj):
    _lease(proj)
    text, _ = _call(proj, _write_cmd("README.md"))
    assert f"session {HOLDER[:12]}…" in text and "README.md" in text
    assert len(_reported(proj)) == 1
    text, out = _call(proj, "ls", cid="toolu_ls")
    assert out is None and text == ""
    assert len(_reported(proj)) == 1


def test_delegate_of_the_holder_takes_no_snapshot(proj):
    _lease(proj, delegates=[AGENT])
    _pre(proj, _write_cmd("README.md"))
    assert _pending(proj) == []
    text, out = _call(proj, _write_cmd("README.md"), cid="toolu_2", checked=False)
    assert out is None and text == ""


@pytest.mark.parametrize("lease", ["none", "stale", "own"])
def test_no_sprint_and_no_foreign_lease_checks_nothing(proj, lease):
    if lease == "stale":
        _lease(proj, age_min=24 * 60)
    elif lease == "own":
        _lease(proj, session=AGENT)
    text, out = _call(proj, _write_cmd("README.md"), checked=False)
    assert out is None and text == ""
    assert not (proj / ".sprint" / "observed-writes").exists()
    assert _reported(proj) == []


_IMPORT_PROBE = r"""
import json, os, sys
from prusik.__main__ import main
sys.argv = ["prusik", "gate", os.environ["HOOK"]]
try:
    main()
except SystemExit:
    pass
json.dump(sorted(sys.modules), open(os.environ["OUT"], "w"))
"""


@pytest.mark.parametrize("lease", ["none", "stale", "own"])
def test_no_sprint_and_no_foreign_lease_never_imports_observed_writes(proj, tmp_path, lease):
    if lease == "stale":
        _lease(proj, age_min=24 * 60)
    elif lease == "own":
        _lease(proj, session=AGENT)
    for hook, event in (("pre-tool", "PreToolUse"), ("post-tool", "PostToolUse")):
        out = tmp_path / f"{hook}.json"
        body = {**_payload(proj, "ls", "toolu_1", AGENT), "hook_event_name": event,
                "tool_response": {"stdout": ""}}
        subprocess.run([PY, "-c", _IMPORT_PROBE], cwd=proj, input=json.dumps(body), text=True,
                       env=_env(proj, HOOK=hook, OUT=str(out)), check=True,
                       capture_output=True, timeout=120)
        mods = set(json.loads(out.read_text()))
        assert "prusik.gate" in mods                               # the hook really ran
        assert "prusik.observed_writes" not in mods, hook


def test_foreign_lease_reports_a_write_to_a_phase_writable_path_fb_16c859a406b9(building):
    _lease(building)
    text, _ = _call(building, _write_cmd("prusik/gate.py"))
    assert "prusik/gate.py" in text and "holds the single-writer lease" in text
    assert "may not write" not in text              # writable in building: lease reason only
    ev = _reported(building)[0]
    assert ev["paths"] == ["prusik/gate.py"] and ev["unwritable"] == []
    assert ev["holder"] == HOLDER


def test_accepted_miss_lease_write_to_always_writable_findings_is_not_reported(proj):
    """Decision 10, pinned on purpose: always-writable dirs are pruned from the walk, so a
    foreign-lease write to findings/ is NOT reported. Changing this is a design change."""
    _lease(proj)
    text, out = _call(proj, _heredoc("import os; os.makedirs('findings', exist_ok=True); "
                                     "open('findings/x.json', 'w').write('{}')"))
    assert (proj / "findings" / "x.json").exists()
    assert out is None and text == ""
    assert _reported(proj) == []


# ---- fails closed, visibly ------------------------------------------------------------------

def _failed(root: Path, stage: str) -> list[dict]:
    return [e for e in _events(root, "observed_write_check_failed") if e.get("stage") == stage]


def test_post_without_pre_snapshot_reports_could_not_run(building):
    subprocess.run(["/bin/sh", "-c", _write_cmd("README.md")], cwd=building, check=True,
                   env=_env(building))
    out = _post(building, "x", 0, cid="toolu_unpaired")
    text = out["additionalContext"]
    assert text.startswith(f"{PREFIX} the change check could not run for this call")
    assert "no pre-call snapshot" in text
    assert "leaving the phase re-checks the tree" in text
    assert [e["reason"] for e in _failed(building, "post")] == ["no_pre_snapshot"]


def test_corrupt_pending_reports_could_not_run(building):
    _pre(building, "ls")
    [name] = _pending(building)
    (building / ".sprint" / "observed-writes" / "pending" / name).write_text("{not json")
    out = _post(building, "ls", 0)
    assert "could not run" in out["additionalContext"]
    assert "pending snapshot unreadable" in out["additionalContext"]
    assert _failed(building, "post")[0]["reason"] == "pending snapshot unreadable"
    assert _pending(building) == []


def test_broken_git_check_ignore_reports_could_not_run_never_passes(building, tmp_path):
    fake = tmp_path / "fakebin"
    fake.mkdir()
    git = fake / "git"
    git.write_text("#!/bin/sh\necho 'fatal: broken' >&2\nexit 128\n")
    git.chmod(git.stat().st_mode | stat.S_IEXEC)
    env = _env(building, PATH=f"{fake}{os.pathsep}{os.environ['PATH']}")
    text, _ = _call(building, _write_cmd("README.md"), env=env)
    assert "could not run" in text and "git check-ignore failed" in text
    assert "rc 128" in text
    assert _reported(building) == []                   # not a pass, not a report: a failure
    assert _failed(building, "post")


def test_payload_without_tool_use_id_reports_could_not_run(building):
    text, _ = _call(building, _write_cmd("README.md"), cid=None)
    assert "could not run" in text and "no tool-use id in the hook payload" in text
    assert _failed(building, "pre")[0]["reason"] == "no tool-use id in the hook payload"


_DEADLINE_ZERO = r"""
import os, sys
from prusik import observed_writes
observed_writes.HOOK_SNAPSHOT_DEADLINE_S = 0
from prusik.__main__ import main
sys.argv = ["prusik", "gate", os.environ["HOOK"]]
sys.exit(main())
"""


def test_deadline_overrun_reports_could_not_run_and_keeps_progress(building):
    hashes = building / ".sprint" / "observed-writes" / "hashes.json"
    hashes.unlink()                                                   # cold tree
    body = _payload(building, "ls", "toolu_1", AGENT)
    r = subprocess.run([PY, "-c", _DEADLINE_ZERO], cwd=building, text=True,
                       input=json.dumps({**body, "hook_event_name": "PreToolUse"}),
                       env=_env(building, HOOK="pre-tool"), capture_output=True, timeout=120)
    assert r.returncode == 0 and not r.stdout.strip(), r.stdout + r.stderr
    [name] = _pending(building)
    rec = json.loads((building / ".sprint/observed-writes/pending" / name).read_text())
    assert "exceeded 0" in rec["error"]
    assert "exceeded" in _failed(building, "pre")[0]["reason"]
    out = _post(building, "ls", 0)
    assert "could not run" in out["additionalContext"]
    assert "exceeded" in out["additionalContext"]
    assert json.loads(hashes.read_text())                            # progress was kept


# ---- advance -------------------------------------------------------------------------------

def _entry_ts(root: Path) -> str:
    entries = [e for e in _events(root)
               if e.get("event") in ("sprint_started", "phase_advance", "phase_rewind")
               and e.get("feature") == F]
    return entries[-1]["ts"]


def _blocked(root: Path) -> list[dict]:
    return [e for e in _events(root, "advance_blocked")
            if e.get("gate_class") == "observed_write"]


def test_baseline_is_stamped_with_each_entry_event_and_first_advance_refuses_with_paths(proj):
    """PC-3, adversarial: a normal sprint never retakes its baseline, and the first advance
    after a stray write refuses with the path, not with a baseline reason."""
    assert _gate(proj, "sprint-start", F).returncode == 0
    b = _baseline(proj)
    assert (b["feature"], b["phase"], b["entered_at"]) == (F, "scoping", _entry_ts(proj))
    for phase in ("planning", "building"):
        r = _gate(proj, "advance", phase, "--feature", F)
        assert r.returncode == 0, r.stderr
        b = _baseline(proj)
        assert (b["phase"], b["entered_at"]) == (phase, _entry_ts(proj))
    assert _events(proj, "phase_baseline_retaken") == []

    _call(proj, _write_cmd("README.md"))
    r = _gate(proj, "advance", "reviewing", "--feature", F)
    assert r.returncode == 2
    assert "BLOCKED leaving 'building'" in r.stderr and "README.md" in r.stderr
    assert "git stash push -- <path>" in r.stderr                  # move-aside way out
    [blk] = _blocked(proj)
    assert blk["paths"] == ["README.md"] and "reason" not in blk
    assert (blk["from_phase"], blk["to_phase"]) == ("building", "reviewing")
    assert _events(proj, "phase_baseline_retaken") == []
    st = json.loads((proj / ".sprint" / "state.json").read_text())
    assert st["phase"] == "building"                               # a refusal moves nothing

    _git(proj, "checkout", "--", "README.md")                      # operator reverts
    r = _gate(proj, "advance", "reviewing", "--feature", F)
    assert r.returncode == 0, r.stderr


def test_dirty_at_entry_untouched_passes_and_rewritten_refuses(proj):
    (proj / "README.md").write_text("dirty before the sprint\n")
    _start(proj, "building")                                       # passes every phase
    assert _blocked(proj) == []
    _call(proj, _write_cmd("README.md", "rewritten in building\\n"))
    r = _gate(proj, "advance", "reviewing", "--feature", F)
    assert r.returncode == 2 and _blocked(proj)[0]["paths"] == ["README.md"]


def test_rewind_is_refused_after_an_unwritable_write(proj):
    _start(proj, "reviewing")
    text, _ = _call(proj, _write_cmd("prusik/x.py"))
    assert "phase 'reviewing' may not write" in text and "prusik/x.py" in text
    r = _gate(proj, "advance", "building", "--feature", F, "--allow-rewind")
    assert r.returncode == 2, r.stdout + r.stderr
    [blk] = _blocked(proj)
    assert blk["paths"] == ["prusik/x.py"]
    assert (blk["from_phase"], blk["to_phase"]) == ("reviewing", "building")
    assert json.loads((proj / ".sprint" / "state.json").read_text())["phase"] == "reviewing"


@pytest.mark.parametrize("damage,reason", [("missing", "missing"),
                                           ("corrupt", "unreadable")])
def test_missing_or_corrupt_baseline_refuses_once_then_passes(building, damage, reason):
    path = building / ".sprint" / "observed-writes" / "baseline.json"
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("{corrupt")
    r = _gate(building, "advance", "reviewing", "--feature", F)
    assert r.returncode == 2
    assert "re-run the advance" in r.stderr
    retaken = _events(building, "phase_baseline_retaken")
    assert [(e["phase"], e["reason"]) for e in retaken] == [("building", reason)]
    assert _blocked(building)[0]["reason"] == f"phase-entry baseline {reason}"
    assert _baseline(building)["entered_at"] == _entry_ts(building)   # same entry, fresh
    r = _gate(building, "advance", "reviewing", "--feature", F)
    assert r.returncode == 0, r.stderr
    assert len(_events(building, "phase_baseline_retaken")) == 1


_FAILING_BASELINE_WRITE = r"""
import os, sys
from prusik import observed_writes
def boom(*a, **k):
    observed_writes.clear_baseline(a[0])
    raise OSError("disk full (test)")
observed_writes.write_baseline = boom
from prusik.__main__ import main
sys.argv = ["prusik", "gate", "advance", "reviewing", "--feature", "feat"]
sys.exit(main())
"""


def test_failed_baseline_write_leaves_no_stale_baseline_and_next_advance_refuses(building):
    path = building / ".sprint" / "observed-writes" / "baseline.json"
    assert _baseline(building)["phase"] == "building"
    r = subprocess.run([PY, "-c", _FAILING_BASELINE_WRITE], cwd=building, env=_env(building),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr                         # the advance itself happened
    assert json.loads((building / ".sprint/state.json").read_text())["phase"] == "reviewing"
    assert not path.exists()                    # never the previous entry's valid-looking one
    assert "baseline for 'reviewing' was not taken" in r.stderr
    assert _failed(building, "baseline")
    r = _gate(building, "advance", "integrating", "--feature", F)
    assert r.returncode == 2
    assert [e["reason"] for e in _events(building, "phase_baseline_retaken")] == ["missing"]


def test_sprint_start_writes_the_scoping_baseline(proj):
    assert not (proj / ".sprint" / "observed-writes" / "baseline.json").exists()
    assert _gate(proj, "sprint-start", F).returncode == 0
    b = _baseline(proj)
    assert (b["feature"], b["phase"]) == (F, "scoping")
    assert {"README.md", "docs.md", "prusik/x.py"} <= set(b["snapshot"])
    assert not [p for p in b["snapshot"] if p.startswith((".sprint/", ".git/"))]


# ---- fix-round, through the real command path (PC-9) -----------------------------------------

def _fix_round_project(proj: Path) -> Path:
    """reviewing with a baseline; root prusik/x.py is modified (not pristine) and a partial
    mirror worktree holds a different version of it."""
    _start(proj, "building")
    (proj / "prusik" / "x.py").write_text("x = 'built'\n")          # building may write it
    wt = proj / "worktrees" / "backend-builder" / "prusik"
    wt.mkdir(parents=True)
    (wt / "x.py").write_text("x = 'built'\n")
    assert _gate(proj, "advance", "reviewing", "--feature", F).returncode == 0
    r = _gate(proj, "fix-round", "start", "--feature", F)
    assert r.returncode == 0, r.stdout + r.stderr
    (wt / "x.py").write_text("x = 'fixed'\n")                       # the fix
    return proj


_END = f"{PY} -m prusik gate fix-round end --feature {F}"


def test_fix_round_end_assembly_is_not_reported_and_advance_passes(proj):
    root = _fix_round_project(proj)
    text, _ = _call(root, _END, cid="toolu_end")
    assert (root / "prusik" / "x.py").read_text() == "x = 'fixed'\n"   # really assembled
    assert "prusik/x.py" not in text
    assert _events(root, "worktrees_assembled")
    assert _reported(root) == []
    r = _gate(root, "advance", "integrating", "--feature", F)
    assert r.returncode == 0, r.stderr


def test_fix_round_end_folds_only_the_assembled_path(proj):
    """Adversarial preserve-check: the agent's own root write in the same call is NOT
    folded, so it is still reported and still refuses the advance."""
    root = _fix_round_project(proj)
    cmd = f"{_write_cmd('README.md')}\n{_END}"
    text, _ = _call(root, cmd, cid="toolu_end")
    assert (root / "prusik" / "x.py").read_text() == "x = 'fixed'\n"
    assert "README.md" in text and "phase 'reviewing' may not write" in text
    assert "prusik/x.py" not in text
    assert _reported(root)[0]["unwritable"] == ["README.md"]
    r = _gate(root, "advance", "integrating", "--feature", F)
    assert r.returncode == 2
    assert _blocked(root)[0]["paths"] == ["README.md"]


def test_fix_round_end_without_baseline_assembles_unchanged_and_folds_nothing(proj):
    root = _fix_round_project(proj)
    base = root / ".sprint" / "observed-writes" / "baseline.json"
    base.unlink()
    r = _gate(root, "fix-round", "end", "--feature", F)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "re-assembled 1 worktree file(s)" in r.stdout
    assert (root / "prusik" / "x.py").read_text() == "x = 'fixed'\n"
    [ev] = _events(root, "worktrees_assembled")
    assert ev["files"] == 1
    assert not base.exists()                                  # the fold created nothing
    assert _failed(root, "fold") == []


# ---- prusik feedback regression ---------------------------------------------------------------

def test_prusik_feedback_in_building_is_not_reported(building):
    cmd = (f'{PY} -m prusik feedback "observed writes flagged my feedback call" --kind bug '
           f'--detail "repro in building"')
    text, out = _call(building, cmd)
    assert list((building / "findings").rglob("*")), "feedback wrote nothing; test is vacuous"
    assert out is None and text == ""
    assert _reported(building) == []


def test_findings_path_is_writable_in_every_phase(building):
    from prusik import phases
    cfg = phases.load_sprint_config(building)
    for phase in ("scoping", "planning", "building", "reviewing"):
        assert phases.is_path_writable("findings/fb-1.json", cfg, phase, F, building) \
            == (True, None), phase
