"""Gates take a slug, and a role is never told to run what it can't run.

`gate brief <slug>` used to fail as "brief INVALID" on a healthy brief, and the brief-critic,
which has no shell, was told to run that gate on each of 12 field rounds. The gates now resolve
a slug or a path through one function, and the orchestrator owns the mechanical check.

moat-finding: fb-40d53eec7b0c
moat-finding: fb-02d01d7a2a59
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_AGENTS = _REPO / "prusik/templates/.claude/agents"
_COMMANDS = _REPO / "prusik/templates/.claude/commands"

_BRIEF = """# F

## Type
bug_fix

## Goal
Reduce checkout failures caused by transient payment errors for every customer in the store.

## Success criteria
- Checkout retries exit 0 within 5s on a transient error.
"""
_SCOPE_BROKEN = "# nothing a scope needs\n"
_PLAN_BROKEN = "# nothing a plan needs\n"


def _gate(root: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the real CLI, so argparse, cwd and the exit code are the ones an operator gets."""
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env["PYTHONPATH"] = str(_REPO)
    return subprocess.run([sys.executable, "-m", "prusik", "gate", *args], cwd=cwd or root,
                          env=env, capture_output=True, text=True, timeout=60)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".claude").mkdir()
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "feat.md").write_text(_BRIEF)
    (tmp_path / "briefs" / "broken.md").write_text("# no sections\n")
    (tmp_path / "design" / "feat").mkdir(parents=True)
    (tmp_path / "design" / "feat" / "scope.md").write_text(_SCOPE_BROKEN)
    (tmp_path / "design" / "feat" / "plan.md").write_text(_PLAN_BROKEN)
    (tmp_path / "src").mkdir()
    return tmp_path


def _is_argument_error(p: subprocess.CompletedProcess) -> bool:
    return (p.returncode == 2 and "not found" in p.stderr and "slug" in p.stderr
            and "INVALID" not in p.stdout + p.stderr and "Traceback" not in p.stderr)


# --- the resolver ---------------------------------------------------------------------

def test_brief_slug_resolves(project: Path):
    p = _gate(project, "brief", "feat")
    assert p.returncode == 0, p.stderr
    assert "brief valid" in p.stdout


def test_brief_slug_resolves_from_a_subdirectory(project: Path):
    p = _gate(project, "brief", "feat", cwd=project / "src")
    assert p.returncode == 0, p.stderr


def test_a_slug_wins_over_a_same_named_directory_in_the_cwd(project: Path):
    (project / "feat").mkdir()
    assert _gate(project, "brief", "feat").returncode == 0


@pytest.mark.parametrize("gate,slug_file", [("scope", "scope"), ("plan", "plan")])
def test_scope_and_plan_slugs_reach_their_artifact(project: Path, gate: str, slug_file: str):
    p = _gate(project, gate, "feat")
    assert p.returncode == 2
    assert f"{gate} INVALID" in p.stderr, "must have read the real file, not an argument error"
    assert f"design/feat/{slug_file}.md" in p.stderr


def test_a_real_content_failure_is_still_invalid(project: Path):
    for arg in ("broken", "briefs/broken.md"):
        p = _gate(project, "brief", arg)
        assert p.returncode == 2
        assert "brief INVALID" in p.stderr


# --- the path form must survive -------------------------------------------------------

def test_relative_path_still_validates(project: Path):
    assert _gate(project, "brief", "briefs/feat.md").returncode == 0


def test_absolute_path_still_validates(project: Path):
    assert _gate(project, "brief", str(project / "briefs/feat.md")).returncode == 0


def test_a_file_outside_the_project_still_validates(project: Path, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "x.md"
    outside.write_text(_BRIEF)
    assert _gate(project, "brief", str(outside)).returncode == 0


def test_a_bare_extensionless_file_still_validates_when_no_slug_owns_it(project: Path):
    (project / "draft").write_text(_BRIEF)
    assert _gate(project, "brief", "draft").returncode == 0


@pytest.mark.parametrize("arg", ["feat", "design/feat/plan.md"])
def test_plan_advisories_read_the_feature_in_both_forms(project: Path, arg: str, monkeypatch):
    """`feature` comes from the resolved file's directory. A slug has no parent of its own,
    so reading it off the raw argument would hand the advisories '' and they'd go quiet."""
    from prusik import blast_plan, gate, ledger, roles
    import argparse

    seen: list[str] = []
    monkeypatch.chdir(project)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr(ledger, "project_root", lambda: project)
    monkeypatch.setattr(blast_plan, "advisory", lambda f, r: seen.append(f))
    monkeypatch.setattr(roles, "advisory", lambda f, r: seen.append(f))
    gate.plan(argparse.Namespace(path=arg))
    assert seen == ["feat", "feat"]


# --- a wrong argument reads as one ----------------------------------------------------

@pytest.mark.parametrize("gate", ["brief", "scope", "plan"])
def test_unknown_slug_is_an_argument_error(project: Path, gate: str):
    assert _is_argument_error(_gate(project, gate, "no-such-feature"))


def test_missing_path_is_an_argument_error(project: Path):
    assert _is_argument_error(_gate(project, "brief", "briefs/no-such.md"))


def test_a_directory_is_an_argument_error_not_a_traceback(project: Path):
    assert _is_argument_error(_gate(project, "brief", "briefs"))


def test_a_path_that_escapes_is_not_read_as_a_slug(project: Path):
    assert _is_argument_error(_gate(project, "brief", "../feat"))


# --- no role is told to run what it can't ---------------------------------------------

_DIRECTIVE = re.compile(r"\b[Rr]un `[^`]+`")


def _frontmatter_tools(text: str) -> set[str]:
    line = next(ln for ln in text.splitlines() if ln.startswith("tools:"))
    return {t.strip() for t in line.split(":", 1)[1].split(",")}


def _shell_directives(text: str) -> list[str]:
    """Commands a role spec tells its reader to run. `prusik gate x` named as something the
    orchestrator does is prose, not a directive, so only an imperative `run` counts."""
    return [m.group(0) for m in _DIRECTIVE.finditer(text)
            if not re.search(r"orchestrator (?:runs|ran) $", text[max(0, m.start() - 20):m.start()])]


def test_the_directive_check_catches_the_shipped_defects():
    """Adversarial control: the check must see the exact sentences that shipped, or a green
    invariant below means nothing."""
    critic = ("**First — run the mechanical check, all at once.** Before assessing consistency, "
              "run `prusik gate brief briefs/{feature}.md`. It reports EVERY violation.")
    planner = "- After writing, run `prusik gate plan design/{feature}/plan.md` — validates."
    assert _shell_directives(critic) and _shell_directives(planner)
    assert not _shell_directives("The orchestrator runs `prusik gate plan {feature}` after you.")


def test_all_15_roles_are_checked():
    assert len(list(_AGENTS.glob("*.md"))) == 15


def test_no_bashless_role_is_directed_to_run_a_command():
    offenders = {}
    for spec in sorted(_AGENTS.glob("*.md")):
        text = spec.read_text()
        if "Bash" not in _frontmatter_tools(text) and (hits := _shell_directives(text)):
            offenders[spec.name] = hits
    assert not offenders


# --- the orchestrator owns the check --------------------------------------------------

@pytest.mark.parametrize("command", ["sprint-start.md", "sprint-run.md"])
def test_spawn_sites_run_the_brief_gate_before_the_critic(command: str):
    text = (_COMMANDS / command).read_text()
    check = text.index("prusik gate brief")
    spawn = text.index("brief-critic", text.index("non-zero exit", check))
    assert check < spawn
    between = text[check:spawn].lower()
    assert "stop" in between, "a failed gate must not reach a critic round"
    assert "that output in its prompt" in text[spawn - 40:spawn + 200]


def test_sprint_start_uses_the_slug_not_the_raw_arguments():
    """`/sprint-start foo --trivial` would otherwise hand the gate two arguments."""
    text = (_COMMANDS / "sprint-start.md").read_text()
    assert "prusik gate brief <feature-slug>" in text
    assert "prusik gate brief $ARGUMENTS" not in text


def test_sprint_run_runs_the_plan_gate_after_the_planner():
    text = (_COMMANDS / "sprint-run.md").read_text()
    spawn = text.index("Agent(subagent_type='feature-planner'")
    critic = text.index("Agent(subagent_type='plan-critic'", spawn)
    assert "prusik gate plan $ARGUMENTS" in text[spawn:critic]


def test_sprint_init_names_the_mechanical_check_first(project: Path, capsys, monkeypatch):
    """The real gate, stopped where it asks for a critique: its next step is the check."""
    import argparse
    from prusik import discovery, gate, ledger

    (project / ".sprint").mkdir()
    (project / ".sprint" / "dep-graph.json").write_text(
        '{"forward": {}, "reverse": {}, "stats": {"by_language": {}}}')
    (project / ".sprint" / "inventory.json").write_text("{}")
    (project / "design" / "map.md").write_text("map")
    shutil.copy(_REPO / "prusik/templates/.claude/sprint-config.yaml",
                project / ".claude" / "sprint-config.yaml")
    monkeypatch.chdir(project)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.setattr(ledger, "project_root", lambda: project)
    discovery.fingerprint_map()
    capsys.readouterr()
    assert gate.sprint_init(argparse.Namespace(feature="feat")) == 1
    out = capsys.readouterr().out
    assert out.index("prusik gate brief feat") < out.index("Invoke brief-critic")
    assert "with that output in its prompt" in out


# --- blocking and advisory ------------------------------------------------------------

def test_critique_format_separates_blocking_from_advisory():
    text = (_AGENTS / "brief-critic.md").read_text()
    assert "`blocking: <section>: <problem>`" in text
    assert "`advisory: <section>: <note>`" in text
    assert "A review with only `advisory:` items is a PASS" in text


def test_an_advisory_review_passes_the_real_verdict_gate(tmp_path: Path):
    """The severity lines sit under the verdict, and a body naming FAIL or blocking can't
    flip a PASS, because the gate reads the first non-empty line."""
    from prusik import gate
    body = ("PASS\nadvisory: Goal: could FAIL to be sharper\n"
            "advisory: Success criteria: blocking is the wrong word here\n")
    critique = tmp_path / "reports" / "f" / "brief-critique.txt"
    critique.parent.mkdir(parents=True)
    critique.write_text(body)
    config = {"pre_sprint_gates": {"brief_critique": {
        "enabled": True, "require_artifact": "reports/{feature}/brief-critique.txt",
        "must_contain": "PASS"}}}
    assert gate._check_pre_sprint_gates(config, "f", tmp_path) == []
