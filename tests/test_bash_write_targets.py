"""The gate sees what a Bash command writes, at the path it lands on. Real bash, sed and perl
are the oracle: a write case must change a file on disk before the gate is held to it.

moat-finding: fb-a609748e159f
moat-finding: fb-2efaa09c9545
moat-finding: fb-7e7454d19bc0
"""

from __future__ import annotations

import subprocess

import pytest

from prusik import gate, phases
from prusik.platform_adapter import ToolEvent

_GNU_SED = subprocess.run(
    ["sed", "--version"], capture_output=True, check=False).returncode == 0


def _bash(cmd, cwd):
    subprocess.run(["/bin/bash", "-c", cmd], cwd=cwd, capture_output=True, check=False)


def _targets(cmd):
    return gate._bash_write_targets(cmd)


# ---- in-place editors ------------------------------------------------------------------

_INPLACE_ANY_SED = [
    "sed -i.bak 's/a/b/' f1",
    "sed -i.bak -e 's/a/b/' f1",
    "sed -e 's/a/b/' -i.bak f1",
    "sed -E -i.bak 's/a/b/' f1",
    "sed -n -i.bak 's/a/b/p' f1",
    "sed -i.bak -e 's/a/b/' -e 's/z/y/' f1",
    "sed -i.bak -e 's/a/b/' -- f1",
    "sed -i.bak 's/a/b/' \\\n  f1",
    "sed -i.bak 's/a/b/' f1 2>&1",
    "sed -i.bak 's/a/b/' f1 >/dev/null",
    "sed -i.bak 's/a/b/' f1 # note",
    "true && sed -i.bak 's/a/b/' f1; echo done",
    "/usr/bin/sed -i.bak 's/a/b/' f1",
]
_INPLACE_BSD_SED = ["sed -i '' 's/a/b/' f1", "sed -i '' -e 's/a/b/' f1", "sed -E -i '' 's/a/b/' f1"]
_INPLACE_GNU_SED = ["sed -i 's/a/b/' f1", "sed -ni 's/a/b/p' f1", "sed --in-place 's/a/b/' f1",
                    "sed --in-place=.bak 's/a/b/' f1", "sed --expression=s/a/b/ -i f1",
                    "sed --in-place --expression 's/a/b/' f1"]
_INPLACE_PERL = [
    "perl -pi -e 's/a/b/' f1",
    "perl -p -i -e 's/a/b/' f1",
    "perl -i -pe 's/a/b/' f1",
    "perl -i.orig -pe 's/a/b/' f1",
    "perl -pi.bak -e 's/a/b/' f1",
    "perl -0777 -pi -e 's/a/b/' f1",
    "perl -lpi -e 's/a/b/' f1",
    "perl -Mstrict -pi -e 's/a/b/' f1",
]


@pytest.mark.parametrize("cmd", _INPLACE_ANY_SED + _INPLACE_BSD_SED + _INPLACE_GNU_SED
                         + _INPLACE_PERL + ["perl -pie 's/a/b/' f1"])
def test_an_in_place_edit_names_its_file_and_never_its_script(cmd):
    assert _targets(cmd) == ["f1"]


@pytest.mark.parametrize("cmd", _INPLACE_ANY_SED + _INPLACE_PERL
                         + (_INPLACE_GNU_SED if _GNU_SED else _INPLACE_BSD_SED))
def test_the_in_place_cases_really_edit_the_file(cmd, tmp_path):
    (tmp_path / "f1").write_text("a\n")
    _bash(cmd, tmp_path)
    assert "b" in (tmp_path / "f1").read_text(), "not a real edit, so the case proves nothing"


def test_every_edited_file_is_named():
    assert _targets("sed -i '' 's/a/b/' f1 src/f2.py") == ["f1", "src/f2.py"]
    assert _targets("perl -pi -e 's/a/b/' f1 f2") == ["f1", "f2"]


@pytest.mark.parametrize("cmd", [
    "sed 's/a/b/' f1",
    "sed -n '/i/p' f1",
    "sed -e 's/x/-i/' f1",
    "sed -E 's/a/b/' f1 | sort",
    "perl -pe 's/a/b/' f1",
    "perl -ne 'print if /i/' f1",
    "perl -e 'print 1'",
    "perl -Mi -e 'print 1' f1",
    "echo 'sed -i s f1'",
    'prusik feedback "sed -i misses f1" --kind bug',
])
def test_no_in_place_flag_names_nothing(cmd):
    assert _targets(cmd) == []


# ---- redirects, as bash reads them -----------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "cat> f1 </dev/null",
    "echo a>f1",
    "case x in x) echo a;; esac> f1",
    "echo $((1>2))> f1",
    "echo a >| f1",
    "echo a >&f1",
    "echo a 1<> f1",
    "exec {fd}>f1",
    "echo [[ a > f1 ]]",
    "echo a > \\\n  f1",
    "echo a | tee f1 f2",
    "echo a | tee -a f1 f2",
    "echo a | tee -ai f2 f1",
    "echo x | tee f2 > f1",
    "echo a | tee >(cat >/dev/null) f1",
    "echo a | tee f2 >(cat >/dev/null) f1",
    "echo a | tee >(cat >/dev/null) >(cat >/dev/null) f1",
])
def test_every_write_real_bash_performs_is_named(cmd, tmp_path):
    _bash(cmd, tmp_path)
    assert (tmp_path / "f1").exists(), "not a real write, so the case proves nothing"
    assert "f1" in _targets(cmd)


@pytest.mark.parametrize("cmd, name", [
    ("echo a > a\\ b", "a b"),
    ('echo a > "a b"c', "a bc"),
    ("echo a > 'a b'", "a b"),
    ("echo a | tee a\\ b", "a b"),
    ("sed -i.bak 's/a/b/' a\\ b", "a b"),
])
def test_a_target_is_one_shell_word(cmd, name, tmp_path):
    (tmp_path / "a b").write_text("a\n")
    (tmp_path / "abc").write_text("")
    _bash(cmd, tmp_path)
    assert (tmp_path / name).exists()
    assert name in _targets(cmd)


def test_forms_this_host_may_not_run_are_still_read():
    # macOS ships bash 3.2 with no `&>>`, and BSD tee has no long flags.
    assert _targets("echo a &>> f1") == ["f1"]
    assert _targets("echo a | tee --append f1") == ["f1"]


def test_a_home_target_expands(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    assert _targets("echo a > ~/x") == ["/home/u/x"]


@pytest.mark.parametrize("cmd", [
    "echo $((3>2))",
    "a=3; b=2; echo $((a>b))",
    "((3>2)) && echo ok",
    "[[ a>b ]]",
    "[[ 2 > 1 ]] && echo ok",
    'echo "$((3>2))"',
    "if [[ a > b ]]; then echo ok; fi",
    "echo a 2>&1",
    "echo a >&2",
    "echo a 2>&-",
])
def test_a_comparison_or_a_descriptor_writes_nothing(cmd, tmp_path):
    _bash(cmd, tmp_path)
    assert list(tmp_path.iterdir()) == [], "bash wrote a file, so the case proves nothing"
    assert _targets(cmd) == []


@pytest.mark.parametrize("cmd", [
    """mongosh --eval 'db.a.find().forEach(a=>print(a))'""",
    """npm run x --eval "a=>b" """,
    r'''git commit -m "a->b in \`src\`"''',
    '''git commit -m "eval a->b"''',
    """echo "${x}" ${y:-z} 'a>b'""",
    r'''python3 -c "print(\"a>b\")"''',
    r'''prusik feedback "x" --detail "REPO=\$(git rev-parse) a->b"''',
])
def test_literal_text_stays_literal_without_the_separator(cmd):
    assert _targets(cmd) == []


@pytest.mark.parametrize("cmd", [
    """echo "$(echo a > f1)" 'b>c'""",
    """echo "$(echo "x > y" > f1)" """,
    """echo "$(echo 'z>w' > f1)" """,
    """echo "text $(echo a > f1) more" 'b>c'""",
    r'''python3 -c "print(\"x\")" > f1''',
    """echo "`echo a > f1`" """,
])
def test_a_substitution_in_double_quotes_is_code(cmd, tmp_path):
    _bash(cmd, tmp_path)
    assert (tmp_path / "f1").exists(), "not a real write, so the case proves nothing"
    assert _targets(cmd) == ["f1"]


# ---- where a target lands --------------------------------------------------------------

@pytest.mark.parametrize("cmd, landing", [
    ("cd d && echo a > f1", "d/f1"),
    ("cd d; echo a > f1", "d/f1"),
    ("cd d\necho a > f1", "d/f1"),
    ("cd d/e && echo a > ../f1", "d/f1"),
    ("cd d && cd e && echo a > f1", "d/e/f1"),
    ("if true; then cd d; echo a > f1; fi", "d/f1"),
    ("{ cd d; echo a > f1; }", "d/f1"),
    ("cd d && echo a | tee f1", "d/f1"),
    ("(cd d && echo a > f1)", "d/f1"),
    ("(cd d); echo a > f1", "f1"),
    ("x=$(cd d && pwd); echo a > f1", "f1"),
    ("echo cd d > f1", "f1"),
    ("echo 'cd d' > f1", "f1"),
    ("(cd d && (cd e)); cd d; echo a > f1", "d/f1"),
    ("pushd d >/dev/null; echo a > f1", "d/f1"),
    ("pushd d >/dev/null; pushd e >/dev/null; echo a > f1", "d/e/f1"),
    ("pushd d >/dev/null; popd >/dev/null; echo a > f1", "f1"),
    ("cd d; pushd e >/dev/null; popd >/dev/null; echo a > f1", "d/f1"),
])
def test_a_target_lands_where_bash_puts_it(cmd, landing, tmp_path):
    (tmp_path / "d" / "e").mkdir(parents=True)
    _bash(cmd, tmp_path)
    assert (tmp_path / landing).exists(), "the case names the wrong landing path"
    assert landing in _targets(cmd)


def test_an_absolute_target_ignores_the_cd():
    assert _targets("cd d && echo a > /x/f1") == ["/x/f1"]
    assert _targets("cd /abs && echo a > f1") == ["/abs/f1"]


def test_a_sed_after_cd_lands_in_that_directory():
    assert _targets("cd worktrees/solo && sed -i '' 's/a/b/' prusik/gate.py") == [
        "worktrees/solo/prusik/gate.py"]


# ---- targets the gate can't resolve ----------------------------------------------------

@pytest.mark.parametrize("cmd, target", [
    ("echo a > $OUT", "$OUT"),
    ('echo a > "$OUT"', "$OUT"),
    ("echo a > $OUT/x", "$OUT/x"),
    ("echo a > ${OUT}.log", "${OUT}.log"),
    ("echo a > $(mktemp)", "$(mktemp)"),
    ("echo a > `mktemp`", "`mktemp`"),
    ('sed -i "" s/a/b/ "$f"', "$f"),
    ("echo a | tee $OUT", "$OUT"),
    ("cd $D && echo a > f1", "$D/f1"),
    ("cd - && echo a > f1", "$OLDPWD/f1"),
    ("pushd +1 && echo a > f1", "$OLDPWD/f1"),
    ("pushd && echo a > f1", "$OLDPWD/f1"),
    ("popd && echo a > f1", "$OLDPWD/f1"),
    ("grep -l a * | xargs sed -i '' 's/a/b/'", gate._FROM_STDIN),
    ("find . -name x -exec sed -i '' 's/a/b/' {} +", gate._FROM_FIND),
    ("find . -name x -exec perl -pi -e 's/a/b/' {} \\;", gate._FROM_FIND),
])
def test_an_unresolvable_target_is_named_as_it_stands(cmd, target):
    assert target in _targets(cmd)
    assert gate._is_unresolved(target)


def test_an_unresolved_cd_can_not_borrow_a_writable_prefix():
    # $X may be absolute, so the target must not read as inside the worktree.
    assert _targets("cd worktrees/solo && cd $X && echo a > f1") == ["$X/f1"]


# ---- the gate itself -------------------------------------------------------------------

_CFG = {"phases": [
    {"name": "planning", "writable": ["design/{feature}/plan.md"]},
    {"name": "solo_execute", "writable": ["worktrees/solo/**"]},
]}


@pytest.fixture
def gated(tmp_path, monkeypatch):
    blocks = []
    monkeypatch.setattr(phases, "project_root", lambda: tmp_path)
    monkeypatch.setattr(gate.ledger, "project_root", lambda: tmp_path)
    monkeypatch.setattr(gate.ledger, "append", lambda *a, **k: blocks.append(k))

    def decide(cmd, phase="planning"):
        event = ToolEvent(tool="Bash", command=cmd)
        return gate._gate_decision(event, _CFG, phase, "f")
    decide.blocks = blocks
    return decide


@pytest.mark.parametrize("cmd", [
    "sed -i '' 's/a/b/' design/f/scope.md",
    "perl -pi -e 's/a/b/' design/f/scope.md",
    "cd design/f && sed -i '' 's/a/b/' scope.md",
    "(cd worktrees/solo && true); sed -i '' 's/a/b/' design/f/scope.md",
    "cat> design/f/scope.md </dev/null",
])
def test_the_gate_refuses_what_it_used_to_miss(gated, cmd):
    assert "design/f/scope.md" in gated(cmd)
    assert gated.blocks[-1]["gate_class"] == gate.gate_class.WRITABLE_SCOPE


@pytest.mark.parametrize("cmd", [
    "cd worktrees/solo && sed -i '' 's/a/b/' prusik/gate.py",
    "cd worktrees/solo && echo a > f1",
    "sed -i '' 's/a/b/' worktrees/solo/prusik/gate.py",
    "sed 's/a/b/' design/f/scope.md",
    "echo a>/dev/null",
])
def test_the_gate_allows_what_lands_in_the_writable_set(gated, cmd):
    assert gated(cmd, "solo_execute") is None


@pytest.mark.parametrize("cmd", [
    "cat > $S/p.py", "echo a > $OUT", "grep -l a f | xargs sed -i '' 's/a/b/'",
])
def test_an_unresolved_refusal_says_so_and_offers_no_route(gated, cmd):
    msg = gated(cmd, "solo_execute")
    assert "can't tell where it lands" in msg
    assert "use the Write tool" in msg
    assert "Try writing to" not in msg


def test_a_resolved_refusal_keeps_its_worktree_route(gated):
    assert "Try writing to worktrees/solo/x.py" in gated("echo a > x.py", "solo_execute")


def test_a_command_that_can_not_write_skips_the_scan(monkeypatch):
    def fail(_cmd):
        raise AssertionError("scanned a command that can't write")
    monkeypatch.setattr(gate, "_parsed", fail)
    assert gate._bash_write_targets("git status && ls -la") == []
