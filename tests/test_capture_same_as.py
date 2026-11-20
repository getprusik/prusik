"""fb-4c17172a6c89: capture reuse keys on byte-exact command text. regression-sentinel
retyped the `solo_execute` pytest command with a trailing ` .`, missed the green entry at the
same tree and exec dir, and re-ran the full suite (478s, same result). Normalising commands is
unsafe, so `--same-as Q` makes Q's recorded entry the command's single source. Capture then
reuses that entry at the same tree, or runs that exact command when the tree has changed.

moat-finding: fb-4c17172a6c89
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from prusik import gate, schema

FEATURE = "feat"
RUN = "echo r >> run.log; echo '3 passed'"
OTHER = "echo o >> run.log; echo '2 passed'"
SPEC = Path(__file__).resolve().parent.parent / "prusik/templates/.claude/agents/regression-sentinel.md"


def _git(cwd: Path, *a: str) -> None:
    subprocess.run(["git", *a], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / ".sprint").mkdir()
    (r / "sub").mkdir()
    (r / ".gitignore").write_text("*.log\n.sprint/\nreports/\n")
    (r / "src.py").write_text("x = 1\n")
    (r / "sub" / "keep.py").write_text("y = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "init")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(r))
    return r


def _capture(phase: str, cmd: str | None = None, same_as: str | None = None,
             kind: str = "tests") -> int:
    return gate.capture(SimpleNamespace(command=[cmd] if cmd else [], reset=False,
                                        feature=FEATURE, phase=phase, kind=kind,
                                        same_as=same_as))


def _ev(root: Path, phase: str) -> Path:
    return schema.evidence_path_for(root / "reports" / FEATURE, phase)


def _runs(root: Path, where: str = ".") -> int:
    p = root / where / "run.log"
    return len(p.read_text().splitlines()) if p.exists() else 0


def test_same_tree_reuses_the_recorded_command(root, capsys):
    assert _capture("solo_execute", RUN) == 0
    assert _capture("regression", same_as="solo_execute") == 0
    assert _runs(root) == 1                                   # nothing re-ran
    (entry,) = schema.load_evidence(_ev(root, "regression"))
    assert entry["command"] == RUN
    assert entry["reused_from"] == {"phase": "solo_execute"}
    assert "--same-as solo_execute" in capsys.readouterr().err


def test_changed_tree_runs_the_recorded_command(root):
    assert _capture("solo_execute", RUN) == 0
    (root / "src.py").write_text("x = 2\n")
    assert _capture("regression", same_as="solo_execute") == 0
    assert _runs(root) == 2
    (entry,) = schema.load_evidence(_ev(root, "regression"))
    assert entry["command"] == RUN and "reused_from" not in entry
    assert entry["phase"] == "regression"


def test_nothing_recorded_refuses(root, capsys):
    assert _capture("regression", same_as="solo_execute") == 2
    assert not _ev(root, "regression").exists()
    assert "no green --kind tests" in capsys.readouterr().err


def test_red_only_source_refuses(root, capsys):
    assert _capture("solo_execute", "echo r >> run.log; exit 1") == 1
    assert _capture("regression", same_as="solo_execute") == 2
    assert _runs(root) == 1
    assert not _ev(root, "regression").exists()


def test_other_kind_only_refuses_and_names_what_is_held(root, capsys):
    assert _capture("solo_execute", RUN) == 0
    capsys.readouterr()
    assert _capture("regression", same_as="solo_execute", kind="lint") == 2
    err = capsys.readouterr().err
    assert "no green --kind lint" in err and "green --kind tests in ." in err
    assert _runs(root) == 1 and not _ev(root, "regression").exists()


def test_several_distinct_commands_refuse_and_list_them(root, capsys):
    assert _capture("solo_execute", RUN) == 0
    assert _capture("solo_execute", OTHER) == 0
    capsys.readouterr()
    assert _capture("regression", same_as="solo_execute") == 2
    err = capsys.readouterr().err
    assert RUN in err and OTHER in err and "drop --same-as" in err
    assert _runs(root) == 2 and not _ev(root, "regression").exists()


def test_typed_command_with_same_as_refuses(root, capsys):
    assert _capture("solo_execute", RUN) == 0
    assert _capture("regression", RUN, same_as="solo_execute") == 2
    assert _runs(root) == 1 and not _ev(root, "regression").exists()
    assert "drop --same-as" in capsys.readouterr().err


def test_entry_from_another_exec_dir_is_not_borrowed(root, monkeypatch, capsys):
    # Adversarial: Q's only green entry ran in sub/. From the root, --same-as must refuse,
    # not run that command here against a different directory.
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(root / "sub"))
    assert _capture("solo_execute", RUN) == 0
    assert _runs(root, "sub") == 1
    monkeypatch.setattr(gate.os, "getcwd", lambda: str(root))
    assert _capture("regression", same_as="solo_execute") == 2
    assert _runs(root) == 0 and _runs(root, "sub") == 1
    assert not _ev(root, "regression").exists()
    assert "sub" in capsys.readouterr().err


def test_same_phase_source_repeats_without_re_running(root):
    assert _capture("regression", RUN) == 0
    before = _ev(root, "regression").read_bytes()
    assert _capture("regression", same_as="regression") == 0
    assert _runs(root) == 1 and _ev(root, "regression").read_bytes() == before


def test_sentinel_spec_names_same_as():
    assert "--same-as solo_execute" in SPEC.read_text()
