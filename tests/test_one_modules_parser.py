"""One reader of `## Modules touched`: every gate sees the same declared paths, and backticked
prose declares nothing.

moat-finding: fb-4e4a9fe3d850
moat-finding: fb-8f6e68b77a32
moat-finding: fb-e0c3707245d2
moat-finding: fb-c554221bbd75
moat-finding: fb-967150eabdce
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prusik import affected, blast_plan, consistency, schema, triage

_PROSE_SLASH = "## Modules touched\n- `api/x.py` — add `render` for route `/`\n"


def _write(root: Path, name: str, text: str) -> Path:
    path = root / "design" / "f" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize("bullet, declared", [
    ("`a.py` — add `render` and `b.py`", [("a.py", False)]),
    ("`a.py` — `b.py`, `c.py`", [("a.py", False)]),
    ("`a.py`: see `b.py`", [("a.py", False)]),
    ("`a.py` (`b.py`)", [("a.py", False)]),
    ("`a.sh`, `b.md` — x", [("a.sh", False), ("b.md", False)]),
    ("`a.sh` + `b.yml` — x", [("a.sh", False), ("b.yml", False)]),
    ("`a.sh` & `b.yml`", [("a.sh", False), ("b.yml", False)]),
    ("`a.sh` and `b.yml`, `c.txt`",
     [("a.sh", False), ("b.yml", False), ("c.txt", False)]),
    ("+ `a.sh`, `b.md` — x", [("a.sh", True), ("b.md", True)]),
    ("`a.sh`, + `b.md` — x", [("a.sh", False), ("b.md", True)]),
    ("+ `a.ts`.", [("a.ts", True)]),
    ("`+ a.ts` — x", [("a.ts", True)]),
    ("web/checkout/, related", [("web/checkout/", False)]),
    ("`a.py` and `render` helper", [("a.py", False)]),
    ("`a.py`, `render`, `b.py`", [("a.py", False), ("b.py", False)]),
    ("`a.py` + `x-y z`", [("a.py", False)]),
    ("`a.py` or `b.py`", [("a.py", False), ("b.py", False)]),
    ("`a.py`; `b.py`", [("a.py", False), ("b.py", False)]),
    ("`a.py`, and `b.py`", [("a.py", False), ("b.py", False)]),
    ("`a.py` and new `b.py`", [("a.py", False), ("b.py", True)]),
    ("`a.py`,`b.py`", [("a.py", False), ("b.py", False)]),
    ("`Makefile` — x", [("Makefile", False)]),
    ("", []),
])
def test_only_the_head_declares(bullet, declared):
    assert schema.extract_declared_paths(bullet) == declared


def test_comma_joined_paths_keep_no_stray_backtick_and_each_must_exist(tmp_path):
    (tmp_path / "a.sh").write_text("x\n")
    (tmp_path / "b.md").write_text("x\n")
    body = "## Modules touched\n- `a.sh`, `b.md` — both exist\n"
    errors = schema.validate_scope(_write(tmp_path, "scope.md", body), project_root=tmp_path)[1]
    assert not [e for e in errors if "does not exist" in e]

    body = "## Modules touched\n- `a.sh`, `gone.md` — second is missing\n"
    errors = schema.validate_scope(_write(tmp_path, "scope.md", body), project_root=tmp_path)[1]
    missing = [e.splitlines()[0] for e in errors if "does not exist" in e]
    assert missing == ["modules_touched: path does not exist: gone.md"]


def test_a_prose_slash_cannot_open_the_builder_boundary(tmp_path):
    _write(tmp_path, "scope.md", _PROSE_SLASH)
    stray = tmp_path / "worktrees" / "solo" / "web" / "evil.py"
    stray.parent.mkdir(parents=True)
    stray.write_text("x\n")
    assert "web/evil.py" in "\n".join(consistency.builder_writes_within_plan(tmp_path, "f"))


def test_a_declared_file_stays_inside_the_boundary(tmp_path):
    # The adversarial twin: narrowing the parser must not block what the head really declares.
    _write(tmp_path, "scope.md", _PROSE_SLASH)
    kept = tmp_path / "worktrees" / "solo" / "api" / "x.py"
    kept.parent.mkdir(parents=True)
    kept.write_text("x\n")
    assert consistency.builder_writes_within_plan(tmp_path, "f") == []


def test_prose_in_a_plan_bullet_does_not_fail_the_subset_check(tmp_path):
    _write(tmp_path, "scope.md", "## Modules touched\n- `api/x.py` — x\n")
    _write(tmp_path, "plan.md", "## Modules touched\n- `api/x.py` — add `render_page`\n")
    assert consistency.plan_within_scope(tmp_path, "f") == []


def test_a_real_extra_plan_module_still_fails_the_subset_check(tmp_path):
    _write(tmp_path, "scope.md", "## Modules touched\n- `api/x.py` — x\n")
    _write(tmp_path, "plan.md", "## Modules touched\n- `api/x.py`, `api/y.py` — x\n")
    assert "api/y.py" in "\n".join(consistency.plan_within_scope(tmp_path, "f"))


def test_triage_keeps_new_files_and_never_a_bare_plus(tmp_path):
    scope = _write(tmp_path, "scope.md",
                   "## Modules touched\n- + `new.py` — x\n+ other.py\n- `old.py` — x\n"
                   "- `old.py` — again\n")
    assert triage.parse_scope(scope)["modules"] == ["new.py", "other.py", "old.py"]


def test_all_five_readers_see_the_same_modules(tmp_path):
    (tmp_path / "api").mkdir()
    for name in ("x.py", "y.py", "z.py"):
        (tmp_path / "api" / name).write_text("x = 1\n")
    text = ("## Modules touched\n"
            "- `api/x.py` — add `render` and `api/z.py`\n"
            "\n### NOT in the touch-list (deliberate)\n- `api/z.py` — left alone\n"
            "\n## Modules touched (final)\n- `api/y.py` — added during build\n")
    expected = {"api/x.py", "api/y.py"}
    scope, plan = _write(tmp_path, "scope.md", text), _write(tmp_path, "plan.md", text)

    assert {path for path, _new in schema.declared_modules(text)} == expected
    assert consistency._modules_from(scope) == expected
    assert set(triage.parse_scope(scope)["modules"]) == expected
    assert set(affected._source_modules("f", tmp_path)) == expected
    assert set(blast_plan.plan_modules("f", tmp_path)[0]) == expected
    assert schema.validate_scope(plan, project_root=tmp_path)[1] == schema.validate_scope(
        scope, project_root=tmp_path)[1]


def test_validate_scope_ignores_prose_and_excluded_paths(tmp_path):
    (tmp_path / "x.py").write_text("x\n")
    scope = _write(tmp_path, "scope.md",
                   "## Modules touched\n- `x.py` — mentions `gone.py`\n"
                   "### NOT in the touch-list\n- `also-gone.py` — left alone\n")
    errors = schema.validate_scope(scope, project_root=tmp_path)[1]
    assert not [e for e in errors if "does not exist" in e]


def test_consistency_has_no_second_bullet_parser():
    source = Path(consistency.__file__).read_text()
    assert "_bullet_module_tokens" not in source and "extract_module_token" not in source


def test_enforcer_spec_checks_against_the_boundary_the_gate_enforces():
    spec = (Path(consistency.__file__).parent / "templates" / ".claude" / "agents"
            / "conventions-enforcer.md").read_text()
    step0 = spec[spec.index("**0. Worktree completeness"):spec.index("**1. Content conventions")]
    assert "scope.md" in spec and "deviations.md" in step0 and "blast-prediction" in step0
    assert "never\n    FAIL a file for being missing from it" in step0
    assert "plan.md\n    `## Modules touched` in team mode" not in step0
