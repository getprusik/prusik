"""A `>` in literal text is not a redirect, and no real redirect is lost to prove it.

moat-finding: fb-48a328898d07
"""

from __future__ import annotations

import subprocess

import pytest

from prusik import gate


@pytest.mark.parametrize("cmd", [
    r'''echo hi | sed -E "s/\) > \/\///"''',
    """echo hi | sed 's/a > b/c/'""",
    """awk '{ if ($1 > 3) print $2 > "/tmp/x" }' data.txt""",
    """prusik feedback "gate bug" --detail "the pattern ends in > //etc/passwd" --kind bug""",
    """git commit -m 'redirect > output.log is the bug'""",
    """echo "pipe it through tee out.log" """,
    r"""echo a \> b.log""",
    """ls # then > comment.log""",
    """echo 'one' "two > three.log" 'four > five.log'""",
    'echo "escaped \\" quote > inside.log"',
])
def test_literal_text_is_not_a_redirect(cmd):
    assert gate._bash_redirect_targets(cmd) == []


@pytest.mark.parametrize("cmd, target", [
    ("""sed 's/a > b/c/' in.txt > out.log""", "out.log"),
    ("""echo "a > b" >> out.log""", "out.log"),
    ("""echo 'x' 2> err.log""", "err.log"),
    ("""echo "hi" | tee out.log""", "out.log"),
    ("""echo 'hi' | tee -a out.log""", "out.log"),
    ("""echo hi > "path with spaces.log" """, "path with spaces.log"),
    ("""echo hi > 'quoted.log'""", "quoted.log"),
    ("""echo "hi">out.log""", "out.log"),
    ("""echo 'hi'>out.log""", "out.log"),
    ("""ls # don't stop here\necho x > out.log""", "out.log"),
    ("""echo $# > out.log""", "out.log"),
    ("""echo ${#a} > out.log""", "out.log"),
    (r"""echo \\ > out.log""", "out.log"),
])
def test_real_redirects_survive_beside_literal_text(cmd, target):
    assert target in gate._bash_redirect_targets(cmd)


@pytest.mark.parametrize("cmd, target", [
    ('''echo "$(date > stamp.log)"''', "stamp.log"),
    ('''echo "$(echo "a" > nested.log)"''', "nested.log"),
    ('''echo "`date > stamp.log`"''', "stamp.log"),
    ('''echo "text $(date) and then" > out.log''', "out.log"),
    ("""echo 'never closed > out.log""", "out.log"),
    ('''echo "never closed > out.log''', "out.log"),
    ('''eval "echo x > out.log"''', "out.log"),
    ("""eval 'echo x > out.log'""", "out.log"),
    ("""echo $'it\\'s' > out.log""", "out.log"),
    ("""echo ${a} 'x > y' > out.log""", "out.log"),
])
def test_uncertain_lexing_scans_as_code(cmd, target):
    assert target in gate._bash_redirect_targets(cmd)


# Each writes a file named `f1` under real bash. The test asks bash first, so a case can't
# encode a wrong reading of shell, then requires the gate to name the file.
_REAL_WRITES = [
    """echo $'it\\'s' > f1; echo 'x'""",
    """echo $'a\\'b' 2>'f1'""",
    """echo $'\\'' > f1 && echo 'z'""",
    "echo a\\\n#x > f1",
    """echo \\;#b > f1""",
    """echo \\ #b > f1""",
    """echo ${x:-a #b} > f1""",
    """echo ${x:-"a"} > f1; echo 'y'""",
    """echo a | \\tee f1""",
    """echo "$(echo "a" > f1)" """,
    """echo "`echo a > f1`" """,
    """echo `echo 'a' > f1` 'b'""",
    """echo 'a' ; echo "b" > f1""",
    """echo don\\'t > f1; echo 'x'""",
    """ls # don't\necho x > f1; echo 'y'""",
    """echo $((1 > 2)) > f1""",
    """cat <<< 'x' > f1""",
    """eval "echo a > f1" """,
    """echo "echo a > f1" | sh""",
    """echo 'echo a > f1' | bash -s""",
    """echo 'echo a > f1' | /bin/sh""",
    """echo 'echo a > f1' | xargs -0 sh -c""",
    """trap "echo a > f1" EXIT""",
    """trap 'echo a > f1' EXIT""",
    """echo '>' >'f1'""",
    'echo ">" >"f1"',
    """echo '>' 2>'f1'""",
    """echo "a $(echo '>')" >'f1'""",
    """echo "a >x"> f1""",
    """echo '>' "b" >> 'f1'""",
    """echo 'tee x' | tee 'f1'""",
]


@pytest.mark.parametrize("cmd", _REAL_WRITES)
def test_a_real_write_in_bash_is_always_named(cmd, tmp_path):
    subprocess.run(["/bin/bash", "-c", cmd], cwd=tmp_path, capture_output=True, check=False)
    assert (tmp_path / "f1").exists(), "not a real write, so the case proves nothing"
    assert "f1" in gate._bash_redirect_targets(cmd)


def test_a_command_with_no_operator_skips_the_scan(monkeypatch):
    def fail(_cmd):
        raise AssertionError("scanned a command that can't redirect")
    monkeypatch.setattr(gate, "_strip_c_payloads", fail)
    assert gate._bash_redirect_targets("""git status && echo 'plain'""") == []


def test_the_block_message_no_longer_fires_on_the_reported_command():
    # The original report: the gate blocked the bug report about itself.
    cmd = """prusik feedback "x" --detail 'sed pattern ) > //'"""
    assert gate._bash_redirect_targets(gate._strip_heredocs(cmd)) == []
