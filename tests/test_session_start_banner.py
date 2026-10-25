"""fb-392958c3076a — the SessionStart banner names the feedback channel.

An adopter's in-session agent reached a concrete, prusik-directed design conclusion and
parked it in scratchpad + local memory — filing it only after two operator nudges. The
SessionStart banner is the one prusik surface the main agent always reads, and it pointed at
briefs and /sprint-start but never at `prusik feedback`, so dogfooding insights that aren't
dev deliverables had no reflex path into the loop.

Adversarial cases: the advertised invocation must actually parse against the real CLI (a
banner advertising a command that doesn't run is worse than none), and the new line must not
displace the existing banner lines (incl. the template-skew banner).

moat-finding: fb-392958c3076a
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from prusik import gate, phases

_TEMPLATE_CFG = (Path(__file__).resolve().parent.parent / "prusik" / "templates" / ".claude"
                 / "sprint-config.yaml")


def _project(tmp_path, monkeypatch, *, config=True) -> Path:
    if config:
        (tmp_path / ".claude").mkdir()
        shutil.copy(_TEMPLATE_CFG, tmp_path / ".claude" / "sprint-config.yaml")
    (tmp_path / ".sprint").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def _banner(capsys) -> str:
    assert gate.session_start() == 0
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def _feedback_line(ctx: str) -> str:
    lines = [ln for ln in ctx.splitlines() if "prusik feedback" in ln]
    assert len(lines) == 1, ctx
    return lines[0]


def test_no_active_sprint_banner_names_feedback_and_keeps_guidance(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    ctx = _banner(capsys)
    assert ctx.splitlines()[0] == ("[prusik] No active sprint. Write briefs/<feature>.md "
                                   "and use /sprint-start.")
    assert _feedback_line(ctx).startswith("[prusik] ")


def test_active_sprint_banner_names_feedback_and_keeps_state(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    phases.set_sprint_state({"phase": "building", "feature": "feat"})
    ctx = _banner(capsys)
    assert ctx.splitlines()[0] == ("[prusik] Active sprint 'feat' in phase 'building'. "
                                   "Run /sprint-status for details.")
    _feedback_line(ctx)


def test_skew_banner_and_feedback_line_coexist(tmp_path, monkeypatch, capsys):
    from prusik import refresh
    _project(tmp_path, monkeypatch)
    monkeypatch.setattr(refresh, "skew_banner", lambda root: "TEMPLATES SKEWED — restart")
    ctx = _banner(capsys)
    lines = ctx.splitlines()
    assert lines[0] == "[prusik] TEMPLATES SKEWED — restart"        # still first
    assert lines[1].startswith("[prusik] No active sprint.")
    _feedback_line(ctx)


def test_no_sprint_config_emits_nothing(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, config=False)
    assert gate.session_start() == 0
    assert capsys.readouterr().out == ""


def test_advertised_invocation_parses_against_the_real_cli(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    line = _feedback_line(_banner(capsys))
    m = re.search(r"`(prusik feedback [^`]+)`", line)
    assert m, f"banner must quote a copyable invocation: {line}"
    argv = shlex.split(m.group(1))
    kinds = re.search(r"--kind (\S+)", m.group(1))
    assert kinds, "invocation must show the --kind choices"
    for kind in kinds.group(1).split("|"):
        concrete = [kind if a == kinds.group(1) else a for a in argv[1:]]
        # `--help` after the args: argparse validates every earlier arg (an unknown flag or
        # bad --kind choice exits 2), then exits 0 BEFORE the handler — nothing is filed.
        proc = subprocess.run([sys.executable, "-m", "prusik", *concrete, "--help"],
                              capture_output=True, text=True, cwd=tmp_path, check=False)
        assert proc.returncode == 0, (kind, proc.stderr)
    assert not (tmp_path / ".sprint" / "feedback.jsonl").exists()
