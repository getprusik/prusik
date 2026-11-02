"""The bash gate finds a file verb wherever bash runs one, finishes in linear time on the inputs
measured slow, and refuses a call it could not judge. Real bash and a real hook process are the
oracles: a write case must change the file before the gate is held to naming it.

moat-finding: fb-961eaed62314
moat-finding: fb-860c63485143
moat-finding: fb-d325f0be82a2
moat-finding: fb-a9456cbd1f88
"""

from __future__ import annotations

import _signal
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from prusik import gate, gate_class, ledger

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG = _ROOT / "prusik" / "templates" / ".claude" / "sprint-config.yaml"
_DEEP = "rm " + '"$( ' * 2000
_DENY_KEY = "hookSpecificOutput"


def _targets(cmd, start=""):
    return gate._bash_write_targets(gate._strip_heredocs(cmd), start)


@pytest.mark.parametrize("cmd", [
    ">o rm a", "2>/dev/null rm a", "<in rm a", "&>o rm a", ">>o rm a", ">|o rm a",
    "2>&1 rm a", "1>&2 rm a", "3>&- rm a", "<&0 rm a", "<<<w rm a", "2>e 1>f rm a",
    "X=1 >o rm a", ">o X=1 rm a", "X=1 2>/dev/null Y=2 rm a", "<in X=1 2>&1 rm a",
    "X[0]=1 rm a", "X[0]+=1 rm a", "X[a b]=1 rm a", "X= rm a", "X=1 Y[2]=3 2>e rm a",
    "<<EOF rm a\nbody\nEOF", "<<-EOF rm a\n\tbody\n\tEOF", "cat <<EOF; rm a\nbody\nEOF",
    "cat <<EOF >/dev/null && rm a\nbody\nEOF", "cat <<A <<B; rm a\nx\nA\ny\nB",
    "cat <<'E' | cat; rm a\n>body\nE", "cat <<<EOF\nrm a\nEOF",
    "case x in x) rm a;; esac", "case x in (x) rm a;; esac", "case x in y|x) rm a;; esac",
    "case x in\n  x)\n    rm a\n    ;;\nesac",
    "case x in y) echo no;; x) rm a;; esac", "case x in x) case y in y) rm a;; esac;; esac",
    "case x in x) >o rm a;; esac", "case x in x) true; rm a;; esac",
    'case "$1" in *) rm a;; esac',
    "if\\\n rm a; then :; fi", "while\\\n rm a; do break; done",
    "until\\\n ! rm a; do :; done", "! \\\n rm a", "if true; then\\\n rm a; fi",
    "if false; then :; else\\\n rm a; fi", "if false; then :; elif\\\n rm a; then :; fi",
    "for i in 1; do\\\n rm a; done", "while\\\n rm a\ndo break; done",
])
def test_a_verb_bash_runs_is_named(tmp_path, cmd):
    (tmp_path / "a").write_text("x")
    (tmp_path / "in").write_text("x")
    subprocess.run(["bash", "-c", cmd], cwd=tmp_path, capture_output=True, check=False)
    assert not (tmp_path / "a").exists(), "bash did not run the verb, so this case proves nothing"
    assert "a" in _targets(cmd)


@pytest.mark.parametrize("cmd", ["{fd}>o rm a", "case x in @(x|y)) rm a;; esac",
                                 "case x in x) rm a;& y) true;; esac"])
def test_a_verb_only_newer_bash_runs_is_named(cmd):
    # macOS ships bash 3.2, with no `{fd}>` or `;&`, and extglob needs a shopt. Linux CI runs them.
    assert "a" in _targets(cmd)


@pytest.mark.parametrize(("cmd", "expected"), [
    ("cat <<EOF > out\nbody\nEOF", {"out"}),
    ("cat <<EOF >> out\nbody\nEOF\nrm b", {"out", "b"}),
    ("cat <<EOF | tee out\nbody\nEOF", {"out"}),
    ("cat <<'EOF' > out\n$body > not-a-target\nEOF", {"out"}),
    ("cat <<A > x\nrm no\nA\ncat <<B > y\nrm no2\nB", {"x", "y"}),
    ("cat <<A <<B > out\nrm no\nA\nrm no2\nB", {"out"}),
])
def test_only_a_heredoc_body_is_data(tmp_path, cmd, expected):
    (tmp_path / "b").write_text("x")
    subprocess.run(["bash", "-c", cmd], cwd=tmp_path, capture_output=True, check=False)
    assert not (tmp_path / "no").exists()
    assert set(_targets(cmd)) == expected


def test_a_heredoc_in_a_substitution_keeps_its_prose_out():
    cmd = """git commit -m "$(cat <<'EOF'\nrm prose > nowhere\nEOF\n)\""""
    assert _targets(cmd) == []


def test_a_command_hidden_on_the_opening_line_is_denied():
    assert gate._command_denied(gate._strip_heredocs("cat <<EOF; git push\nbody\nEOF"),
                                "git push")


@pytest.mark.parametrize("cmd", [
    "echo $(x) rm a", "echo `x` rm a", "echo rm a", "echo >o2 rm a", "echo case x in x) rm a",
    "echo 'case x in x) rm a;; esac'", "'>o rm a'", '">o rm a"', "# >o rm a", "git rm a",
    ">o git rm a", "X[0]=1 git rm a", "case x in rm) echo a;; esac",
    "case x in x) echo rm a;; esac", "case x in x) git rm a;; esac", "(echo) ; echo rm a",
])
def test_a_verb_bash_does_not_run_is_not_named(cmd):
    assert "a" not in _targets(cmd)


def test_a_redirect_in_front_is_still_a_target():
    assert set(_targets(">o rm a")) == {"o", "a"}


def test_an_arm_past_a_substitution_in_code_stays_in_its_arm():
    assert _targets("case x in x) echo $(y) rm a; rm b;; z) rm c;; esac") == ["b", "c"]


def test_the_word_esac_as_an_argument_closes_nothing():
    assert _targets("case x in x) echo esac; rm a;; y) rm b;; esac") == ["a", "b"]


def test_a_case_label_opens_no_subshell(tmp_path):
    cmd = "(case x in x) cd d;; esac; touch a); touch b"
    (tmp_path / "d").mkdir()
    subprocess.run(["bash", "-c", cmd], cwd=tmp_path, check=True)
    assert (tmp_path / "d" / "a").exists() and (tmp_path / "b").exists()
    assert _targets(cmd) == ["d/a", "b"]


@pytest.mark.parametrize(("cmd", "expected"), [
    ("case x in x) cd d; rm a;; esac", "d/a"),
    ("until\\\n cd d; rm a; do :; done", "d/a"),
    (">o cd d; rm a", "d/a"),
])
def test_cd_is_read_at_every_statement_start(cmd, expected):
    assert expected in _targets(cmd)


def test_cd_as_an_argument_moves_nothing():
    assert _targets("echo cd d; rm a") == ["a"]


def test_an_assignment_in_a_subshell_case_arm_does_not_resolve():
    assert _targets('(case x in x) S=a;; esac); cp b "$S/c"') == ["$S/c"]


def test_an_assignment_in_a_case_arm_counts_as_taken():
    assert _targets('case x in x) S=a;; esac; cp b "$S/c"') == ["a/c"]


def test_an_assignment_after_a_redirect_binds():
    assert _targets('>o S=a; cp b "$S/c"') == ["o", "a/c"]


@pytest.mark.parametrize(("cmd", "expected"), [
    ("S=-t; cp $S design a", ["design/a"]),
    ('S=-t; cp "$S" design a', ["design/a"]),
    ("S=d/; cp a $S", ["d/a"]),
    ("S=-r; rm $S a", ["a"]),
    ("S=of=f; dd if=a $S", ["f"]),
    ("S=x; mv a $S", ["a", "x"]),
])
def test_a_resolved_value_is_read_as_the_word_it_is(cmd, expected):
    assert _targets(cmd) == expected


def test_a_flag_held_in_a_variable_really_moves_the_target(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f").write_text("x")
    cmd = "S=-R; cp $S a b"
    subprocess.run(["bash", "-c", cmd], cwd=tmp_path, check=True)
    assert (tmp_path / "b" / "f").exists()
    assert _targets(cmd) == ["b"]


@pytest.mark.parametrize("cmd", [
    'S="-t d"; cp $S a', 'S="a b"; rm $S', 'S="*"; rm $S', 'S="x y"; touch $S/z',
])
def test_a_value_bash_would_split_fails_closed(cmd):
    assert any(gate._is_unresolved(t) for t in _targets(cmd))


def test_a_quoted_value_does_not_split():
    assert _targets('S="a b"; rm "$S"') == ["a b"]


def test_an_unbound_operand_stays_one_operand():
    assert _targets("cp $src dest") == ["dest"]


def test_a_literal_dollar_is_not_expanded_again():
    assert _targets("S=x; rm '$S'") == ["$S"]


@pytest.mark.parametrize("cmd", [
    "S=a; S[" + "k" * 300 + ']=b; cp x "$S/c"',
    "S=a; S[" + "k" * 256 + ']=b; cp x "$S/c"',
])
def test_a_subscript_of_any_length_mutates_its_name(cmd):
    assert _targets(cmd) == ["$S/c"]


def test_an_unclosed_subscript_past_the_bound_counts_as_a_mutation():
    assert _targets('S=a; S[' + "k" * 300 + ' ; cp x "$S/c"') == ["$S/c"]


@pytest.mark.parametrize("cmd", [
    "x " + "/" * 48_000, "x " + "~" * 48_000, "x " + "./" * 24_000,
    "S=1; rm $S " + "a[ " * 16_000, "rm x; " + "python " * 8_000,
    "rm x; " + "bash -c " * 7_000,
])
def test_a_long_command_stays_linear(cmd):
    begin = time.perf_counter()
    gate._may_write(cmd)
    _targets(cmd)
    assert time.perf_counter() - begin < 1.0


def test_many_operands_stay_linear():
    for cmd in ("rm " + " ".join(f"f{i}.txt" for i in range(16_000)),
                'S="a b"; cp ' + "$S " * 24_000 + "d"):
        begin = time.perf_counter()
        _targets(cmd)
        assert time.perf_counter() - begin < 1.0


def test_interpreter_payloads_are_still_stripped():
    assert _targets("""python -c "x > y" > out""") == ["out"]
    assert _targets("""python -u -X dev -c 'a > b'""") == []


def test_a_path_to_the_verb_is_still_read():
    assert _targets("/bin/rm a") == ["a"]
    assert _targets("./rm a") == ["a"]
    assert _targets("~/bin/rm a") == ["a"]
    assert _targets("//bin//rm a") == ["a"]


def _project(path: Path) -> Path:
    claude = path / ".claude"
    claude.mkdir()
    shutil.copy(_CONFIG, claude / "sprint-config.yaml")
    return path


def _hook(path: Path, payload: dict, code: str | None = None):
    env = {**os.environ, "PYTHONPATH": str(_ROOT)}
    argv = ([sys.executable, "-c", code] if code
            else [sys.executable, "-m", "prusik", "gate", "pre-tool"])
    return subprocess.run(argv, cwd=path, input=json.dumps(payload), capture_output=True,
                          text=True, env=env, check=False, timeout=60)


def _bash(command: str, path: Path) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "session_id": "s",
            "cwd": str(path)}


def _denied(result) -> str:
    assert result.returncode == 0, result.stderr[-500:]
    return json.loads(result.stdout)[_DENY_KEY]["permissionDecisionReason"]


def _records(path: Path) -> list[dict]:
    log = path / ".sprint" / "ledger.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_a_command_too_deep_to_read_is_denied_by_the_real_hook(tmp_path):
    root = _project(tmp_path)
    reason = _denied(_hook(root, _bash(_DEEP, root)))
    assert "RecursionError" in reason and "Nothing ran" in reason
    blocked = [r for r in _records(root) if r["event"] == "gate_blocked"]
    assert [r["gate_class"] for r in blocked] == [gate_class.GATE_FAULT]
    assert gate_class.classify(blocked[0]) == gate_class.GATE_FAULT


def test_a_routine_call_is_not_denied_by_the_real_hook(tmp_path):
    root = _project(tmp_path)
    result = _hook(root, _bash("echo hello", root))
    assert (result.returncode, result.stdout) == (0, "")


def test_a_repo_without_prusik_is_left_alone_even_for_a_deep_command(tmp_path):
    result = _hook(tmp_path, _bash(_DEEP, tmp_path))
    assert (result.returncode, result.stdout) == (0, "")


def test_a_tool_the_gate_has_no_event_for_is_left_alone(tmp_path):
    root = _project(tmp_path)
    result = _hook(root, {"tool_name": "Read", "tool_input": {"file_path": "x"}})
    assert (result.returncode, result.stdout) == (0, "")


_SLOW = """
import re, sys
from prusik import gate
gate._GATE_DEADLINE_SECONDS = 0.2
def slow(event):
    re.search(r"(a|a)*b", "a" * 40)
    raise SystemExit("the regex finished")
gate._pre_tool_decision = slow
sys.exit(gate.pre_tool())
"""

_SWALLOWED = """
import sys, time
from prusik import gate
gate._GATE_DEADLINE_SECONDS = 0.2
def guarded(event):
    try:
        time.sleep(30)
    except Exception:
        pass
    return None
gate._pre_tool_decision = guarded
sys.exit(gate.pre_tool())
"""


@pytest.mark.parametrize("code", [_SLOW, _SWALLOWED], ids=["inside-a-regex", "guarded-step"])
def test_a_gate_out_of_time_denies_before_the_host_kills_it(tmp_path, code):
    root = _project(tmp_path)
    begin = time.perf_counter()
    reason = _denied(_hook(root, _bash("echo hello", root), code))
    assert time.perf_counter() - begin < 10
    assert "_GateDeadline" in reason


def test_the_deadline_sits_inside_the_hook_timeout_every_config_ships():
    settings = json.loads((_ROOT / "prusik/templates/.claude/settings.json").read_text())
    hooks = settings["hooks"]["PreToolUse"]
    timeouts = [h["timeout"] for group in hooks for h in group["hooks"]
                if h["command"] == "prusik gate pre-tool"]
    assert timeouts and gate._GATE_DEADLINE_SECONDS < min(timeouts)


def test_this_platform_has_the_timer():
    assert hasattr(_signal, "setitimer")


class _Adapter:
    def __init__(self):
        self.denied = None

    def read_payload(self):
        return {}

    def parse_event(self, _payload):
        return type("Event", (), {"tool": "Bash", "command": "x"})()

    def deny(self, reason):
        self.denied = reason
        return 0


@pytest.fixture
def adapter(monkeypatch):
    fake = _Adapter()
    monkeypatch.setattr("prusik.platform_adapter.get_adapter", lambda: fake)
    return fake


def test_the_deadline_is_cleared_and_the_handler_restored(monkeypatch, adapter):
    monkeypatch.setattr(gate, "_pre_tool_decision", lambda _event: None)
    before = _signal.getsignal(_signal.SIGALRM)
    assert gate.pre_tool() == 0
    assert _signal.getitimer(_signal.ITIMER_REAL) == (0.0, 0.0)
    assert _signal.getsignal(_signal.SIGALRM) is before
    assert adapter.denied is None


def test_a_fault_clears_the_deadline_too(monkeypatch, adapter):
    def boom(_event):
        raise ValueError("bad")

    monkeypatch.setattr(gate, "_pre_tool_decision", boom)
    monkeypatch.setattr(ledger, "append", lambda *a, **k: None)
    gate.pre_tool()
    assert _signal.getitimer(_signal.ITIMER_REAL) == (0.0, 0.0)
    assert "ValueError: bad" in adapter.denied


def test_a_damaged_config_is_refused_and_the_message_names_the_remedy(tmp_path):
    root = _project(tmp_path)
    (root / ".claude" / "sprint-config.yaml").write_text("key: [unclosed\n")
    reason = _denied(_hook(root, _bash("echo hello", root)))
    assert "ParserError" in reason and ".claude/sprint-config.yaml" in reason
    assert "operator repairs it" in reason


def test_a_ledger_that_cannot_be_written_still_denies(monkeypatch, adapter):
    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(gate, "_pre_tool_decision", lambda _event: boom())
    monkeypatch.setattr(ledger, "append", boom)
    gate.pre_tool()
    assert "disk full" in adapter.denied


def test_a_platform_with_no_timer_still_decides(monkeypatch):
    monkeypatch.delattr(_signal, "setitimer")
    monkeypatch.setattr(gate, "_pre_tool_decision", lambda _event: "denied")
    assert gate._decide_in_time(object()) == "denied"
