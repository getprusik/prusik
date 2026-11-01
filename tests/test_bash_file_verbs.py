"""The gate reads cp, mv, rm, truncate, dd, touch and install. Real tools are the oracle: a write
case must change the file on disk before the gate is held to naming it.

moat-finding: fb-f113f05860db
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from prusik import gate, phases
from prusik.platform_adapter import ClaudeCodeAdapter, ToolEvent

_GNU_CP = subprocess.run(["cp", "--version"], capture_output=True, check=False).returncode == 0
_HAS_TRUNCATE = shutil.which("truncate") is not None


def _targets(cmd, start=""):
    return gate._bash_write_targets(gate._strip_heredocs(cmd), start)


def _snapshot(root):
    """{relative path: (contents, mtime)} for every file under `root`. `touch` moves only the
    mtime."""
    found = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                found[os.path.relpath(path, root)] = (f.read(), os.stat(path).st_mtime_ns)
    return found


def _changed(before, after):
    return {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}


@pytest.fixture
def box(tmp_path):
    (tmp_path / "src").write_text("source\n")
    (tmp_path / "src2").write_text("source two\n")
    (tmp_path / "old").write_text("old\n")
    (tmp_path / "ref").write_text("reference\n")
    os.utime(tmp_path / "old", (1_000_000, 1_000_000))
    (tmp_path / "d").mkdir()
    return tmp_path


# ---- each case runs for real: exactly the files it changed are the ones named -------------

_WRITES = [
    "cp src new",
    "cp -p src new",
    "cp -- src new",
    "cp src d/",
    "cp src src2 d/",
    "cp src src2 d",
    "cd d && cp ../src .",
    "cd d && cp ../src new",
    "cp src new 2>/dev/null",
    "cp src new && echo done",
    "/bin/cp src new",
    "FOO=1 cp src new",
    "mv old new",
    "mv old d/",
    "rm old",
    "rm -f old src",
    "rm -- old",
    "cd d && rm ../old",
    "if true; then rm old; fi",
    "touch new",
    "touch -c old new",
    "touch -r ref old",
    "dd if=src of=new",
    "dd if=src of=d/new bs=1",
]


def _named(cmd):
    return {os.path.normpath(t) for t in _targets(cmd)}


@pytest.mark.parametrize("cmd", _WRITES)
def test_real_write_is_named_exactly(box, cmd):
    before = _snapshot(box)
    subprocess.run(["/bin/bash", "-c", cmd], cwd=box, capture_output=True, check=False)
    changed = _changed(before, _snapshot(box))
    assert changed, f"the case changed nothing, so it proves nothing: {cmd}"
    named = _named(cmd)
    # Naming a file the tool skipped (`touch -c`) is allowed. Missing one is not.
    assert changed <= named, f"{cmd}: changed {changed}, named {named}"


@pytest.mark.skipif(not _HAS_TRUNCATE, reason="no truncate on this host")
@pytest.mark.parametrize("cmd", ["truncate -s 0 old", "truncate -s0 old", "truncate -r ref old"])
def test_real_truncate_is_named(box, cmd):
    before = _snapshot(box)
    subprocess.run(["/bin/bash", "-c", cmd], cwd=box, capture_output=True, check=False)
    assert _changed(before, _snapshot(box)) == {"old"}
    assert _targets(cmd) == ["old"]


def test_real_install_is_named(box):
    cmd = "install -m 644 src d/new"
    before = _snapshot(box)
    subprocess.run(["/bin/bash", "-c", cmd], cwd=box, capture_output=True, check=False)
    assert _changed(before, _snapshot(box)) == {"d/new"}
    assert _targets(cmd) == ["d/new"]


def test_real_install_d_names_the_directory_it_creates(box):
    subprocess.run(["/bin/bash", "-c", "install -d -m 755 made/here"], cwd=box, check=True)
    assert (box / "made" / "here").is_dir()
    assert _targets("install -d -m 755 made/here") == ["made/here"]


@pytest.mark.skipif(not _GNU_CP, reason="GNU-only flag")
@pytest.mark.parametrize("cmd", ["cp -t d src", "mv -t d old", "cp --target-directory=d src"])
def test_real_gnu_target_directory(box, cmd):
    before = _snapshot(box)
    subprocess.run(["/bin/bash", "-c", cmd], cwd=box, check=True)
    assert _changed(before, _snapshot(box)) <= {os.path.normpath(t) for t in _targets(cmd)}


@pytest.mark.parametrize("cmd,expected", [
    ("cp -t d a b", ["d/a", "d/b"]),
    ("cp --target-directory d a", ["d/a"]),
    ("cp --target-directory=d a", ["d/a"]),
    ("mv -t d a", ["a", "d/a"]),
    ("touch --reference=ref t", ["t"]),
    ("touch --reference ref t", ["t"]),
    ("truncate --size 0 t", ["t"]),
    ("install --mode=644 a b", ["b"]),
    ("install --directory d", ["d"]),
])
def test_gnu_forms_read_by_the_parser_on_any_host(cmd, expected):
    assert _targets(cmd) == expected


def test_destination_directory_lands_as_dir_slash_basename():
    assert _targets("cp src/a.py design/") == ["design/a.py"]
    assert _targets("cp x/a x/b design") == ["design/a", "design/b"]
    assert _targets("cp -r dir/ design/") == ["design/dir"]
    assert _targets("cp src/a .", "worktrees/solo") == ["worktrees/solo/a"]


# ---- what must never be named ------------------------------------------------------------

@pytest.mark.parametrize("cmd,must_not_name", [
    ("cp src new", "src"),
    ("cp -r src1 src2 d/", "src1"),
    ("install src new", "src"),
    ("touch -r ref t", "ref"),
    ("touch --reference=ref t", "ref"),
    ("truncate -r ref t", "ref"),
    ("dd if=src of=t", "src"),
    ("touch -t 202601010000 t", "202601010000"),
    ("install -m 644 -o root a b", "644"),
    ("install -m 644 -o root a b", "root"),
    ("truncate -s 0 t", "0"),
])
def test_read_only_operand_is_not_named(cmd, must_not_name):
    assert must_not_name not in _targets(cmd)


@pytest.mark.parametrize("cmd", [
    "git rm a.py",
    "git mv a.py b.py",
    "docker cp a b",
    "kubectl cp a b",
    "pip install requests",
    "uv pip install requests",
    "npm install left-pad",
    "npm rm left-pad",
    "brew install jq",
    "make install",
    "echo rm a.py",
    "echo cp a b",
    "grep rm a.py",
    "ls cp rm mv",
    "man dd",
    "cp onlyone",
    "mv onlyone",
    "dd if=a",
    "echo 'rm a.py'",
    'echo "cp a b"',
    "# rm a.py",
    "echo hi # cp a b",
    "my-rm a.py",
    "rm.sh a.py",
    "python -c 'rm a'",
])
def test_word_that_is_not_the_verb_names_nothing(cmd):
    assert _targets(cmd) == []


# ---- regenerable directories --------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "rm -rf .pytest_cache .ruff_cache .mypy_cache",
    "rm -rf worktrees/solo/.pytest_cache",
    "rm -rf a/b/__pycache__",
    "rm -rf node_modules dist",
    "rm -rf .venv venv",
    "rm -f pkg/__pycache__/x.pyc",
    "cd worktrees/solo && rm -rf .venv",
])
def test_regenerable_directories_can_be_cleared(cmd):
    assert _targets(cmd) == []


@pytest.mark.parametrize("cmd,expected", [
    # The adversarial side: the exemption reads the normalized path, never the text.
    ("rm .pytest_cache/../design/f/scope.md", ["design/f/scope.md"]),
    ("rm __pycache__/../../x", ["../x"]),
    ("rm design/f/scope.md .pytest_cache", ["design/f/scope.md"]),
    ("cp x .pytest_cache/../design/f/scope.md", ["design/f/scope.md"]),
    ("cp x .pytest_cache/y", [".pytest_cache/y"]),         # only rm is exempt
    ("rm my.venv", ["my.venv"]),
    ("rm cachedist/a", ["cachedist/a"]),
])
def test_exemption_never_widens(cmd, expected):
    assert _targets(cmd) == expected


def test_unresolved_path_is_never_exempt():
    assert _targets("rm $X/.pytest_cache/../../a") != []


def test_shared_exemption_set_is_built_on_the_derived_dirs():
    assert gate._DERIVED_DIRS <= gate._REGENERABLE_DIRS
    assert {".venv", "venv"} <= gate._REGENERABLE_DIRS


# ---- literal assignments ------------------------------------------------------------------

@pytest.mark.parametrize("cmd,expected", [
    ('S=/tmp/x; cp a "$S/b"', ["/tmp/x/b"]),
    ('S=/tmp/x; cp a "${S}/b"', ["/tmp/x/b"]),
    ('S=/tmp/x; echo hi > "$S/log"', ["/tmp/x/log"]),
    ('S=/tmp/x; echo hi | tee "$S/log"', ["/tmp/x/log"]),
    ('S=/tmp/x; sed -i.bak s/a/b/ "$S/f"', ["/tmp/x/f"]),
    ('W=/tmp/w; cd "$W" && rm f', ["/tmp/w/f"]),
    ('A=/tmp; B=$A/sub; rm "$B/f"', ["/tmp/sub/f"]),
    ('export S=/tmp/x; rm "$S/f"', ["/tmp/x/f"]),
    ('S=/tmp/x\nrm "$S/f"', ["/tmp/x/f"]),
    ('S=/tmp/x && rm "$S/f"', ["/tmp/x/f"]),
    ('C="/tmp/with space"; rm "$C/f"', ["/tmp/with space/f"]),
])
def test_literal_assignment_resolves(cmd, expected):
    assert list(dict.fromkeys(_targets(cmd))) == expected      # a quoted redirect names its head too


def test_home_resolves_as_tilde_does():
    assert _targets('rm "$HOME/a"') == [os.path.expanduser("~/a")]
    assert _targets('rm "${HOME}/a"') == [os.path.expanduser("~/a")]


@pytest.mark.parametrize("cmd", [
    "S=/tmp/x; S=design; rm $S/f",            # assigned twice
    "rm $S/f; S=/tmp/x",                      # assigned after the use
    "S=/tmp/x; S+=y; rm $S/f",
    "for S in /tmp/x design; do rm $S/f; done",
    "for S in design; do rm $S/f; done; S=/tmp/x",
    "S=/tmp/x; read S; rm $S/f",
    "S=/tmp/x; read -r T S; rm $S/f",
    "S=/tmp/x; unset S; rm $S/f",
    "S=/tmp/x; printf -v S design; rm $S/f",
    "S=$(mktemp -d); rm $S/f",                # a command substitution
    "S=`mktemp -d`; rm $S/f",
    "(S=/tmp/x; true); rm $S/f",              # a subshell's assignment dies with it
    "S=/tmp/x true; rm $S/f",                 # a prefix never reaches the next statement
    "S=/tmp/x rm $S/f",
    "S=/tmp/x; rm $UNKNOWN/f",
    "echo S=/tmp/x; rm $S/f",                 # an argument, not an assignment
    "echo 'S=/tmp/x'; rm $S/f",
    "X=$X/b; rm $X/f",                        # reads its own old value
])
def test_unresolved_name_fails_closed(cmd):
    targets = _targets(cmd)
    assert targets and all(gate._is_unresolved(t) for t in targets), targets


def test_resolved_value_is_the_one_real_bash_uses(box):
    (box / "real").mkdir()
    cmd = f'S={box}/real; touch "$S/made"'
    subprocess.run(["/bin/bash", "-c", cmd], check=True)
    assert (box / "real" / "made").exists()
    assert _targets(cmd) == [f"{box}/real/made"]


# ---- the shell's starting directory -------------------------------------------------------

@pytest.mark.parametrize("cmd,start,expected", [
    ("rm f", "worktrees/solo", ["worktrees/solo/f"]),
    ("echo hi > f", "worktrees/solo", ["worktrees/solo/f"]),
    ("sed -i.bak s/a/b/ f", "worktrees/solo", ["worktrees/solo/f"]),
    ("cd .. && rm f", "worktrees/solo", ["worktrees/f"]),
    ("cd ../.. && rm f", "worktrees/solo", ["f"]),
    ("rm /tmp/f", "worktrees/solo", ["/tmp/f"]),
    ("(cd d; rm a); rm b", "worktrees/solo", ["worktrees/solo/d/a", "worktrees/solo/b"]),
    ("rm f", "/elsewhere", ["/elsewhere/f"]),
    ("rm f", "", ["f"]),
])
def test_relative_path_starts_from_the_shell(cmd, start, expected):
    assert _targets(cmd, start) == expected


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / ".sprint").mkdir()
    (tmp_path / ".claude").mkdir()
    (tmp_path / "worktrees" / "solo").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def test_shell_start_is_relative_inside_the_project(project):
    assert gate._shell_start(str(project / "worktrees" / "solo")) == "worktrees/solo"
    assert gate._shell_start(str(project)) == ""
    assert gate._shell_start(None) == ""
    assert gate._shell_start("") == ""


def test_shell_start_stays_absolute_outside_the_project(project, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    assert gate._shell_start(str(outside)) == str(outside.resolve())


_CFG = {"phases": [{"name": "reviewing", "writable": ["reports/**"]},
                   {"name": "solo_execute", "writable": ["worktrees/solo/**"]}]}


def _decide(cmd, phase, cwd=None):
    event = ToolEvent(tool="Bash", command=cmd, cwd=cwd)
    return gate._gate_decision(event, _CFG, phase, "f")


def test_gate_refuses_the_field_bypass(project):
    cmd = "cp worktrees/solo/src/a.py src/a.py"
    assert "src/a.py" in _decide(cmd, "solo_execute", str(project))


def test_gate_allows_a_write_the_shell_makes_inside_the_worktree(project):
    wt = str(project / "worktrees" / "solo")
    assert _decide("rm src/a.py", "solo_execute", wt) is None
    assert _decide("rm src/a.py", "solo_execute", str(project)) is not None


def test_gate_still_refuses_climbing_out_of_the_worktree(project):
    wt = str(project / "worktrees" / "solo")
    assert _decide("rm ../../src/a.py", "solo_execute", wt) is not None


def test_gate_allows_cache_cleanup_in_a_read_only_phase(project):
    assert _decide("rm -rf worktrees/solo/.pytest_cache", "reviewing", str(project)) is None


def test_gate_says_an_unresolved_file_verb_target_cannot_be_judged(project):
    reason = _decide('for f in a b; do rm "$f"; done', "reviewing", str(project))
    assert "can't tell where it lands" in reason


def test_refusal_keeps_the_reason_doctor_parses(project):
    from prusik import ledger
    _decide("rm src/a.py", "reviewing", str(project))
    records = [r for r in ledger.read_all() if r.get("event") == "gate_blocked"]
    assert records[-1]["reason"].startswith("bash redirect to unwriteable path: src/a.py")


def test_writable_set_is_what_judges_the_landed_path(project):
    ok, _ = phases.is_path_writable("worktrees/solo/x", _CFG, "solo_execute", "f", root=project)
    assert ok


def test_adapter_carries_the_host_cwd():
    event = ClaudeCodeAdapter().parse_event(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": "/a/b"})
    assert event.cwd == "/a/b"


def test_adapter_without_a_cwd_leaves_it_none():
    event = ClaudeCodeAdapter().parse_event({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert event.cwd is None


def test_shared_tree_check_starts_from_the_shell(project):
    wt = str(project / "worktrees" / "solo")
    assert not gate._mutates_shared_tree(ToolEvent(tool="Bash", command="rm a", cwd=wt))
    assert gate._mutates_shared_tree(ToolEvent(tool="Bash", command="rm a", cwd=str(project)))
    assert not gate._mutates_shared_tree(ToolEvent(tool="Bash", command="git commit -m x", cwd=wt))
    assert gate._mutates_shared_tree(
        ToolEvent(tool="Bash", command="git commit -m x", cwd=str(project)))


# ---- cost ---------------------------------------------------------------------------------

def _no_scan(*_a, **_k):
    raise AssertionError("the scan ran")


@pytest.mark.parametrize("cmd", ["ls -la", "git status", "pytest -q tests/test_x.py", "pwd"])
def test_command_naming_no_writer_returns_before_any_scan(cmd, monkeypatch):
    monkeypatch.setattr(gate, "_literal_text_mask", _no_scan)
    monkeypatch.setattr(gate, "_shell_start", _no_scan)
    assert gate._bash_write_targets(cmd) == []
    assert gate._event_write_targets(ToolEvent(tool="Bash", command=cmd, cwd="/a"), cmd) == []
