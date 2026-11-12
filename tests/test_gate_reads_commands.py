"""`deny_commands` and the writer lease read a command wherever bash runs it, and read none in
quoted text. Real bash is the oracle: a stub `git` first on PATH logs what ran, and the gate must
agree with the log in both directions.

moat-finding: fb-7610468920d4
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from prusik import gate, phases
from prusik.platform_adapter import ToolEvent

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "prusik" / "templates" / ".claude" / "sprint-config.yaml"

_STUB = """#!/bin/bash
while [ "${1#-}" != "$1" ]; do
  case "$1" in
    -C) cd "$2" || exit 1; shift 2;;
    -c|--git-dir|--work-tree|--attr-source) shift 2;;
    *) shift;;
  esac
done
echo "$(pwd -P) $1" >> "$GIT_LOG"
"""


@pytest.fixture
def shell(tmp_path):
    """Runs a command in real bash in a project with a worktree, and returns [(directory, git
    subcommand)] for each time the stub git ran."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    stub = stubs / "git"
    stub.write_text(_STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    project = tmp_path / "p"
    (project / "worktrees" / "solo").mkdir(parents=True)
    log = tmp_path / "log"
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}", "GIT_LOG": str(log)}

    def run(cmd: str) -> list[tuple[str, str]]:
        log.write_text("")
        subprocess.run(["bash", "-c", cmd.replace("{stubs}", str(stubs))], cwd=project, env=env,
                       capture_output=True, check=False, timeout=10)
        return [tuple(line.rsplit(" ", 1)) for line in log.read_text().splitlines()]

    return run


@pytest.mark.parametrize("cmd", [
    "git push", "git push origin main", "(git push)", "if git push; then :; fi",
    ">o git push", "2>/dev/null git push", "X=1 git push", "! git push", "{ git push; }",
    "case x in x) git push;; esac", "git status & git push", "git status && git push",
    "false || git push", "true | git push", "echo $(git push)", 'echo "$(git push)"',
    "echo `git push`", 'echo "`git push`"', "cat <(git push)", "for i in 1; do git push; done",
    "while git push; do break; done", "if false; then :; else git push; fi",
    "G=git; $G push", 'G=git; "$G" push', "G=push; git $G", "'git' push", '"git" push', "gi\\t push",
    "git 'push'", "git -C . push", "git -c a=b push", "git --git-dir x push",
    "git --work-tree=x push", "git -C . -c a=b push", "{stubs}/git push", "git >o push",
    "bash -c 'git push'", "sh -c 'true; git push'", "sh -ec 'git push'", 'eval "git push"',
    "eval git push", "trap 'git push' EXIT", "bash -c 'bash -c \"git push\"'",
    "echo `echo \\`git push\\``", "echo $(echo $(git push))", 'bash -c "echo \\$(git push)"',
    "echo \"`true`\"; echo 'a`b'; echo `git push`", "echo $'x'; echo 'a`b'; echo `git push`",
    "echo ${x:-'a'}; echo 'a`b'; echo `git push`", "echo \"`true`\" '`' ; echo `git push`",
    "echo $'a\\'`'; echo `git push`", "echo \"$(echo '`')\"; echo `git push`",
    "echo \"it's\"; echo `git push`", "# don't\necho `git push`",
    "echo \\\\`git push`", "echo \\\\\\\\`git push`",
    "bash -c -x 'git push'", "bash -c -- 'git push'", "eval -- git push",
    "trap -- 'git push' EXIT", "git --attr-source x push",
])
def test_a_push_bash_runs_is_denied_and_mutates(shell, cmd):
    ran = shell(cmd)
    assert "push" in [sub for _dir, sub in ran], "bash ran no push, so this case proves nothing"
    assert gate._command_denied(cmd, "git push")
    assert gate._shared_tree_git_mutation(cmd)


@pytest.mark.parametrize("cmd", [
    "echo 'a; git push b'", 'echo "x && git push origin"', "git log --grep 'x; git push y'",
    "git status # git push", "# git push", "echo git push", "echo \\; git push",
    "printf '%s\\n' '(git push)'", "echo \\& git push", "echo \\| git push",
    "cat <<'EOF'\ngit push\nEOF", "git stash-push", "gitx push", "git pushx",
    "echo 'bash -c git push'", "python3 -c 'print(1); git push'",
    "[[ a == 'git push' ]]", "echo $((1 + 2)) 'x; git push y'", "bash script.sh",
    "echo '`git push`'", "echo \\`git push\\`", "echo 'a`'; echo 'git push`'",
    "git status # `git push`",
])
def test_text_bash_does_not_run_is_not_a_push(shell, cmd):
    assert "push" not in [sub for _dir, sub in shell(cmd)]
    assert not gate._command_denied(cmd, "git push")
    assert not gate._shared_tree_git_mutation(cmd)


def test_a_commit_message_is_not_a_push_but_the_commit_mutates(shell):
    cmd = 'git commit -m "a; git push b"'
    assert [sub for _dir, sub in shell(cmd)] == ["commit"]
    assert not gate._command_denied(cmd, "git push")
    assert gate._command_denied(cmd, "git commit")
    assert gate._shared_tree_git_mutation(cmd)


@pytest.mark.parametrize("cmd", [
    "git status", "git -C worktrees/solo status", "(git log)", "git diff; git show",
])
def test_a_reading_git_command_never_mutates(shell, cmd):
    assert shell(cmd)
    assert not gate._shared_tree_git_mutation(cmd)


@pytest.mark.parametrize("cmd", [
    "cd worktrees/solo && git commit", "(cd worktrees/solo && git commit)",
    "git -C worktrees/solo commit", "(cd worktrees/solo; git commit)",
    "cd worktrees && git -C solo commit", "git -C worktrees -C solo commit",
    "D=worktrees/solo; git -C $D commit", "D=worktrees/solo; cd $D; git commit",
    "cd worktrees/solo; (git commit)", "(cd worktrees/solo; echo `git commit`)",
    "cd worktrees/solo; bash -c 'git commit'",
    "(cd worktrees/solo); git commit", "cd worktrees/solo && git -C ../.. commit",
    "git -C worktrees/solo -C ../.. commit", "cd worktrees/solo; cd ../..; git commit",
    "git -C worktrees/solo commit; git commit", "git commit; cd worktrees/solo",
    "pushd worktrees/solo >/dev/null; popd >/dev/null; git commit",
])
def test_the_lease_agrees_with_where_bash_ran_git(shell, cmd):
    ran = shell(cmd)
    assert ran, "bash ran no git, so this case proves nothing"
    shared = any("/worktrees/" not in directory for directory, _sub in ran)
    assert gate._shared_tree_git_mutation(cmd) is shared


def test_a_shell_started_in_a_worktree_stays_there():
    assert not gate._shared_tree_git_mutation("(git commit)", "worktrees/solo")
    assert gate._shared_tree_git_mutation("git -C ../.. commit", "worktrees/solo")


@pytest.mark.parametrize("cmd", [
    "git --git-dir=.git commit", "git --work-tree=. commit", "git --git-dir .git commit",
])
def test_a_git_dir_flag_is_where_git_acts(cmd):
    assert not gate._shared_tree_git_mutation(f"cd worktrees/solo; {cmd}")
    assert gate._shared_tree_git_mutation(cmd)


@pytest.mark.parametrize("flag", ["--git-dir=../../.git", "--work-tree=../.."])
def test_a_git_dir_flag_can_leave_a_worktree(flag):
    assert gate._shared_tree_git_mutation(f"git {flag} commit", "worktrees/solo")


def test_an_unset_directory_is_the_shared_tree():
    assert gate._shared_tree_git_mutation('git -C "$WT" commit')
    assert gate._shared_tree_git_mutation('cd "$WT"; git commit')


def test_quoting_never_gives_up_but_a_real_command_after_it_is_read():
    # Quoted text stays quoted after `$'`, so the prose in it is not a push; a push after it is.
    assert not gate._command_denied("echo $'x'; echo 'a; git push b'", "git push")
    assert gate._command_denied("echo $'x'; echo 'a; git push b'; git push", "git push")


def test_a_name_the_command_never_set_stays_unread():
    assert not gate._command_denied("$G push", "git push")
    assert not gate._command_denied('$PYTHON -m pytest', "pytest")


def test_a_denied_command_needs_no_git():
    assert gate._command_denied("(npm publish --dry-run)", "npm publish")
    assert not gate._command_denied("npm -g publish", "npm publish")
    assert gate._command_denied("(rm -rf x)", "rm -rf")
    assert not gate._command_denied("anything", "")


def test_a_path_in_the_deny_entry_is_compared_whole():
    assert gate._command_denied("./deploy.sh now", "./deploy.sh")
    assert not gate._command_denied("deploy.sh now", "./deploy.sh")


def test_the_lease_needs_no_literal_git_in_the_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert gate._mutates_shared_tree(ToolEvent(tool="Bash", command="gi\\t push"))


@pytest.mark.parametrize(("cmd", "expected"), [
    ("echo `rm a`", ["a"]), ('echo "`rm a`"', ["a"]), ("x=`rm a`", ["a"]),
    ("cd d; echo `rm a`", ["d/a"]), ("echo `echo \\`rm a\\``", ["a"]),
    ("echo '`rm a`'", []), ("echo \\\\`rm a`", ["a"]),
    ("echo \"`true`\"; echo 'a`b'; echo `rm a`", ["a"]),
])
def test_the_write_check_reads_a_backtick_body(tmp_path, cmd, expected):
    (tmp_path / "a").write_text("x")
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "a").write_text("x")
    subprocess.run(["bash", "-c", cmd], cwd=tmp_path, capture_output=True, check=False)
    ran = not (tmp_path / "a").exists() or not (tmp_path / "d" / "a").exists()
    assert ran is bool(expected)
    assert gate._bash_write_targets(cmd) == expected


@pytest.mark.parametrize("cmd", [
    "if true; then true; fi " * 2000, "git status; " * 4000,
])
def test_a_long_command_stays_fast(cmd):
    begun = time.perf_counter()
    gate._command_denied(cmd, "git push")
    gate._shared_tree_git_mutation(cmd)
    assert time.perf_counter() - begun < 1


def test_a_word_read_only_when_named():
    begun = time.perf_counter()
    assert not gate._command_denied("do " * 16000, "git push")
    assert time.perf_counter() - begun < 1


def _building_project(path):
    claude = path / ".claude"
    claude.mkdir()
    shutil.copy(_CONFIG, claude / "sprint-config.yaml")
    phases.set_sprint_state({"feature": "f", "phase": "solo_execute"}, path)
    return path


def _pre_tool(path, command, deadline=gate._GATE_DEADLINE_SECONDS, setup=""):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s",
               "cwd": str(path)}
    code = (f"import sys\nfrom prusik import gate\ngate._GATE_DEADLINE_SECONDS = {deadline}\n"
            f"{setup}sys.exit(gate.pre_tool())")
    result = subprocess.run([sys.executable, "-c", code], cwd=path, input=json.dumps(payload),
                            capture_output=True, text=True, check=False, timeout=60,
                            env={**os.environ, "PYTHONPATH": str(_ROOT)})
    assert result.returncode == 0, result.stderr[-500:]
    if not result.stdout:
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("cmd", ["(git push)", "git -C . push", "bash -c 'git push'"],
                         ids=["subshell", "flag", "payload"])
def test_the_real_hook_denies_the_phase_command(tmp_path, cmd):
    assert "blocks command: 'git push'" in _pre_tool(_building_project(tmp_path), cmd)


@pytest.mark.parametrize("cmd", ["eval " * 9_600 + "git push"], ids=["eval"])
def test_a_command_too_slow_to_read_is_refused(tmp_path, cmd):
    assert "gate" in _pre_tool(_building_project(tmp_path), cmd, deadline=1.0)


def test_a_read_that_outruns_the_deadline_is_refused_on_the_deadline(tmp_path):
    """No command shape outruns the deadline any more: `"do git " * 6800 + "push"` took 59 s and now
    parses in milliseconds, and the `eval` shape above is refused for its depth. The mechanism is
    kept by making the parse slow."""
    slow = "import time\ngate._parsed = lambda cmd: time.sleep(30)\n"
    reason = _pre_tool(_building_project(tmp_path), "git push", deadline=1.0, setup=slow)
    assert "gate" in reason and "_GateDeadline" in reason


def test_code_nested_too_deep_is_refused_at_once():
    deep = "eval " * (gate._MAX_CODE_DEPTH + 2) + "git push"
    with pytest.raises(RecursionError):
        gate._command_denied(deep, "git push")
    assert gate._command_denied("eval " * gate._MAX_CODE_DEPTH + "git push", "git push")


@pytest.mark.parametrize("cmd", [
    "`` " * 16_000 + "git status", "bash -c " * 6_000 + "x", "(" * 48_000 + "x", "`" * 48_000,
    "do git " * 6_800 + "push",
], ids=["backticks", "bash-c", "parens", "ticks", "keyword"])
def test_a_long_shape_with_no_push_stays_cheap(tmp_path, cmd):
    project = _building_project(tmp_path)
    begun = time.perf_counter()
    assert _pre_tool(project, cmd) is None
    assert time.perf_counter() - begun < 5
