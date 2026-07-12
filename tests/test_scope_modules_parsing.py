"""scope.md Modules-touched parsing tolerance + boundary remediation steer (fb-6a4075fb15fe, fb-75fd9cfa7ead, fb-c8175a7678be): a header SUFFIX, a restated
'Modules touched (final)' section, and a bullet naming TWO backticked paths must all
parse; and a legitimate file-name deviation must be steered to deviations.md (the
always-writable, boundary-credited path) — never a phase-blocked scope.md edit.

moat-finding: fb-6a4075fb15fe
moat-finding: fb-75fd9cfa7ead
moat-finding: fb-c8175a7678be
"""

from __future__ import annotations

from pathlib import Path

from prusik import consistency


def _scope(tmp_path: Path, feature: str, body: str) -> Path:
    d = tmp_path / "design" / feature
    d.mkdir(parents=True, exist_ok=True)
    p = d / "scope.md"
    p.write_text(body)
    return p


def test_header_with_parenthetical_suffix_is_tolerated(tmp_path):
    p = _scope(tmp_path, "f", "## Goal recap\nx\n\n## Modules touched (final)\n"
               "- `api/service.py` — extend\n")
    assert consistency._modules_from(p) == {"api/service.py"}


def test_two_backticked_paths_in_one_bullet(tmp_path):
    p = _scope(tmp_path, "f", "## Modules touched\n"
               "- `scripts/a.sh` + `config/b.yml` — both produced\n")
    assert consistency._modules_from(p) == {"scripts/a.sh", "config/b.yml"}


def test_restated_modules_section_is_unioned(tmp_path):
    p = _scope(tmp_path, "f", "## Modules touched\n- `api/x.py` — first\n\n"
               "## Modules touched (final)\n- `api/y.py` — added during build\n")
    assert consistency._modules_from(p) == {"api/x.py", "api/y.py"}


def test_plain_section_still_parses(tmp_path):
    p = _scope(tmp_path, "f", "## Modules touched\n- `api/x.py` — extend\n")
    assert consistency._modules_from(p) == {"api/x.py"}


def test_boundary_block_steers_to_deviations_not_scope_edit(tmp_path):
    _scope(tmp_path, "f", "## Modules touched\n- `api/` — the api\n")
    wt = tmp_path / "worktrees" / "builder" / "web" / "page.tsx"
    wt.parent.mkdir(parents=True)
    wt.write_text("x\n")
    blob = "\n".join(consistency.builder_writes_within_plan(tmp_path, "f"))
    assert "outside" in blob                       # still blocks a genuine out-of-scope file
    assert "deviations.md" in blob                 # steers to the RIGHT, writable path
    assert "phase-blocked" in blob                 # explains why not scope.md


def test_logged_deviation_clears_the_block(tmp_path):
    """The honest path works: logging the file in deviations.md credits it."""
    _scope(tmp_path, "f", "## Modules touched\n- `api/` — the api\n")
    wt = tmp_path / "worktrees" / "builder" / "web" / "page.tsx"
    wt.parent.mkdir(parents=True)
    wt.write_text("x\n")
    (tmp_path / "design" / "f" / "deviations.md").write_text(
        "## Deviations\n- DEV-001: web/page.tsx — consolidated render here\n")
    assert consistency.builder_writes_within_plan(tmp_path, "f") == []
