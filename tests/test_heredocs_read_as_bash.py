"""The gate takes out only what bash treats as heredoc data (fb-ccd55bc92cb0).

Real bash is the oracle: each shape runs in a temp directory with a stub `git` first on PATH, and
the gate must name the file, or deny the push, exactly when bash removed it or ran the stub. Each
shape also states the expected outcome, so a shape that stops doing what it says can't pass.

moat-finding: fb-ccd55bc92cb0
"""

import os
import stat
import subprocess

import pytest

from prusik import gate

_RM, _PUSH = "rm a", "git push"

_RUNS = [
    # `<<` that opens nothing.
    'echo "use <<EOF in docs"\n{run}\nEOF',
    "echo 'use <<EOF in docs'\n{run}\nEOF",
    "echo $((1<<2))\n{run}\n2",
    "((1<<2))\n{run}\n2",
    "# <<EOF\n{run}\nEOF",
    "cat <<<EOF\n{run}\nEOF",
    "\\<<EOF cat\n{run}\nEOF",
    # An unquoted body runs its substitutions.
    "cat <<EOF\n$({run})\nEOF",
    "cat <<EOF\n`{run}`\nEOF",
    "cat <<EOF\nx $({run}) y\nEOF",
    'cat <<EOF\n"$({run})"\nEOF',
    "cat <<EOF\n'$({run})'\nEOF",
    "cat <<EOF\n${{x:-$({run})}}\nEOF",
    "cat <<EOF\n\\\\$({run})\nEOF",
    "cat <<-EOF\n\t$({run})\n\tEOF",
    "cat <<EOF > /dev/null\n$({run})\nEOF",
    "x=$(cat <<EOF\n$({run})\nEOF\n)",
    'x="$(cat <<EOF\n$({run})\nEOF\n)"',
    # A second operator on the line can't lose the first body's substitution.
    "cat <<A <<'B'\n$({run})\nA\nb\nB",
    "cat <<'A' <<B\na\nA\n$({run})\nB",
    # A body handed to a shell is code.
    "bash <<EOF\n{run}\nEOF",
    "sh -s <<'EOF'\n{run}\nEOF",
    "/bin/bash <<EOF\n{run}\nEOF",
    "<<EOF bash\n{run}\nEOF",
    "X=1 bash <<EOF\n{run}\nEOF",
    "cat <<EOF | sh\n{run}\nEOF",
    "cat <<'EOF' | /bin/sh\n{run}\nEOF",
    "cat <<EOF \\\n| sh\n{run}\nEOF",
    "cat <<A; bash <<B\nprose\nA\n{run}\nB",
    # The closing line is bash's, so these bodies run on past the look-alike.
    "cat <<EOF\n  EOF\n$({run})\nEOF",
    "cat <<EOF\nEOF \n$({run})\nEOF",
    "cat <<EOF\n\tEOF\n$({run})\nEOF",
    "cat <<a-b\n$({run})\na-b",
    # Code around a body stays read, and a body's quotes shift nothing after it.
    "cat <<EOF; {run}\nbody\nEOF",
    "cat <<EOF\nbody\nEOF\n{run}",
    "cat <<EOF\nit's\nEOF\n{run}",
    'cat <<EOF\nsay "hi\nEOF\n{run}',
    "cat <<E\nit's\nE\necho \"a <<X b\"\n{run}\nX",
    'cat <<EOF "a\nb"\nbody\nEOF\n{run}',
]

_STAYS_DATA = [
    "cat <<EOF\n{run}\nEOF",
    "cat <<-EOF\n\t{run}\n\tEOF",
    "cat << EOF\n{run}\nEOF",
    # A quoted delimiter keeps the body literal.
    "cat <<'EOF'\n$({run})\nEOF",
    'cat <<"EOF"\n$({run})\nEOF',
    "cat <<\\EOF\n$({run})\nEOF",
    "cat <<E'O'F\n$({run})\nEOF",
    "cat <<'EOF'\n`{run}`\nEOF",
    # An escape keeps a substitution literal.
    "cat <<EOF\n\\$({run})\nEOF",
    "cat <<EOF\n\\`{run}\\`\nEOF",
    # Delimiters a `\w+` never matched.
    "cat <<'END OF'\n{run}\nEND OF",
    "cat <<a-b\n{run}\na-b",
    "cat <<a.b\n{run}\na.b",
    # Bodies in order.
    "cat <<A <<B\n{run}\nA\n{run}\nB",
    "cat <<A\n{run}\nA\ncat <<B\n{run}\nB",
    # The commit shape, with every quote a body can hold.
    "echo \"$(cat <<'EOF'\nit's \"q\" `x` (p) ) {run}\nEOF\n)\"",
    "echo \"$(cat <<'EOF'\n{run}\nit's `x`\nEOF\n)\"",
    # Nested code.
    "bash -c 'cat <<EOF\n{run}\nEOF'",
    # An empty delimiter closes at an empty line.
    "cat <<''\n{run}\n\n",
]


def _bash_ran(tmp_path, command: str) -> tuple[bool, bool]:
    """(`a` was removed, the stub ran `git push`) after real bash runs `command`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "git"
    stub.write_text(f'#!/bin/sh\necho "$*" >> "{tmp_path}/git-calls"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    (tmp_path / "a").write_text("")
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    subprocess.run(["bash", "-c", command], cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=10, check=False)
    calls = tmp_path / "git-calls"
    return not (tmp_path / "a").exists(), calls.exists() and "push" in calls.read_text()


def _gate_reads(command: str) -> tuple[bool, bool]:
    """(the gate names `a`, the gate denies `git push`)."""
    stripped = gate._strip_heredocs(command)
    return "a" in gate._bash_write_targets(stripped), gate._command_denied(stripped, _PUSH)


def _assert_matches_bash(tmp_path, shape: str, expected: bool) -> None:
    rm_dir, push_dir = tmp_path / "rm", tmp_path / "push"
    rm_dir.mkdir()
    push_dir.mkdir()
    removed = _bash_ran(rm_dir, shape.format(run=_RM))[0]
    pushed = _bash_ran(push_dir, shape.format(run=_PUSH))[1]
    assert (removed, pushed) == (expected, expected), "the shape does not do what it says"
    assert _gate_reads(shape.format(run=_RM))[0] is expected
    assert _gate_reads(shape.format(run=_PUSH))[1] is expected


@pytest.mark.parametrize("shape", _RUNS)
def test_a_command_bash_runs_is_read(tmp_path, shape):
    _assert_matches_bash(tmp_path, shape, True)


@pytest.mark.parametrize("shape", _STAYS_DATA)
def test_text_bash_treats_as_data_is_not_read(tmp_path, shape):
    _assert_matches_bash(tmp_path, shape, False)


@pytest.mark.parametrize("shell", ["bash", "sh", "zsh", "ksh", "dash", "/bin/zsh"])
def test_every_shell_in_the_set_reads_its_body_as_code(shell):
    assert _gate_reads(f"{shell} <<EOF\n{_PUSH}\nEOF")[1] is True


def test_a_script_argument_is_read_as_code_on_purpose():
    """`bash script.sh <<EOF` runs no body, but telling it apart means parsing each shell's flags,
    and a false refusal costs one turn."""
    assert _gate_reads(f"bash script.sh <<EOF\n{_PUSH}\nEOF")[1] is True


def test_a_heredoc_with_no_closing_line_stays_code():
    assert _gate_reads(f"cat <<EOF\n{_PUSH}")[1] is True
    assert _gate_reads(f"cat <<EOF\n{_PUSH}\n EOF")[1] is True


def test_an_operator_with_no_delimiter_opens_nothing():
    assert _gate_reads(f"cat <<\n{_PUSH}")[1] is True


def test_a_substitution_never_becomes_an_argument_of_the_command():
    """In `tee f <<EOF` with `$(date)` in the body, `$(date)` is not a file tee writes."""
    assert gate._bash_write_targets(gate._strip_heredocs("tee f <<EOF\n$(date)\nEOF")) == ["f"]


def test_the_body_is_gone_from_what_the_write_check_reads():
    stripped = gate._strip_heredocs("cat > out <<'EOF'\n> not-a-target\nEOF\n")
    assert gate._bash_write_targets(stripped) == ["out"]
    assert "not-a-target" not in stripped and "EOF" not in stripped


def test_a_commit_message_is_no_write_and_no_command():
    message = "it's \"q\" `git push` (p) > f\ngit push\n"
    command = f"git commit -m \"$(cat <<'EOF'\n{message}EOF\n)\""
    assert _gate_reads(command) == (False, False)
    assert gate._bash_write_targets(gate._strip_heredocs(command)) == []


@pytest.mark.parametrize("command", [
    'eval "cat <<EOF\ngit push\nEOF"',
    "trap 'cat <<EOF\ngit push\nEOF' EXIT",
])
def test_text_a_shell_runs_again_stays_code(command):
    """`_literal_text_mask` gives up on such text by design, so its heredocs read as code. A false
    refusal here costs a turn, and reading them would change the verdict on `... | sh` too."""
    assert gate._command_denied(gate._strip_heredocs(command), _PUSH) is True


def test_nested_heredocs_in_a_kept_body_are_stripped_too():
    assert _gate_reads(f"bash <<A\ncat <<B\n{_PUSH}\nB\nA")[1] is False


def test_many_heredocs_all_come_out():
    command = "\n".join(f"cat <<E{i}\n{_PUSH}\nE{i}" for i in range(50))
    assert _gate_reads(command)[1] is False


def test_a_command_without_the_operator_costs_no_scan(monkeypatch):
    def _no_scan(_cmd):
        raise AssertionError("no `<<`, so nothing to scan")

    monkeypatch.setattr(gate, "_quoted_text_mask", _no_scan)
    assert gate._strip_heredocs("git status && echo hi") == "git status && echo hi"
