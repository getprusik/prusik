"""The gate reads a Bash command as bash does, from one reader (one-bash-lexer).

Real bash is the oracle: each shape runs in a temp directory holding a file `a`, with a stub `git`
first on PATH, and the gate must name `a` as a write target, or deny `git push`, exactly when bash
removed the file or ran the stub. Each shape also states the outcome it expects, so a shape that
stops doing what it says can't pass. Every call takes the RAW command, as the hook does.

Three groups assert on the gate alone, and say why where they do: the stated over-reads (code the
gate reads on purpose though bash runs none of it), the shapes whose result differs between bash 3.2
(this Mac) and bash 5 (CI), and the lease, parse-shape and recovery checks, which have no bash
half. The rest of the file covers the absence of the old scanners, one parse per call, linear cost
and the shape of the tree.

moat-finding: fb-401f6430ec61
moat-finding: fb-386c186d88ee
moat-finding: fb-b298c5270e7a
"""

import ast
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

from prusik import bash_reader, gate

_RM, _PUSH = "rm a", "git push"
_LITERAL_DOLLAR, _QUOTED_DOLLAR = bash_reader.LITERAL_DOLLAR, bash_reader.QUOTED_DOLLAR


_HERE = {"a", "bin", "git-calls"}


def _bash_ran(tmp_path, command: str) -> tuple[bool, bool, list[str]]:
    """(`a` was removed, the stub ran `git push`, files bash created) after real bash runs
    `command`."""
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
    created = sorted(p.name for p in tmp_path.iterdir() if p.name not in _HERE)
    return not (tmp_path / "a").exists(), calls.exists() and "push" in calls.read_text(), created


def _gate_reads(command: str) -> tuple[bool, bool]:
    """(the gate names `a`, the gate denies `git push`), from the raw command."""
    return "a" in gate._bash_write_targets(command), gate._command_denied(command, _PUSH)


def _agree(tmp_path, command: str, removed: bool, pushed: bool) -> list[str]:
    """Bash did what the case says, and the gate reads exactly that. Returns what bash created."""
    ran = _bash_ran(tmp_path, command)
    assert ran[:2] == (removed, pushed), "the shape does not do what it says"
    assert _gate_reads(command) == (removed, pushed)
    return ran[2]


def _forms(shape) -> tuple[str, str]:
    """The shape written once with `rm a` and once with `git push` where it says RUN. A shape that
    can't be swapped by text comes as the pair itself."""
    if isinstance(shape, tuple):
        return shape
    return shape.replace("RUN", _RM), shape.replace("RUN", _PUSH)


def _agree_shape(tmp_path, shape, expected: bool) -> list[str]:
    rm_form, push_form = _forms(shape)
    rm_dir, push_dir = tmp_path / "rm", tmp_path / "push"
    rm_dir.mkdir()
    push_dir.mkdir()
    created = _agree(rm_dir, rm_form, expected, False)
    created += _agree(push_dir, push_form, False, expected)
    return created


def _id(shape) -> str:
    text = shape if isinstance(shape, str) else shape[0]
    return text.replace("\n", "\\n").replace("\t", "\\t")[:70]


# --- bash runs it, the gate must read it ---------------------------------------------------------
_RUNS = [
    # A `<<` inside a substitution that closed before the line ended.
    'echo "$(echo <<A)"\nRUN\nA',
    'echo "$(cat <<A\nx\nA\n)" ; RUN',
    # Code past an array assignment prefix, and code in `[[ ]]`, `(( ))` and `$(( ))`.
    "a=(1 2) RUN",
    "echo $(( $(RUN; echo 1) + 1 ))",
    "(( $(RUN; echo 1) ))",
    "[[ -n $(RUN) ]]",
    "[[ -n `RUN` ]]",
    "[[ -f a && -n $(RUN) ]]",
    # Heredoc bodies: substitutions in an unquoted one, and a comment's `)` or a joined line.
    "cat <<EOF\n$(echo hi # )\nRUN)\nEOF",
    "cat <<EOF\n$\\\n(RUN)\nEOF",
    'cat <<EOF\n"$(RUN)"\nEOF',
    # A body that a shell reads, wherever the shell sits.
    "{ bash; } <<EOF\nRUN\nEOF",
    "while true; do bash; break; done <<EOF\nRUN\nEOF",
    "if true; then bash; fi <<EOF\nRUN\nEOF",
    "echo `bash <<B\nRUN\nB\n`",
    'eval "$(cat <<EOF\nRUN\nEOF\n)"',
    "bash -c \"$(cat <<'EOF'\nRUN\nEOF\n)\"",
    "sh < <(cat <<EOF\nRUN\nEOF\n)",
    "source /dev/stdin <<EOF\nRUN\nEOF",
    ". /dev/stdin <<EOF\nRUN\nEOF",
    'x="$(bash <<EOF\nRUN\nEOF\n)"',
    "bash <<-EOF\n\tRUN\n\tEOF",
    # Text handed to a shell on a pipe, and a here-string.
    "echo 'RUN' | sh",
    "echo RUN | sh",
    "bash <<< 'RUN'",
    # Pipes past the next stage.
    "cat <<EOF | { cat; } | sh\nRUN\nEOF",
    "echo 'RUN' | cat | sh",
    "cat <<EOF | { sh; }\nRUN\nEOF",
    # Compounds and keywords.
    "function f { RUN; }; f",
    "f() { RUN; }; f",
    "case x in x) RUN;; esac",
    "if true; then RUN; fi",
    "for ((i=0;i<1;i++)); do RUN; done",
    "time RUN",
    "! RUN",
    "true & RUN",
    "(cd /; pwd); RUN",
    "true && { RUN; }",
    # Nested substitutions and backticks.
    "echo $(echo $(RUN))",
    "echo `echo \\`RUN\\``",
    'echo "$(echo ")" ; RUN)"',
    # Line continuations, comments, escaped operators.
    "echo x \\\n; RUN",
    "echo x # <<EOF\nRUN\nEOF",
    "cat \\<<EOF 2>/dev/null\nRUN\nEOF",
    ("r\\\nm a", "gi\\\nt push"),
    # Prefixes and literal code handed to a code runner.
    "X=1 RUN",
    "> f RUN",
    "bash -c 'RUN'",
    "eval 'RUN'",
    "eval RUN",
    "trap 'RUN' EXIT",
    "eval -- RUN",
    "trap -- 'RUN' EXIT",
    # Code in an array subscript or literal, on an assignment alone or after `export`/`local`.
    "a[$(RUN)]=1",
    "echo hi; a[$(RUN)]=1",
    "a[$(RUN)]+=1",
    "x[ $(RUN) ]=1",
    "a[`RUN`]=1",
    'a["$(RUN)"]=1',
    "a[b[$(RUN)]]=1",
    "x=1 a[$(RUN)]=1",
    "a[ x ]=$(RUN)",
    "a=( $(RUN) )",
    "a=( [$(RUN)]=x )",
    "a=( [1]=$(RUN) )",
    "export A[$(RUN)]=1",
    "local A[$(RUN)]=1",
    "readonly A[$(RUN)]=1",
    "declare -a b=( $(RUN) )",
    # Code in the expansions, arithmetic and operands that carry a word.
    "echo ${a[$(RUN)]}",
    "echo ${x:-$(RUN)}",
    "echo $(( a[$(RUN)] ))",
    "(( a[$(RUN)]++ ))",
    "for (( $(RUN); 0; )); do :; done",
    "[[ x =~ $(RUN) ]]",
    "[[ $(RUN) ]]",
    "case $(RUN) in x) ;; esac",
    "case x in $(RUN)) ;; esac",
    "case x in $(RUN)|y) ;; esac",
    "for i in $(RUN); do :; done",
    "select i in $(RUN); do break; done </dev/null",
    "echo > $(RUN)",
    "echo x 2> $(RUN)",
    "echo {fd}> $(RUN)",
    "cat <<< $(RUN)",
    'echo $"$(RUN)"',
    "time $(RUN)",
    "export A=$(RUN)",
    # A command name split by quoting is still the command.
    ("r''m a", "g''it push"),
    ('rm"" a', 'git"" push'),
    ("r\\m a", "g\\it push"),
    ("$'rm' a", "$'git' push"),
    ('"rm" a', '"git" push'),
    ("'r'm a", "'g'it push"),
    ('eval "r"m a', 'eval "g"it push'),
    # A quoted `<<` after each point the old mask stopped opens nothing: bash runs the next line.
    "echo $'x' '<<EOF'\nRUN\nEOF",
    'echo "`date`" \'<<EOF\'\nRUN\nEOF',
    'echo ${x:-"y"} "<<EOF"\nRUN\nEOF',
]


@pytest.mark.parametrize("shape", _RUNS, ids=[_id(s) for s in _RUNS])
def test_a_command_bash_runs_is_read(tmp_path, shape):
    _agree_shape(tmp_path, shape, True)


def test_a_second_heredoc_on_the_line_is_read_in_order(tmp_path):
    """`bash` takes the second body: `rm a` runs, the first body is data, so no push."""
    _agree(tmp_path, "cat <<A; bash <<B\ngit push\nA\nrm a\nB", True, False)


# --- bash treats it as text, the gate must not read it ------------------------------------------
_TEXT = [
    # Quoting that the old mask gave up on.
    "echo $'a; RUN'",
    "echo \"`date`\" 'a; RUN > f'",
    "echo ${x:-\"a > b\"} '; RUN'",
    "echo eval; echo 'a; RUN'",
    'echo $"a; RUN"',
    # A body no shell reads.
    "cat <<EOF | sh.py\nRUN\nEOF",
    "cat <<EOF | bash-completion\nRUN\nEOF",
    "cat <<EOF || sh\nRUN\nEOF",
    "cat <<EOF; echo x | sh\nRUN\nEOF",
    'eval "cat <<EOF\nRUN\nEOF"',
    "trap 'cat <<EOF\nRUN\nEOF' EXIT",
    "cat <<EOF\nfoo\\\nEOF\nRUN\nEOF",
    "cat <<'EOF'\n$(RUN)\nEOF",
    # Words that are not commands.
    "for i in RUN; do :; done",
    "echo {RUN}",
    "echo do RUN",
    "echo if RUN",
]


@pytest.mark.parametrize("shape", _TEXT, ids=[_id(s) for s in _TEXT])
def test_text_bash_does_not_run_is_not_read(tmp_path, shape):
    created = _agree_shape(tmp_path, shape, False)
    assert created == [], "bash created a file, so something in the shape ran"
    assert gate._bash_write_targets(_forms(shape)[0]) == []


def test_a_python_comparison_is_no_redirect(tmp_path):
    command = 'python3 -c "print(1 > 2)"'
    assert _bash_ran(tmp_path, command)[2] == []
    assert gate._bash_write_targets(command) == []


# --- the quoted text stays quoted and the real command after it is still read -------------------
_QUOTED = [
    "echo $'a; X'",
    "echo \"`date`\" 'a; X'",
    "echo ${x:-\"a > b\"} '; X'",
    "echo eval; echo 'a; X'",
    'echo $"a; X"',
]


@pytest.mark.parametrize("quoted", _QUOTED, ids=[_id(s) for s in _QUOTED])
def test_a_real_command_after_quoted_text_is_still_read(tmp_path, quoted):
    """The quoted text holds the other command; only the one after it runs."""
    rm_dir, push_dir = tmp_path / "rm", tmp_path / "push"
    rm_dir.mkdir()
    push_dir.mkdir()
    _agree(rm_dir, quoted.replace("X", _PUSH) + " ; " + _RM, True, False)
    _agree(push_dir, quoted.replace("X", _RM) + " ; " + _PUSH, False, True)


@pytest.mark.parametrize("quoted", _QUOTED, ids=[_id(s) for s in _QUOTED])
def test_a_real_redirect_after_quoted_text_is_still_read(tmp_path, quoted):
    command = quoted.replace("X", _RM) + " > f"
    assert _agree(tmp_path, command, False, False) == ["f"]
    targets = gate._bash_write_targets(command)
    assert "f" in targets and "b" not in targets and "a" not in targets


# --- stated over-reads: the gate reads code bash does not run, on purpose -----------------------
def test_a_function_body_is_read_where_it_is_defined(tmp_path):
    """Over-read by design: `f` is never called, so bash removes nothing, but a definition is code
    the gate reads where it stands, called or not (a call can come from anywhere)."""
    command = "f() { rm a; }"
    assert _bash_ran(tmp_path, command)[0] is False
    assert "a" in gate._bash_write_targets(command)


def test_a_script_argument_is_read_as_code_on_purpose(tmp_path):
    """Over-read by design: `bash script.sh <<EOF` runs the script and not the body, but telling
    it from `bash <<EOF` means parsing each shell's flags, and a false refusal costs one turn."""
    command = f"bash script.sh <<EOF\n{_PUSH}\nEOF"
    assert _bash_ran(tmp_path, command)[1] is False
    assert gate._command_denied(command, _PUSH) is True


@pytest.mark.parametrize("command", ["echo \"abc\nrm a", "echo hi\necho $(rm a"])
def test_an_unterminated_shape_is_read_on_to_its_end(tmp_path, command):
    """Over-read by design: bash rejects the syntax and runs nothing, but the text after the opener
    is also read as code, so nothing after an unterminated quote or substitution is lost."""
    assert _bash_ran(tmp_path, command)[0] is False
    assert "a" in gate._bash_write_targets(command)


# --- version-dependent: asserted on the gate alone ----------------------------------------------
def test_a_pipe_with_stderr_into_a_shell_is_read():
    """Gate only: `|&` is a syntax error in bash 3.2 (this Mac) and a pipe in bash 5 (CI), so no
    single bash result exists to compare. The text on the pipe is read as it is for `|`."""
    assert "a" in gate._bash_write_targets('echo "rm a" |& sh')


def test_coproc_is_read_without_raising():
    """Gate only: `coproc` is a keyword in bash 4 and later and a plain command name in bash 3.2.
    It is outside the reader's grammar, so the pass must still return from every judgment."""
    for judgment in (gate._bash_write_targets, gate._shared_tree_git_mutation):
        assert isinstance(judgment("coproc rm a"), (list, bool))
    assert gate._command_denied("coproc git push", _PUSH) in (True, False)


def test_an_extglob_pattern_in_a_case_is_one_word():
    """Gate only: `@(x|y)` in a `case` pattern needs `shopt -s extglob` before it parses, and bash 3.2
    and bash 5 differ on whether `bash -c` accepts it. The parens that touch a word belong to the
    word, and the branch's command is read."""
    assert "a" in gate._bash_write_targets("case x in @(x|y)) rm a;; esac")


# --- the lease and the directory ----------------------------------------------------------------
@pytest.mark.parametrize(("command", "shared"), [
    ("cd worktrees/solo | git commit -m x", True),
    ("cd worktrees/solo & git commit -m x", True),
    ("cd worktrees/solo; trap 'git commit -m x' EXIT; cd ../..", True),
    ("cd -- worktrees/solo && git commit -m x", False),
    ("cd -P worktrees/solo && git commit -m x", False),
    ("cd -L worktrees/solo && git commit -m x", False),
    ("(cd worktrees/solo); git commit -m x", True),
    ("cd worktrees/solo && git commit -m x", False),
    ("cd worktrees/solo && trap 'git commit -m x' EXIT", False),
])
def test_the_lease_judges_a_command_from_where_it_runs(command, shared):
    """No bash half: the directory is the gate's own reading of `cd`, `( )`, pipes, `&` and trap."""
    assert gate._shared_tree_git_mutation(command, "") is shared


def test_trap_code_names_its_write_where_it_was_set():
    assert "f1" in gate._bash_write_targets("trap 'echo a > f1' EXIT")


def test_trap_code_is_also_judged_from_a_later_directory():
    targets = gate._bash_write_targets("trap 'echo a > f1' EXIT; cd sub")
    assert "f1" in targets and "sub/f1" in targets


# --- writers past command position hold ---------------------------------------------------------
@pytest.mark.parametrize(("command", "target"), [
    ("sudo tee out.txt", "out.txt"),
    ("env sed -i s/a/b/ f.txt", "f.txt"),
    ("echo x | xargs tee g.txt", "g.txt"),
])
def test_a_writer_past_command_position_is_still_named(command, target):
    assert target in gate._bash_write_targets(command)


@pytest.mark.parametrize("command", ['echo "tee" h.txt', 'sudo "tee" h.txt'])
def test_a_quoted_writer_name_is_no_writer(command):
    assert gate._bash_write_targets(command) == []


# --- the text the legacy deny_bash patterns see -------------------------------------------------
def test_a_data_body_is_cut_from_the_deny_text():
    assert "git push" not in gate._deny_bash_text(f"cat <<EOF\n{_PUSH}\nEOF\n")


def test_a_data_body_inside_a_substitution_is_cut():
    assert "git push" not in gate._deny_bash_text(f"x=$(cat <<EOF\n{_PUSH}\nEOF\n)")


def test_a_body_a_shell_reads_is_kept():
    assert "git push" in gate._deny_bash_text(f"bash <<EOF\n{_PUSH}\nEOF")


def test_a_data_heredoc_inside_a_kept_body_is_cut():
    assert "git push" not in gate._deny_bash_text(f"bash <<A\ncat <<B\n{_PUSH}\nB\nA")


def test_an_unquoted_bodys_substitution_is_appended():
    text = gate._deny_bash_text(f"cat <<EOF\nnote $({_PUSH})\nEOF")
    assert "git push" in text and "note" not in text


def test_a_quoted_bodys_substitution_is_data():
    assert "git push" not in gate._deny_bash_text(f"cat <<'EOF'\n$({_PUSH})\nEOF")


def test_a_command_with_no_operator_is_returned_unparsed(monkeypatch):
    def _no_parse(_cmd):
        raise AssertionError("no `<<`, so nothing to parse")

    monkeypatch.setattr(gate, "_parsed", _no_parse)
    assert gate._deny_bash_text("git status && echo hi") == "git status && echo hi"


# --- the old scanners are gone, and the reader imports only `re` --------------------------------
_REMOVED = [
    "_quoted_text_mask", "_literal_text_mask", "_mask_double_quotes", "_backtick_bodies",
    "_substitution_end", "_double_quote_end", "_strip_heredocs", "_take_bodies",
    "_shell_reads_body", "_substitutions", "_strip_c_payloads", "_command_starts", "_prefix_end",
    "_case_labels", "_at_command_position", "_shell_word", "_redirect_words", "_command_args",
    "_skip_blanks", "_cwd_scopes", "_cwd_at",
    "_REEXEC_RE", "_PIPED_SHELL_RE", "_ARITHMETIC_RE", "_TEST_RE", "_PARAMETER_RE",
    "_STATEMENT_START_RE", "_CASE_TOKEN_RE", "_C_PAYLOAD_RE", "_HEREDOC_OPERATOR_RE",
    "_LINE_END_RE", "_CWD_RE", "_PREFIX_REDIRECT_RE", "_REDIRECT_RE", "_WRITER_RE",
    "_UNESCAPED_BACKTICK_RE", "_INNER_QUOTE_RE", "_KEYWORD_BEFORE_RE", "_ENV_WORD_RE",
    "_EXTGLOB_OPENERS", "_COMMAND_STARTERS", "_ESCAPABLE_OPERATORS", "_WORD_END",
]


@pytest.mark.parametrize("name", _REMOVED)
def test_the_gate_no_longer_defines_a_partial_scanner(name):
    assert not hasattr(gate, name)


def test_the_gate_reads_through_the_reader(monkeypatch):
    """Every verdict comes from the reader's tree: with the parse made to fail, each check fails."""
    def _no_parse(_cmd):
        raise AssertionError("parsed")

    monkeypatch.setattr(gate, "_parsed", _no_parse)
    for judge in (lambda c: gate._command_denied(c, _PUSH), gate._bash_write_targets,
                  lambda c: gate._shared_tree_git_mutation(c, ""), gate._deny_bash_text):
        with pytest.raises(AssertionError):
            judge("git push > fresh-reader-probe.txt <<EOF\nx\nEOF")


def test_the_reader_imports_nothing_but_re():
    """The hook's stdlib allowlist is pinned, so a new import in the reader reds it elsewhere."""
    source = Path(gate.__file__).with_name("bash_reader.py").read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"re", "__future__"}


# --- one parse per call -------------------------------------------------------------------------
def test_one_hook_call_parses_the_command_once(monkeypatch):
    command = f"{_PUSH} origin main > out.txt\ncat <<EOF\nbody\nEOF"
    calls = []
    real = bash_reader.parse

    def _counted(text, *args, **kwargs):
        calls.append(text)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(bash_reader, "parse", _counted)
    gate._parsed.cache_clear()
    gate._bash_write_targets(command)
    gate._command_denied(command, "git push")
    gate._command_denied(command, "git merge")
    gate._shared_tree_git_mutation(command, "")
    gate._deny_bash_text(command)
    assert calls.count(command) <= 1
    assert gate._parsed.cache_info().misses == 1
    gate._parsed.cache_clear()


# --- linear cost and depth ----------------------------------------------------------------------
def _best_of_three(command: str) -> float:
    best = float("inf")
    for _ in range(3):
        gate._parsed.cache_clear()
        start = time.perf_counter()
        gate._bash_write_targets(command)
        gate._command_denied(command, "git push")
        best = min(best, time.perf_counter() - start)
    return best


_MANY = "\n".join(f"cat <<E{i}\n{_PUSH}\nE{i}" for i in range(500))
_UNCLOSED = [
    "".join(f"cat <<E{i}; " for i in range(500)),
    "".join(f"cat <<E{i}\n" for i in range(500)),
]


def test_five_hundred_heredocs_cost_under_200ms():
    assert gate._command_denied(_MANY, _PUSH) is False
    assert _best_of_three(_MANY) < 0.2


@pytest.mark.parametrize("command", _UNCLOSED, ids=["one-line", "one-per-line"])
def test_five_hundred_unclosed_operators_cost_under_200ms(command):
    assert _best_of_three(command) < 0.2


def test_heredocs_nested_past_the_bound_raise():
    """`pre_tool` turns this into a refusal, as it does for nested shell code."""
    n = bash_reader.MAX_DEPTH + 2
    deep = "".join(f"bash <<E{i}\n" for i in range(n))
    deep += "".join(f"E{i}\n" for i in reversed(range(n)))
    with pytest.raises(RecursionError):
        gate._command_denied(deep, _PUSH)


# --- recovery: no input raises, and each returns from every judgment ----------------------------
_BROKEN = [
    "fi", ")", "}", "ls *(.)", "echo ${", 'echo "', "echo `", "<<", "cat <<", "case x in",
    "(( 1 +", "[[ a", "for", "", "(" * 48_000,
]


@pytest.mark.parametrize("text", _BROKEN, ids=[_id(t[:20]) or "empty" for t in _BROKEN])
def test_broken_input_raises_nothing_and_every_judgment_returns(text):
    bash_reader.parse(text)
    assert isinstance(gate._bash_write_targets(text), list)
    assert gate._command_denied(text, "git push") in (True, False)
    assert gate._command_denied(text, "git merge") in (True, False)
    assert gate._shared_tree_git_mutation(text, "") in (True, False)


def test_code_nested_past_the_bound_raises_recursion_error():
    text = 'rm "$( ' * 2000
    with pytest.raises(RecursionError):
        bash_reader.parse(text)
    with pytest.raises(RecursionError):
        gate._parsed(text)


# --- the shape of the tree ----------------------------------------------------------------------
def _first(text: str):
    return bash_reader.parse(text).pipelines[0].stages[0]


@pytest.mark.parametrize(("text", "expected", "bare"), [
    ("plain", "plain", True),
    ("'a b'", "a b", False),
    ('"a b"', "a b", False),
    ("a'b'\"c\"", "abc", True),
    ("'a'b", "ab", False),
    ("'$S'", _LITERAL_DOLLAR + "S", False),
    ('"$S"', _QUOTED_DOLLAR + "S", False),
    ("\\$S", _LITERAL_DOLLAR + "S", False),
    ("$S", "$S", True),
    ("a\\ b", "a b", True),
], ids=["plain", "single", "double", "mixed", "quote-first", "single-dollar", "double-dollar",
        "escaped-dollar", "bare-dollar", "escaped-space"])
def test_a_words_text_and_bare_flag(text, expected, bare):
    word = _first(f"echo {text}").words[1]
    assert word.text == expected
    assert word.bare is bare


def test_a_substitution_word_keeps_its_raw_text_and_holds_its_script():
    word = _first("echo $(echo hi)").words[1]
    assert word.text == "$(echo hi)"
    assert len(word.scripts) == 1
    assert _first("echo `echo hi`").words[1].scripts


def test_a_simple_command_holds_assignments_words_and_redirects():
    simple = _first("A=1 B+=2 cmd arg >out 2>&1")
    assert [(a.name, a.append) for a in simple.assignments] == [("A", False), ("B", True)]
    assert [w.text for w in simple.words] == ["cmd", "arg"]
    assert [(r.op, r.target.text if r.target else None) for r in simple.redirects] == [
        (">", "out"), (">&", None)]


def test_a_simple_command_may_hold_no_words():
    simple = _first("A=1")
    assert simple.words == [] and [a.name for a in simple.assignments] == ["A"]


def test_a_closed_unquoted_heredoc():
    text = "cat <<EOF\nbody\nEOF\n"
    redirect = _first(text).redirects[0]
    assert redirect.op == "<<"
    doc = redirect.heredoc
    assert (doc.quoted, doc.closed) == (False, True)
    assert doc.body.rstrip("\n") == "body"
    assert text[doc.span[0]:doc.span[1]] == "body\nEOF\n"


@pytest.mark.parametrize("delimiter", ["'EOF'", '"EOF"', "\\EOF"])
def test_a_quoted_delimiter_makes_the_body_data(delimiter):
    doc = _first(f"cat <<{delimiter}\n$(x)\nEOF\n").redirects[0].heredoc
    assert doc.quoted is True and doc.closed is True
    assert doc.scripts == []


def test_an_unquoted_body_holds_its_substitution_code():
    doc = _first("cat <<EOF\n$(rm a)\nEOF\n").redirects[0].heredoc
    assert doc.quoted is False and len(doc.scripts) == 1


def test_a_dash_heredoc_closes_on_a_tabbed_line():
    redirect = _first("cat <<-EOF\n\tbody\n\tEOF\n").redirects[0]
    assert redirect.op == "<<-" and redirect.heredoc.closed is True


def test_a_heredoc_that_never_closes_runs_to_the_end_and_is_read_as_code():
    text = "cat <<EOF\nrm a"
    doc = _first(text).redirects[0].heredoc
    assert doc.closed is False
    assert doc.body.rstrip("\n") == "rm a"
    assert doc.span == (text.index("rm a"), len(text))
    assert doc.scripts, "the unclosed body is also parsed as code"


def test_pipeline_stages_and_background():
    script = bash_reader.parse("a | b | c; d & e")
    assert [len(p.stages) for p in script.pipelines] == [3, 1, 1]
    assert [p.background for p in script.pipelines] == [False, True, False]


@pytest.mark.parametrize("text", ["a; b", "a\nb", "a && b", "a || b"])
def test_a_list_separator_ends_the_pipeline(text):
    assert len(bash_reader.parse(text).pipelines) == 2


@pytest.mark.parametrize(("text", "kind"), [
    ("(a)", "subshell"),
    ("{ a; }", "group"),
    ("if a; then b; fi", "if"),
    ("while a; do b; done", "while"),
    ("until a; do b; done", "until"),
    ("for i in a; do b; done", "for"),
    ("select i in a; do b; done", "select"),
    ("case x in x) a;; esac", "case"),
    ("f() { a; }", "function"),
    ("function f { a; }", "function"),
    ("[[ -n x ]]", "test"),
    ("((1+1))", "arith"),
], ids=["subshell", "group", "if", "while", "until", "for", "select", "case", "function",
        "function-keyword", "test", "arith"])
def test_a_compound_has_its_kind(text, kind):
    stage = _first(text)
    assert isinstance(stage, bash_reader.Compound)
    assert stage.kind == kind


def test_a_compound_names_its_variable_or_function_and_holds_its_list():
    loop = _first("for i in a b; do c; done")
    assert loop.name == "i" and [w.text for w in loop.words] == ["a", "b"]
    assert _first("f() { a; }").name == "f"
    assert len(loop.bodies) >= 1


def test_the_walk_visits_every_command_in_source_order_without_recursion():
    script = bash_reader.parse("x; if y; then { z; } fi; (w)")
    names = []
    depth = 0
    for event in bash_reader.iter_commands(script):
        if event[0] == "enter":
            depth += 1
        elif event[0] == "leave":
            depth -= 1
        elif event[0] == "simple":
            names.append(event[1].words[0].text)
    assert names == ["x", "y", "z", "w"] and depth == 0


def test_a_reserved_word_with_no_place_is_dropped_at_command_position():
    assert [w.text for w in _first("then git push").words] == ["git", "push"]
    assert [w.text for w in _first("echo do git push").words] == ["echo", "do", "git", "push"]


def test_an_ansi_c_escape_that_spells_a_name_stays_unread():
    """Out of reach, stated: `$'\\x72m' a` removes `a` in bash, but the escape is decoded at run
    time and the reader keeps its text, so no verb is named."""
    assert gate._bash_write_targets("$'\\x72m' a") == []


# --- two heredocs with equal bodies are told apart ----------------------------------------------
@pytest.mark.parametrize("command", [
    "bash <<A; cat <<B\nrm a\nA\nrm a\nB",
    "cat <<A; bash <<B\nrm a\nA\nrm a\nB",
])
def test_equal_heredoc_bodies_are_judged_one_by_one(tmp_path, command):
    """Bash runs `rm a` once, from the body the shell reads; the other body is data. Only the shell's
    body stays in the `deny_bash` text, so equal text in two bodies is not one verdict."""
    assert _bash_ran(tmp_path, command)[0] is True
    assert "a" in gate._bash_write_targets(command)
    assert gate._deny_bash_text(command).count("rm a") == 1


# --- the bounds fail toward reading --------------------------------------------------------------
def test_a_subscript_past_its_bound_still_runs_its_substitution():
    assert "a" in gate._bash_write_targets("a[" + "x" * 300 + "$(rm a)]=1")


def test_an_unmatched_extglob_group_past_its_bound_is_read_as_code():
    assert "a" in gate._bash_write_targets("echo *(" + "x" * 600 + ") ; rm a")
