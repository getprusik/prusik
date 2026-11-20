"""brief-lint catches a criterion that bumps the project's version pin, at scoping.

The release owns the version bump (scripts/release.sh, `prusik refresh`); a sprint that
changes the pin is refused at the solo_execute→reviewing pin gate. A brief asking for it used
to pass brief-lint and the brief-critic and fail only after the build (fb-944e75f3e6e6).
moat-finding: fb-944e75f3e6e6
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prusik import brief_lint, pin

BRIEF = """# A brief

## Type
bug_fix

## Goal
Fix the thing so that it works for every caller, measured by the tests below.

## Success criteria
{line}
- The thing works (100% of the cases in tests/test_thing.py pass).
"""


def _source_project(root: Path) -> Path:
    (root / "prusik").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "prusik"\n')
    (root / "prusik" / "__init__.py").write_text('__version__ = "0.260.1"\n')
    return root


def _manifest_project(root: Path) -> Path:
    (root / ".claude").mkdir(parents=True)
    (root / pin.MANIFEST_REL).write_text(json.dumps({"template_surface_version": "0.260.1"}))
    return root


def _issues(root: Path, line: str) -> list[str]:
    return brief_lint._release_owned_issues(BRIEF.format(line=line), root)


def test_a_criterion_bumping_the_source_version_is_flagged(tmp_path, capsys):
    root = _source_project(tmp_path)
    line = "- Release content: `__version__` 0.261.0 in prusik/__init__.py."
    issues = _issues(root, line)
    assert len(issues) == 1
    assert pin.RELEASE_OWNS_BUMP in issues[0]
    assert "scripts/release.sh" in issues[0] and "prusik refresh" in issues[0]
    # Counted: brief-lint exits 1 on it.
    (root / "briefs").mkdir()
    brief = root / "briefs" / "b.md"
    brief.write_text(BRIEF.format(line=line))
    assert brief_lint.lint(brief, root=root) == 1
    assert "[release-owned]" in capsys.readouterr().out


def test_a_changelog_version_is_not_flagged(tmp_path):
    root = _source_project(tmp_path)
    assert _issues(root, "- CHANGELOG has a 0.261.0 entry naming the fix.") == []


def test_a_version_mention_with_no_version_is_not_flagged(tmp_path):
    root = _source_project(tmp_path)
    assert _issues(root, "- `__version__` is left to the release.") == []


def test_a_criterion_bumping_the_manifest_pin_is_flagged(tmp_path):
    root = _manifest_project(tmp_path)
    issues = _issues(root, f"- {pin.MANIFEST_REL.as_posix()} moves to 0.261.0.")
    assert len(issues) == 1 and pin.RELEASE_OWNS_BUMP in issues[0]
    # Without a source pin, `__version__` names nothing here.
    assert _issues(root, "- `__version__` 0.261.0") == []


def test_no_pin_flags_nothing(tmp_path):
    assert _issues(tmp_path, "- `__version__` 0.261.0") == []


def test_the_brief_critic_spec_names_the_rule():
    spec = (Path(brief_lint.__file__).parent / "templates" / ".claude" / "agents"
            / "brief-critic.md").read_text()
    assert "the release owns the version bump" in spec
    assert "[release-owned]" in spec


@pytest.mark.parametrize("moves", [[{"where": "branch", "file": "prusik/__init__.py",
                                     "from": "0.1.0", "to": "0.2.0", "revert": "git ..."}]])
def test_the_pin_gate_and_lint_share_one_wording(moves):
    assert pin.moves_message(moves).startswith(
        f"sprint changes the version pin; {pin.RELEASE_OWNS_BUMP}.")
