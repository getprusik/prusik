"""A logged deviation credits the one out-of-boundary file it names.

moat-finding: fb-82127b9f9ca9
"""

from __future__ import annotations

import pytest

from prusik import consistency


def _project(root, log, built, modules=("api/q.py",)):
    (root / "design" / "f").mkdir(parents=True)
    (root / "worktrees" / "solo").mkdir(parents=True)
    bullets = "\n".join(f"- `{m}` — x" for m in modules)
    (root / "design" / "f" / "scope.md").write_text(f"## Modules touched\n{bullets}\n")
    (root / "design" / "f" / "deviations.md").write_text(log)
    for rel in built:
        _write(root / "worktrees" / "solo" / rel)


def _write(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n")


def _flagged(root):
    return "\n".join(consistency.builder_writes_within_plan(root, "f"))


@pytest.mark.parametrize("token,path", [
    ("errors/E.ts", "src/errors/E.ts"),
    ("src/errors/E.ts", "src/errors/E.ts"),
    ("E.ts", "src/errors/E.ts"),
    (".env.test", "packages/backend/.env.test"),
    ("./web/x.py", "web/x.py"),
    ("worktrees/solo/web/x.py", "web/x.py"),
    ("worktrees/other-role/web/x.py", "web/x.py"),
])
def test_a_token_credits_the_file_it_names(tmp_path, token, path):
    _project(tmp_path, f"DEV-001: {token} — moved\n", [path])
    assert _flagged(tmp_path) == ""


def test_an_absolute_path_under_the_project_credits(tmp_path):
    _project(tmp_path, f"DEV-001: {tmp_path}/web/x.py — moved\n", ["web/x.py"])
    assert _flagged(tmp_path) == ""


@pytest.mark.parametrize("token,path", [
    ("api/util.py", "web/util.py"),
    ("x.py", "ax.py"),
    ("lib/ax.py", "x.py"),
    ("myproj/web/x.py", "web/x.py"),              # longer than the path
    ("/elsewhere/web/x.py", "web/x.py"),
])
def test_a_token_credits_nothing_else(tmp_path, token, path):
    _project(tmp_path, f"DEV-001: {token} — moved\n", [path])
    assert path in _flagged(tmp_path)


@pytest.mark.parametrize("token", ["{root}/x.py", "worktrees/solo/x.py"])
def test_an_anchored_token_names_only_that_path(tmp_path, token):
    _project(tmp_path, f"DEV-001: {token.format(root=tmp_path)} — moved\n", ["src/x.py"])
    assert "src/x.py" in _flagged(tmp_path)


@pytest.mark.parametrize("token", ["{root}/x.py", "worktrees/solo/x.py"])
def test_an_anchored_token_credits_its_root_file(tmp_path, token):
    _project(tmp_path, f"DEV-001: {token.format(root=tmp_path)} — moved\n", ["x.py", "src/x.py"])
    flagged = _flagged(tmp_path)
    assert "solo/x.py" not in flagged
    assert "src/x.py" in flagged


def test_an_absolute_worktree_path_credits(tmp_path):
    _project(tmp_path, f"DEV-001: {tmp_path}/worktrees/solo/web/x.py — moved\n", ["web/x.py"])
    assert _flagged(tmp_path) == ""


def test_a_real_worktrees_directory_keeps_its_written_path(tmp_path):
    _project(tmp_path, "DEV-001: worktrees/a/x.py — moved\n",
             ["worktrees/a/x.py", "worktrees/b/x.py"])
    flagged = _flagged(tmp_path)
    assert "solo/worktrees/a/x.py" not in flagged
    assert "solo/worktrees/b/x.py" in flagged


def test_a_file_with_no_token_is_a_violation(tmp_path):
    _project(tmp_path, "DEV-001: web/x.py — moved\n", ["web/x.py", "web/y.py"])
    assert "web/y.py" in _flagged(tmp_path)
    assert "web/x.py" not in _flagged(tmp_path)


@pytest.mark.parametrize("token,built", [
    ("util.py", ["api/v2/util.py", "web/util.py"]),
    ("a/x.py", ["one/a/x.py", "two/a/x.py"]),
])
def test_a_token_naming_several_credits_none(tmp_path, token, built):
    _project(tmp_path, f"DEV-001: {token} — moved\n", built)
    flagged = _flagged(tmp_path)
    assert all(path in flagged for path in built)


def test_the_refusal_names_the_token_and_the_remedy(tmp_path):
    _project(tmp_path, "DEV-001: util.py — moved\n", ["a/util.py", "b/util.py"])
    flagged = _flagged(tmp_path)
    assert "`util.py` names several files" in flagged
    assert "log the full path" in flagged


def test_a_plain_violation_carries_no_ambiguity_hint(tmp_path):
    _project(tmp_path, "DEV-001: other.py — moved\n", ["a/util.py"])
    assert "names several files" not in _flagged(tmp_path)


def test_a_full_path_token_credits_through_an_ambiguous_bare_one(tmp_path):
    _project(tmp_path, "DEV-001: util.py, a/util.py — moved\n", ["a/util.py", "b/util.py"])
    flagged = _flagged(tmp_path)
    assert "a/util.py" not in flagged
    assert "b/util.py" in flagged


def test_a_root_file_can_always_be_logged(tmp_path):
    _project(tmp_path, "DEV-001: package.json, packages/b/package.json — x\n",
             ["package.json", "packages/b/package.json", "packages/c/package.json"])
    flagged = _flagged(tmp_path)
    assert "solo/package.json" not in flagged
    assert "packages/b/package.json" not in flagged
    assert "packages/c/package.json" in flagged


def test_a_file_inside_the_boundary_never_makes_a_name_ambiguous(tmp_path):
    _project(tmp_path, "DEV-001: util.py — moved\n", ["api/util.py", "web/util.py"],
             modules=("api/",))
    assert _flagged(tmp_path) == ""


def test_one_path_in_two_worktrees_is_one_file(tmp_path):
    _project(tmp_path, "DEV-001: util.py — moved\n", ["web/util.py"])
    _write(tmp_path / "worktrees" / "second" / "web" / "util.py")
    assert _flagged(tmp_path) == ""


def test_the_count_spans_every_worktree(tmp_path):
    _project(tmp_path, "DEV-001: util.py — moved\n", ["a/util.py"])
    _write(tmp_path / "worktrees" / "second" / "b" / "util.py")
    flagged = _flagged(tmp_path)
    assert "solo/a/util.py" in flagged and "second/b/util.py" in flagged


@pytest.mark.parametrize("rel", [
    "tests/test_util.py", "pkg/tests/util.py", "design/f/util.py", ".sprint/status/util.py",
    "uv.lock",
])
def test_a_carve_out_never_makes_a_name_ambiguous(tmp_path, rel):
    _project(tmp_path, "DEV-001: util.py uv.lock — moved\n", ["web/util.py", rel])
    assert _flagged(tmp_path) == ""


def test_no_log_changes_nothing(tmp_path):
    _project(tmp_path, "", ["web/x.py"])
    (tmp_path / "design" / "f" / "deviations.md").unlink()
    assert "web/x.py" in _flagged(tmp_path)
