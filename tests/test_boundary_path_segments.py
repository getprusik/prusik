"""A declared module admits itself and what lies under it, by path segment.

moat-finding: fb-cbaf583f3925
"""

from __future__ import annotations

import pytest

from prusik import consistency, schema


def _scope(root, *bullets):
    path = root / "design" / "f" / "scope.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("## Modules touched\n" + "".join(f"- {b}\n" for b in bullets))
    return path


def _built(root, rel):
    path = root / "worktrees" / "solo" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n")


def _flagged(root, rel):
    return rel in "\n".join(consistency.builder_writes_within_plan(root, "f"))


@pytest.mark.parametrize("module, path", [
    ("api/x", "api/x"),
    ("api/x", "api/x/y.py"),
    ("api/x/", "api/x/y.py"),
    ("./api/x", "api/x/y.py"),
    ("api//x", "api/x/y.py"),
    ("Makefile", "Makefile"),
    (".gitignore", ".gitignore"),
    (".github/", ".github/workflows/ci.yml"),
])
def test_a_module_covers_itself_and_what_lies_under_it(module, path):
    assert schema.module_covers(module, path)


@pytest.mark.parametrize("module, path", [
    ("api/x", "api/xyz.py"),
    ("api/x.py", "api/x.py.bak"),
    ("a", "alembic/env.py"),
    ("api/x/", "api/x.py"),
    ("api/x/y.py", "api/x"),
    (".git", ".github/workflows/ci.yml"),
])
def test_a_shared_string_prefix_is_not_coverage(module, path):
    assert not schema.module_covers(module, path)


@pytest.mark.parametrize("module", ["/", "//", ".", "./", "/etc", "/api/x.py", "..", "../x",
                                    "a/../b", ""])
def test_a_module_outside_the_repo_covers_nothing(module):
    for path in ("api/x.py", ".env", "etc/x", "x", "a/b", "b"):
        assert not schema.module_covers(module, path)


def test_the_boundary_refuses_a_sibling_sharing_a_string_prefix(tmp_path):
    _scope(tmp_path, "`api/x` — the package")
    _built(tmp_path, "api/xyz.py")
    assert _flagged(tmp_path, "api/xyz.py")


def test_a_prose_first_word_opens_nothing(tmp_path):
    _scope(tmp_path, "+ a new module for the proof", "`api/x.py` — x")
    _built(tmp_path, "alembic/env.py")
    assert _flagged(tmp_path, "alembic/env.py")


@pytest.mark.parametrize("bullet, rel", [("`/` — everything", "web/evil.py"),
                                         ("`./` — dotfiles", ".env")])
def test_a_root_module_in_an_older_scope_fails_closed(tmp_path, bullet, rel):
    # A scope written before the validators refused these must not open the boundary.
    _scope(tmp_path, bullet)
    _built(tmp_path, rel)
    assert _flagged(tmp_path, rel)


def test_a_scope_that_declares_nothing_admits_nothing(tmp_path):
    _scope(tmp_path, "`.` — parsed as punctuation, so it declares nothing")
    assert schema.declared_modules((tmp_path / "design/f/scope.md").read_text()) == []
    _built(tmp_path, "web/evil.py")
    assert _flagged(tmp_path, "web/evil.py")


def test_the_boundary_still_admits_what_the_scope_declares(tmp_path):
    # The adversarial twin: narrowing must not refuse a real declaration in any spelling.
    _scope(tmp_path, "`api/x/` — dir", "`./lib/y.py` — dotted", "Makefile", "+ `new/z.py` — new")
    for rel in ("api/x/a.py", "lib/y.py", "Makefile", "new/z.py"):
        _built(tmp_path, rel)
    assert consistency.builder_writes_within_plan(tmp_path, "f") == []


@pytest.mark.parametrize("bullet, token", [
    ("`/` — x", "/"), ("`./` — x", "./"), ("+ `/` — x", "/"),
    ("`/etc` — x", "/etc"), ("`../x.py` — x", "../x.py"), ("+ ../x.py", "../x.py"),
])
@pytest.mark.parametrize("validate", [schema.validate_scope, schema.validate_plan])
def test_the_validators_refuse_a_module_outside_the_repo(tmp_path, validate, bullet, token):
    errors = validate(_scope(tmp_path, bullet), project_root=tmp_path)[1]
    assert f"modules_touched: not a path inside the repo: {token}" in \
        [e.splitlines()[0] for e in errors]


@pytest.mark.parametrize("validate", [schema.validate_scope, schema.validate_plan])
def test_the_validators_refuse_a_section_that_declares_no_path(tmp_path, validate):
    errors = validate(_scope(tmp_path, "`.` — x"), project_root=tmp_path)[1]
    assert any(e.startswith("modules_touched: declares no path") for e in errors)


def test_an_outside_module_is_refused_once_not_also_as_missing(tmp_path):
    errors = schema.validate_scope(_scope(tmp_path, "`/gone` — x"), project_root=tmp_path)[1]
    assert [e for e in errors if "/gone" in e and "does not exist" in e] == []


@pytest.mark.parametrize("validate", [schema.validate_scope, schema.validate_plan])
def test_the_validators_accept_repo_paths(tmp_path, validate):
    (tmp_path / "api").mkdir()
    (tmp_path / ".gitignore").write_text("x\n")
    path = _scope(tmp_path, "`api/` — dir", "`./.gitignore` — dotfile", "+ a new module")
    errors = validate(path, project_root=tmp_path)[1]
    assert [e for e in errors if "not a path inside" in e] == []


def test_a_logged_deviation_credits_by_name_not_by_string_suffix(tmp_path):
    _scope(tmp_path, "`api/q.py` — x")
    (tmp_path / "design" / "f" / "deviations.md").write_text("DEV-001: lib/ax.py — renamed\n")
    _built(tmp_path, "x.py")
    assert _flagged(tmp_path, "x.py")


def test_a_logged_partial_path_still_credits_the_fuller_path(tmp_path):
    _scope(tmp_path, "`api/q.py` — x")
    (tmp_path / "design" / "f" / "deviations.md").write_text("DEV-001: errors/E.ts — moved\n")
    _built(tmp_path, "src/errors/E.ts")
    assert consistency.builder_writes_within_plan(tmp_path, "f") == []
