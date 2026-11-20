"""A command behind a wrapper (`env`, `sudo`, `command`, `xargs`, `find -exec` ...) is named for
`deny_commands` and the shared-tree git check, and a wrapped shell's heredoc is read as code.
Quoted text, `command -v` and a wrapper's value flags name nothing.

moat-finding: fb-0e678051f98e
"""

from __future__ import annotations

import pytest

from prusik import bash_reader, gate

_WRAPPED_PUSHES = [
    "env git push origin main",
    "command git push",
    "exec git push",
    "nohup git push",
    "time git push",
    "sudo -u me git push",
    "env -i A=1 git push",
    "nice -n 5 git push",
    "timeout 5 git push",
    "xargs -I{} git push",
    "find . -exec git push \\;",
    "find . -okdir git push {} +",
    "builtin command git push",
    "stdbuf -oL git push",
    "doas git push",
    "sudo env command git push",
    "env $X git push",
    "/usr/bin/env git push",
    "sudo --user=me -- git push",
    "timeout -s KILL 5 git push",
    "echo ok && env git push",
]


@pytest.mark.parametrize("cmd", _WRAPPED_PUSHES)
def test_wrapped_push_is_denied(cmd):
    assert gate._command_denied(cmd, "git push"), cmd


_WRAPPED_SHELLS = [
    "sudo bash <<EOF\ngit push\nEOF",
    "env bash <<EOF\ngit push\nEOF",
    "exec bash <<EOF\ngit push\nEOF",
    "time bash <<EOF\ngit push\nEOF",
    "command bash <<EOF\ngit push\nEOF",
    "cat <<EOF | env bash\ngit push\nEOF",
    "cat <<EOF | exec sh\ngit push\nEOF",
    "sudo bash -c 'git push'",
    "env -u HOME sh -c 'git push'",
]


@pytest.mark.parametrize("cmd", _WRAPPED_SHELLS)
def test_wrapped_shell_heredoc_is_read(cmd):
    assert gate._command_denied(cmd, "git push"), cmd


_PRESERVED = [
    "echo env git push",
    "env 'git push'",
    'echo "sudo git push"',
    "command -v git",
    "command -V git push",
    "env GIT_DIR=x git status",
    "sudo -u git ls",
    "find . -name git -exec echo push \\;",
    "xargs echo git push",
    "cat <<EOF | env grep x\ngit push\nEOF",
    "sudo cat <<EOF\ngit push\nEOF",
]


@pytest.mark.parametrize("cmd", _PRESERVED)
def test_quoted_and_value_words_name_nothing(cmd):
    assert not gate._command_denied(cmd, "git push"), cmd


def test_wrapped_git_commit_mutates_the_shared_tree():
    assert gate._shared_tree_git_mutation("env git commit -m x")
    assert gate._shared_tree_git_mutation("sudo -u me git commit -m x")
    assert not gate._shared_tree_git_mutation("cd worktrees/solo && env git commit -m x")


def test_command_spans_span_find_exec_to_its_terminator():
    script = gate._parsed("find . -exec git push \\; -exec rm x {} +")
    stage = next(e[1] for e in bash_reader.iter_commands(script) if e[0] == "simple")
    spans = gate._command_spans(stage, {})
    words = [w.text for w in stage.words]
    assert [words[s:e] for s, e in spans] == [
        words, ["git", "push"], ["rm", "x", "{}"]]
