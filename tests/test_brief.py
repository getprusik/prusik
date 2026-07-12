"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: brief.

Run: uv run python -m pytest tests/test_brief.py -v
Or run the whole suite: uv run python -m pytest tests/ -v

Shared helpers live in tests/_common.py (private; pytest does not
collect leading-underscore modules). v0.23.0 split tests/test_smoke.py
by domain to keep individual files navigable.
"""

# noqa: F401 — wildcard imports below intentionally re-export everything
# from _common (prusik modules, helpers, the tempfile/json/os toolbelt).
# F401 individual unused-name warnings would obscure the rest.
from tests._common import *  # noqa: F401,F403,E402
from tests._common import (  # noqa: F401,E402
    argparse, contextlib, io, json, os, re, shutil, subprocess, sys,
    tempfile, time, Path,
    schema, phases, triage, discovery, gate, watchdog, issues,
    kit_init, kit_uninstall, kit_toggle, consistency, agents_doctor,
    kit_refresh, kit_pause, kit_permissions, kit_brief_lint,
    kit_fix_round, kit_bridge, kit_detect, kit_doctor, ledger_digest,
    _mktmp_project, _copy_sprint_config, _wt_file, _write_ledger,
    _capture_stdout, _capture_stderr, _VALID_BRIEF,
)


# ---------- v0.4.0 brief-lint ----------

def test_brief_lint_extracts_candidates():
    text = """## Goal
Add a feature to the "Invoice lifecycle" epic to handle BL-075 and ADR-12.

## Notes
Involves the Workspace Configuration module and some edits.
"""
    cands = kit_brief_lint._extract_candidates(text)
    assert "Invoice lifecycle" in cands
    assert "BL-075" in cands
    assert "ADR-12" in cands
    # Multi-word Capitalized phrase
    assert any("Workspace Configuration" in c for c in cands)


def test_near_miss_suppresses_case_separator_only_reference():
    """fb-a1753e4a729d: 'Submission profile' (Title-Case prose for the CLAUDE.md
    subsection) must NOT flag as a near-miss for the kebab sentinel
    'submission-profile' — it differs only by case + space-vs-hyphen, so it's a
    legitimate reference, not a typo. But a REAL typo still fires.

    moat-finding: fb-a1753e4a729d
    """
    from prusik.brief_lint import _near_misses
    known = {"submission-profile", "invoice-lifecycle-api"}
    # case/separator-only variants → suppressed (no false-positive)
    assert _near_misses({"Submission profile"}, known) == []
    assert _near_misses({"Submission-profile"}, known) == []
    assert _near_misses({"submission_profile"}, known) == []
    # a genuine typo (dropped letter) still normalizes differently → still flagged
    misses = _near_misses({"Submision profile"}, known)
    assert misses and misses[0][0] == "Submision profile"


def test_brief_lint_flags_near_miss_against_dep_graph():
    tmp = _mktmp_project()
    try:
        # Fake a dep-graph with known module names
        (tmp / ".sprint").mkdir()
        (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps({
            "forward": {
                "api/invoice_lifecycle_api/main.py": [],
                "api/workspace_configuration/main.py": [],
            },
            "reverse": {}, "stats": {"by_language": {"python": 2}}
        }))
        # brief with a near-miss reference
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "feat.md").write_text("""## Goal
Extend the "invoice_lifecycle" module to handle new cases.

## Success criteria
Covers at least 10 new cases with no errors.

## Type
new_feature
""")
        rc = kit_brief_lint.lint(None)
        assert rc == 1, "near-miss should produce nonzero exit"
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_passes_with_exact_match():
    tmp = _mktmp_project()
    try:
        (tmp / ".sprint").mkdir()
        (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps({
            "forward": {"api/billing/main.py": []},
            "reverse": {}, "stats": {"by_language": {"python": 1}}
        }))
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "feat.md").write_text("""## Goal
Add email receipts to the api/billing/main.py flow.

## Success criteria
Receipts arrive within 10s with no errors.

## Type
new_feature
""")
        rc = kit_brief_lint.lint(None)
        assert rc == 0
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_structural_takes_priority():
    """Structural errors are reported alongside (not skipped) near-miss checks."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        # Immeasurable success criteria → structural FAIL
        (tmp / "briefs" / "bad.md").write_text("""## Goal
Make checkout better for users.

## Success criteria
Fast and good.

## Type
new_feature
""")
        rc = kit_brief_lint.lint(None)
        assert rc == 1
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_no_briefs_dir():
    tmp = _mktmp_project()
    try:
        # No briefs/ dir at all → 0, no error
        rc = kit_brief_lint.lint(None)
        assert rc == 0
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_extra_known_sources_from_python_list():
    """v0.4.3: extra_known_sources.grep extracts IDs from a declared file."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        # Project has a python file with many BL-### references
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "build_backlog.py").write_text("""
BACKLOG = [
    ("BL-040", "Generate stitched PDF endpoint"),
    ("BL-074", "Some item"),
    ("BL-084", "PII at rest"),
]
""")
        # Inject extra_known_sources into the config
        config_path = tmp / ".claude" / "sprint-config.yaml"
        cfg_text = config_path.read_text()
        cfg_text = cfg_text.replace(
            "brief_lint:\n  extra_known_sources: []",
            'brief_lint:\n  extra_known_sources:\n    - path: scripts/build_backlog.py\n      grep: "BL-\\\\d+"',
        )
        config_path.write_text(cfg_text)

        # Brief references real BL-### IDs. Without extra_known_sources, these
        # would be flagged as near-miss against BL-074. With extra_known_sources,
        # they're known.
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "sync.md").write_text("""## Goal
Flip BL-040 and BL-084 to Done in the backlog sync.

## Success criteria
All status flips apply within 1 minute and no orphan rows introduced.

## Type
doc
""")
        rc = kit_brief_lint.lint(None)
        assert rc == 0, "real BL-### IDs should be recognized after config"
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_extra_known_sources_directory_with_grep():
    """v0.4.3: dir sources walk files and extract matches."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        adr_dir = tmp / "design" / "adr"
        adr_dir.mkdir(parents=True)
        (adr_dir / "adr-001.md").write_text("# ADR-001\nTest ADR\n")
        (adr_dir / "adr-002.md").write_text("# ADR-002\nAnother ADR\n")

        config_path = tmp / ".claude" / "sprint-config.yaml"
        cfg_text = config_path.read_text()
        cfg_text = cfg_text.replace(
            "brief_lint:\n  extra_known_sources: []",
            'brief_lint:\n  extra_known_sources:\n    - path: design/adr/\n      grep: "ADR-\\\\d+"',
        )
        config_path.write_text(cfg_text)

        known = kit_brief_lint._project_known_strings(tmp)
        assert "ADR-001" in known
        assert "ADR-002" in known
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_command_source_extracts_generated_ids():
    """v0.4.4: `type: command` runs a shell command; tokens from stdout go into known set."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config_path = tmp / ".claude" / "sprint-config.yaml"
        cfg_text = config_path.read_text()
        # A command that enumerates BL-001..BL-003 — stand-in for a
        # generator-derived ID set
        cmd = "python3 -c 'for i in range(1,4): print(f\"BL-{i:03d}\")'"
        cfg_text = cfg_text.replace(
            "brief_lint:\n  extra_known_sources: []",
            f'brief_lint:\n  extra_known_sources:\n    - type: command\n      command: {json.dumps(cmd)}\n      grep: "BL-\\\\d+"',
        )
        config_path.write_text(cfg_text)
        known = kit_brief_lint._project_known_strings(tmp)
        assert {"BL-001", "BL-002", "BL-003"}.issubset(known)
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_command_source_without_grep_uses_lines():
    """If no grep is given, each non-empty line of stdout becomes a token."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config_path = tmp / ".claude" / "sprint-config.yaml"
        cfg_text = config_path.read_text()
        cmd = "echo TOKEN-ALPHA && echo TOKEN-BETA"
        cfg_text = cfg_text.replace(
            "brief_lint:\n  extra_known_sources: []",
            f'brief_lint:\n  extra_known_sources:\n    - type: command\n      command: {json.dumps(cmd)}',
        )
        config_path.write_text(cfg_text)
        known = kit_brief_lint._project_known_strings(tmp)
        assert "TOKEN-ALPHA" in known
        assert "TOKEN-BETA" in known
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_proposed_new_ids_admitted_for_brief():
    """v0.4.4: `## Proposed new IDs` section tokens are added to this brief's known set."""
    tmp = _mktmp_project()
    try:
        # Known: only BL-074
        (tmp / ".sprint").mkdir()
        (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps({
            "forward": {"api/BL-074/main.py": []},
            "reverse": {}, "stats": {"by_language": {"python": 1}}
        }))
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "sync.md").write_text("""## Goal
Add new BL-085 and BL-086 story entries for the new epic.

## Success criteria
Both IDs appear in BACKLOG within 5 min of running the generator.

## Type
doc

## Proposed new IDs
- BL-085
- BL-086
""")
        rc = kit_brief_lint.lint(None)
        # BL-085 and BL-086 admitted via the section → no near-miss flags
        assert rc == 0
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_proposed_new_ids_accepts_comma_separated():
    """Section can use bullets or comma-separated inline."""
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "f.md").write_text("""## Goal
Add more items into the system for backlog growth.

## Success criteria
Both land within 5 min with no orphan rows.

## Type
doc

## Proposed new IDs
BL-085, BL-086
""")
        text = (tmp / "briefs" / "f.md").read_text()
        proposed = kit_brief_lint._proposed_new_ids(text)
        assert proposed == {"BL-085", "BL-086"}
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_extra_known_sources_invalid_regex_skipped():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config_path = tmp / ".claude" / "sprint-config.yaml"
        cfg_text = config_path.read_text()
        cfg_text = cfg_text.replace(
            "brief_lint:\n  extra_known_sources: []",
            'brief_lint:\n  extra_known_sources:\n    - path: nonexistent.py\n      grep: "((("',
        )
        config_path.write_text(cfg_text)
        # Should not raise; missing + invalid sources silently skip
        known = kit_brief_lint._project_known_strings(tmp)
        assert isinstance(known, set)
    finally:
        shutil.rmtree(tmp)


def test_brief_lint_specific_path():
    tmp = _mktmp_project()
    try:
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "clean.md").write_text("""## Goal
Add a log line with a clear identifier string for debugging.

## Success criteria
Log line appears within 1 second of the event.

## Type
new_feature
""")
        rc = kit_brief_lint.lint(str(tmp / "briefs" / "clean.md"))
        assert rc == 0
    finally:
        shutil.rmtree(tmp)



# ---------- plan schema / prusik gate plan (v0.5.4) ----------

_VALID_PLAN = """## Goal recap
Ship the API billing update end to end.

## Modules touched
- api/billing/ — swap provider client
- api/billing/adapters.py — new adapter class

## Build order
- 1. Extract provider client interface
- 2. Implement new adapter
- 3. Wire into router

## Interfaces
New `BillingProvider.charge(amount, idempotency_key) -> Receipt`.

## Test plan
- happy path: successful charge returns receipt
- failure mode: provider timeout surfaces 502 to caller
- regression: existing refund flow still round-trips

## Risks
- idempotency key collision mid-rollout

## Out of scope
- invoice PDF generation

## Proposed roles
- backend-builder → api/billing/
- test-writer → tests/billing/
"""


def _valid_scope_for_plan() -> str:
    return """## Goal recap
Ship the API billing update end to end.

## Modules touched
- api/billing/
- api/billing/adapters.py

## Blast radius
- api/router.py

## Related work

## Size
M

## Domains
- backend

## Risks
- idempotency key collision mid-rollout

## Open questions
"""


def test_plan_schema_accepts_valid():
    tmp = _mktmp_project()
    try:
        plan = tmp / "design" / "feat" / "plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text(_VALID_PLAN)
        ok, errs = schema.validate_plan(plan)
        assert ok, f"errors: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_plan_schema_rejects_missing_proposed_roles():
    tmp = _mktmp_project()
    try:
        plan = tmp / "design" / "feat" / "plan.md"
        plan.parent.mkdir(parents=True)
        # Drop the Proposed roles section entirely
        text = _VALID_PLAN.replace(
            "## Proposed roles\n- backend-builder → api/billing/\n- test-writer → tests/billing/\n",
            "",
        )
        plan.write_text(text)
        ok, errs = schema.validate_plan(plan)
        assert not ok
        assert any("Proposed roles" in e for e in errs), errs
    finally:
        shutil.rmtree(tmp)


def test_plan_schema_rejects_thin_test_plan():
    tmp = _mktmp_project()
    try:
        plan = tmp / "design" / "feat" / "plan.md"
        plan.parent.mkdir(parents=True)
        # Only 2 bullets — spec requires ≥3
        text = _VALID_PLAN.replace(
            "## Test plan\n- happy path: successful charge returns receipt\n"
            "- failure mode: provider timeout surfaces 502 to caller\n"
            "- regression: existing refund flow still round-trips\n",
            "## Test plan\n- happy path\n- regression\n",
        )
        plan.write_text(text)
        ok, errs = schema.validate_plan(plan)
        assert not ok
        assert any("test_plan" in e and "3" in e for e in errs), errs
    finally:
        shutil.rmtree(tmp)


def test_plan_schema_rejects_no_risks():
    tmp = _mktmp_project()
    try:
        plan = tmp / "design" / "feat" / "plan.md"
        plan.parent.mkdir(parents=True)
        text = _VALID_PLAN.replace(
            "## Risks\n- idempotency key collision mid-rollout\n",
            "## Risks\n\n",
        )
        plan.write_text(text)
        ok, errs = schema.validate_plan(plan)
        assert not ok
        assert any("Risks" in e or "risks" in e for e in errs), errs
    finally:
        shutil.rmtree(tmp)


def test_plan_schema_cross_ref_detects_scope_drift():
    """plan.md adds module not in scope → validator flags via consistency layer."""
    tmp = _mktmp_project()
    try:
        scope = tmp / "design" / "feat" / "scope.md"
        scope.parent.mkdir(parents=True)
        scope.write_text(_valid_scope_for_plan())

        plan = tmp / "design" / "feat" / "plan.md"
        # Plan sneaks in a new module not declared in scope
        text = _VALID_PLAN.replace(
            "## Modules touched\n- api/billing/ — swap provider client\n"
            "- api/billing/adapters.py — new adapter class\n",
            "## Modules touched\n- api/billing/ — swap provider client\n"
            "- api/billing/adapters.py — new adapter class\n"
            "- api/auth/ — surprise auth tweak\n",
        )
        plan.write_text(text)
        ok, errs = schema.validate_plan(plan, project_root=tmp)
        assert not ok
        assert any("adds modules not in scope" in e for e in errs), errs
    finally:
        shutil.rmtree(tmp)


def test_gate_plan_cli_valid():
    tmp = _mktmp_project()
    try:
        plan = tmp / "design" / "feat" / "plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text(_VALID_PLAN)
        args = argparse.Namespace(path=str(plan))
        rc = gate.plan(args)
        assert rc == 0
    finally:
        shutil.rmtree(tmp)


def test_gate_plan_cli_invalid_exit_code():
    tmp = _mktmp_project()
    try:
        plan = tmp / "design" / "feat" / "plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text("## Goal recap\ntoo short\n")
        args = argparse.Namespace(path=str(plan))
        rc = gate.plan(args)
        assert rc == 2
    finally:
        shutil.rmtree(tmp)


def test_scoping_role_pins_new_file_convention():
    """The scoping role template must explicitly show the `+ ` convention for
    new files, so the role doesn't emit `**(new)**` and force a re-scope."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "scoping.md")
    text = p.read_text()
    # Must show the + marker explicitly with an example
    assert "+ `" in text, "scoping.md must demonstrate the `+ <path>` convention"
    # Must warn off alternate markers we've observed in the wild
    assert "**(new)**" in text, "scoping.md must explicitly reject `**(new)**`"



# ---------- parser parity (v0.5.5) ----------

def test_extract_module_token_shapes():
    """Single helper must handle every bullet shape that surfaced in trial sprints."""
    cases = [
        ("`path/to/file.py` — desc",         ("path/to/file.py", False)),
        ("path/to/file.py",                  ("path/to/file.py", False)),
        ("+ `new/mod.py` — desc",            ("new/mod.py",      True)),
        ("+ new/mod.py",                     ("new/mod.py",      True)),
        ("**+ new/mod.py** — desc",          ("new/mod.py",      True)),
        ("`+ new/mod.py` — desc",            ("new/mod.py",      True)),
        ("",                                 ("",                False)),
        ("+",                                ("",                True)),  # degenerate
    ]
    for body, expected in cases:
        got = schema.extract_module_token(body)
        assert got == expected, f"body={body!r}  got={got}  expected={expected}"


def test_consistency_modules_from_matches_scope_schema_parser():
    """v0.5.5 B20 regression: `builder_writes_within_plan` + `plan_within_scope`
    must parse the same `- + \\`path\\`` bullet identically to scope-schema.

    Before v0.5.5, `_modules_from` called `extract_path_token` raw and
    captured `+` as a path for `+ \\`path\\``-form bullets — producing
    thousands of false builder-out-of-plan violations and deadlocking
    cli-foundation at building → reviewing."""
    tmp = _mktmp_project()
    try:
        # Build a scope with a mix of existing + new-file bullets
        (tmp / "src").mkdir()
        (tmp / "src" / "existing.py").write_text("# existing\n")
        scope = tmp / "design" / "feat" / "scope.md"
        scope.parent.mkdir(parents=True)
        scope.write_text("""## Goal recap
Ship the thing with new files.

## Modules touched
- `src/existing.py` — refactor
- + `src/brand_new.py` — new module
- + `scripts/helper.py` — new helper
- + `tests/test_new.py` — new tests

## Blast radius
- src/caller.py

## Related work
- none

## Size
M

## Domains
- backend

## Risks
- broken imports

## Open questions
- none
""")
        # scope-schema parser should accept all 4 bullets
        ok, errs = schema.validate_scope(scope, project_root=tmp)
        assert ok, f"scope-schema errors: {errs}"

        # Plan under the same feature with a subset of the scope modules
        plan = tmp / "design" / "feat" / "plan.md"
        plan.write_text("""## Goal recap
Ship the thing with new files.

## Modules touched
- `src/existing.py` — refactor
- + `src/brand_new.py` — new module
- + `tests/test_new.py` — new tests

## Build order
- 1. existing refactor
- 2. new module
- 3. tests

## Interfaces
stable

## Test plan
- happy
- failure
- regression

## Risks
- broken imports

## Out of scope
- unrelated fixes

## Proposed roles
- backend-builder → src/
- test-writer → tests/
""")
        # plan_within_scope: plan modules must be a SUBSET of scope modules.
        # Pre-v0.5.5 bug: `+ `-prefixed bullets in either artifact got parsed
        # as `+` — producing spurious set-difference and false positives.
        errs = consistency.plan_within_scope(tmp, "feat")
        assert errs == [], f"plan_within_scope false positive: {errs}"

        # builder_writes_within_plan: a file physically written under worktrees/
        # at `src/brand_new.py` falls under the plan's Modules touched and
        # should NOT be flagged.
        worktree = tmp / "worktrees" / "backend-builder"
        (worktree / "src").mkdir(parents=True)
        (worktree / "src" / "brand_new.py").write_text("# new\n")
        errs = consistency.builder_writes_within_plan(tmp, "feat")
        assert errs == [], f"builder_writes_within_plan false positive: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_consistency_still_catches_real_plan_drift():
    """Parser unification must not mask real drift — a plan that really does
    add an undeclared module should still fail plan_within_scope."""
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir()
        (tmp / "src" / "a.py").write_text("#\n")
        scope = tmp / "design" / "feat" / "scope.md"
        scope.parent.mkdir(parents=True)
        scope.write_text("""## Goal recap
Plan adds a module the scope never declared.

## Modules touched
- `src/a.py`

## Blast radius
- none

## Related work
- none

## Size
S

## Domains
- backend

## Risks
- none

## Open questions
- none
""")
        plan = tmp / "design" / "feat" / "plan.md"
        plan.write_text("""## Goal recap
Plan adds a module the scope never declared.

## Modules touched
- `src/a.py`
- + `src/surprise.py`

## Build order
- 1. do a
- 2. do surprise

## Interfaces
n/a

## Test plan
- t1
- t2
- t3

## Risks
- r1

## Out of scope
- x

## Proposed roles
- backend-builder → src/
""")
        errs = consistency.plan_within_scope(tmp, "feat")
        assert errs, "expected plan_within_scope to flag src/surprise.py as out-of-scope"
        assert any("src/surprise.py" in e for e in errs), errs
    finally:
        shutil.rmtree(tmp)



# ---------- v0.6.7 nested-bullet skipping (B13) ----------

def test_extract_list_items_skips_indented_subbullets():
    """v0.6.7 (B13): a parent bullet with nested sub-bullets should produce
    ONE item (the parent), not N+1 (parent + each sub-bullet treated as
    a top-level entry).

    Surfaced when an M1.S4 plan.md had a `## Modules touched` bullet for
    `tests/integration/test_invoice_lifecycle.py` followed by 9 nested
    sub-bullets describing test coverage. The validator reported phantom
    modules `['25', '6', 'Reissue', 'record-payment', 'unlock']` — first
    words of each sub-bullet."""
    body = """- + `tests/integration/test_invoice_lifecycle.py` — new file
  - 6 happy-path scenarios for invoice lifecycle
  - 25 illegal-cell matrix tests
  - Reissue suffix tests for void
  - `record-payment` overpayment exit 4
  - `record-payment` partial path
- `src/api/lifecycle.py` — orchestrator
"""
    items = schema.extract_list_items(body)
    assert len(items) == 2
    assert items[0].startswith("+ `tests/integration/test_invoice_lifecycle.py`")
    assert items[1].startswith("`src/api/lifecycle.py`")
    # None of the sub-bullet first-words appear as their own entries
    joined = " ".join(items)
    assert "happy-path" not in joined or "happy-path scenarios for invoice" in joined
    # Crucially: no standalone "25", "Reissue", "record-payment" entries
    for phantom in ["25", "Reissue", "record-payment"]:
        assert phantom not in items, f"phantom {phantom!r} should not be a top-level item"


def test_extract_list_items_handles_tab_indentation():
    """Tab-indented sub-bullets must also be skipped (not just space-indented)."""
    body = "- top1\n\t- nested via tab\n- top2\n"
    items = schema.extract_list_items(body)
    assert items == ["top1", "top2"]


def test_extract_list_items_two_space_vs_four_space_indent():
    """Both 2-space and 4-space indent conventions are skipped."""
    body = """- top1
  - 2-space nested
- top2
    - 4-space nested
- top3
"""
    items = schema.extract_list_items(body)
    assert items == ["top1", "top2", "top3"]


def test_consistency_modules_from_skips_nested_bullets():
    """End-to-end: scope.md or plan.md with nested sub-bullets under
    Modules touched must NOT produce phantom modules in the cross-artifact
    parser. Pre-v0.6.7 consistency had its own _bullet_items that didn't
    skip indents — even with schema.extract_list_items hardened, the
    cross-artifact path was a separate parser."""
    tmp = _mktmp_project()
    try:
        (tmp / "src").mkdir()
        (tmp / "src" / "api.py").write_text("#")
        scope = tmp / "design" / "feat" / "scope.md"
        scope.parent.mkdir(parents=True)
        scope.write_text("""## Goal recap
Plan with nested coverage detail.

## Modules touched
- `src/api.py`
- + `tests/integration/test_lifecycle.py` — coverage detail
  - 6 happy-path
  - 25 illegal-cell
  - record-payment overpayment

## Blast radius
- none

## Related work
- none

## Size
M

## Domains
- backend

## Risks
- none

## Open questions
- none
""")
        plan = tmp / "design" / "feat" / "plan.md"
        plan.write_text("""## Goal recap
Same lifecycle work.

## Modules touched
- `src/api.py` — orchestrator
- + `tests/integration/test_lifecycle.py` — new tests
  - happy paths
  - illegal cells

## Build order
- 1. api
- 2. tests

## Interfaces
n/a

## Test plan
- t1
- t2
- t3

## Risks
- r1

## Out of scope
- none

## Proposed roles
- backend-builder
""")
        # Both scope and plan use nested bullets — pre-v0.6.7 this surfaced
        # phantom "modules not in scope.md" violations because plan's nested
        # entries got flagged. Post-v0.6.7 the cross-artifact check sees
        # only top-level paths.
        errs = consistency.plan_within_scope(tmp, "feat")
        assert errs == [], f"nested bullets should not produce phantom drift: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_consistency_no_longer_has_duplicate_bullet_extractor():
    """v0.6.7 consolidation: `_bullet_items` removed; consistency now routes
    through schema.extract_list_items (one helper, shared everywhere).
    Regression-guard: future maintainers shouldn't re-introduce a duplicate."""
    src = (Path(__file__).parent.parent / "prusik" / "consistency.py").read_text()
    assert "def _bullet_items" not in src, \
        "consistency.py must not have its own bullet-extractor — use schema.extract_list_items"
    assert "schema.extract_list_items" in src, \
        "consistency.py must route through schema.extract_list_items"



# ---------- v0.6.8 bullet-parser consolidation across triage + brief_lint ----------

def test_triage_no_longer_has_duplicate_bullet_extractor():
    """v0.6.8: triage._bullets removed; parse_scope routes through
    schema.extract_list_items. Same kit-design principle as v0.6.7's
    consistency._bullet_items removal."""
    src = (Path(__file__).parent.parent / "prusik" / "triage.py").read_text()
    assert "def _bullets(" not in src, \
        "triage.py must not have its own bullet-extractor — use schema.extract_list_items"
    assert "schema.extract_list_items" in src, \
        "triage.py must route through schema.extract_list_items"


def test_brief_lint_no_longer_has_inline_bullet_walk():
    """v0.6.8: brief_lint's scope.md walk now uses schema.extract_list_items
    instead of inline `if s.startswith('- ')`."""
    src = (Path(__file__).parent.parent / "prusik" / "brief_lint.py").read_text()
    assert "for item in schema.extract_list_items(text):" in src, \
        "brief_lint scope walk must route through schema.extract_list_items"


def test_triage_handles_nested_modules_touched_correctly():
    """End-to-end: triage with nested sub-bullets in scope.md must NOT
    inflate modules count from sub-bullet first-words."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / "src").mkdir(exist_ok=True)
        (tmp / "src" / "api.py").write_text("#")
        (tmp / "src" / "lifecycle.py").write_text("#")
        scope = tmp / "design" / "feat" / "scope.md"
        scope.parent.mkdir(parents=True, exist_ok=True)
        scope.write_text("""## Goal recap
End-to-end nested bullet test.

## Modules touched
- `src/api.py` — orchestrator
- `src/lifecycle.py` — state machine
  - 6 happy-path scenarios
  - 25 illegal-cell tests
  - record-payment overpayment exit 4

## Blast radius
- none

## Related work
- none

## Size
M

## Domains
- backend
  - this is a sub-bullet that should NOT count as a domain

## Risks
- none

## Open questions
- none
""")
        (tmp / "briefs" / "feat.md").write_text("""## Goal
Test that nested bullets don't break triage.

## Success criteria
modules count == 2 (only top-level), domains count == 1.

## Type
new_feature
""")
        rc = triage.run("feat")
        assert rc == 0
        decision = json.loads(
            (tmp / "decisions" / "feat.json").read_text()
        )
        modules = decision["scope_summary"]["modules"]
        domains = decision["scope_summary"]["domains"]
        assert len(modules) == 2, f"only top-level modules count: got {modules}"
        assert "src/api.py" in modules and "src/lifecycle.py" in modules
        assert domains == ["backend"], f"only top-level domains: got {domains}"
    finally:
        shutil.rmtree(tmp)


def test_triage_handles_hr_separators_in_scope():
    """End-to-end: triage with `---` separators between sections must NOT
    inflate counts. Pre-v0.6.8 triage._bullets matched `---` as a `--`
    bullet, so triage saw phantom domains/modules."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        (tmp / "src").mkdir(exist_ok=True)
        (tmp / "src" / "api.py").write_text("#")
        scope = tmp / "design" / "feat" / "scope.md"
        scope.parent.mkdir(parents=True, exist_ok=True)
        scope.write_text("""## Goal recap
HR separators end-to-end test.

## Modules touched
- `src/api.py`

---

## Blast radius
- none

---

## Related work
- none

## Size
S

## Domains
- backend

---

## Risks
- none

## Open questions
- none
""")
        (tmp / "briefs" / "feat.md").write_text("""## Goal
Test that HR separators don't break triage parsing.

## Success criteria
modules == 1, domains == 1, no phantom `--` items.

## Type
bug_fix
""")
        rc = triage.run("feat")
        assert rc == 0
        decision = json.loads((tmp / "decisions" / "feat.json").read_text())
        modules = decision["scope_summary"]["modules"]
        domains = decision["scope_summary"]["domains"]
        assert modules == ["src/api.py"], f"HR should not produce phantom modules: {modules}"
        assert domains == ["backend"], f"HR should not produce phantom domains: {domains}"
    finally:
        shutil.rmtree(tmp)



# ---------- v0.6.9 plan-critic CLAUDE.md cross-check (B16) ----------

def test_plan_critic_template_cross_checks_claude_md_conventions():
    """v0.6.9 (B16): plan-critic role spec must instruct cross-checking
    plan-prescribed code samples (docstrings, signatures, exception handling)
    against CLAUDE.md conventions. Pre-v0.6.9, plan-critic approved plans
    that prescribed verbose multi-paragraph docstrings; conventions-enforcer
    then FAILed all of them at review time, costing fix-round 1 each
    occurrence. B16 recurred 3+ times across the trial cycle, meeting the
    'recurrence = ship now' trigger threshold from the [01:00] B14 framing."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "plan-critic.md")
    text = p.read_text()
    # CLAUDE.md must be in the Inputs list
    assert "CLAUDE.md" in text
    # Must explicitly call out checking plan-prescribed code samples
    assert "plan-prescribed code samples" in text.lower() \
        or "plan-prescribed" in text.lower()
    # Must name the specific pattern that triggered B16: multi-paragraph
    # docstrings with Args/Returns/Raises section headers
    assert "Args" in text and "Returns" in text and "Raises" in text
    # Must reference the cost rationale: catch HERE, not at fix-round time
    assert "fix-round" in text.lower()
    # B16 reference for findability
    assert "B16" in text



# ---------- v0.7.0 role-spec extensions (B17 + B21) ----------

def test_regression_sentinel_pins_worktree_paths_during_reviewing():
    """v0.7.0 (B17): regression-sentinel must read deliverables from
    worktrees/<role>/... during reviewing, NOT from project root.
    Recurred at backup-restore-polish + acceptance-scripts (recurrence #2,
    trigger met)."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    # Must explicitly say "deliverables in worktrees, not project root"
    assert "worktrees/<role>" in text or "worktrees/" in text
    # Must explain the WHY: integrator hasn't merged yet
    assert "integrator" in text.lower() and "merged" in text.lower()
    # Must distinguish "read from worktree" vs "run from project root"
    assert "READ" in text and "RUN" in text
    # B17 cross-reference
    assert "B17" in text


def test_regression_sentinel_pins_project_runtime_environment():
    """v0.7.0 (B17): regression-sentinel must use project's declared
    runtime environment (.env, docker-compose, sprint-config) instead
    of shell defaults. Wrong port → wrong DB → false-positive failure
    attributed to sprint deliverables."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "regression-sentinel.md")
    text = p.read_text()
    # Must call out the env-resolution rule
    assert ".env" in text or "docker-compose" in text
    # Must call out the failure mode (wrong port, etc.)
    assert "port" in text.lower()
    # Must instruct reading project config before running
    lower = text.lower()
    assert "shell" in lower or "default" in lower
    assert "reviewer-side" in text.lower() or "reviewer side" in text.lower()


def test_plan_critic_requires_acceptance_scenario_test_coverage():
    """v0.7.0 (B21): plan-critic must REJECT plans for new user-facing
    handlers that don't surface BOTH default-flag AND
    destructive-on-populated-state test cases. Family caught the
    M1.S5 _build_payment and backup-restore-polish _handle_restore
    defects at acceptance walkthrough — would have been caught at
    plan time with this check."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "agents" / "plan-critic.md")
    text = p.read_text()
    # Must call out user-facing entry points specifically
    lower = text.lower()
    assert "user-facing" in lower
    # Must name the two required scenario shapes
    assert "default-flag" in text or "default flag" in text
    assert "populated" in text or "populated-state" in text
    # Must reference the precedent recurrence pattern
    assert "_build_payment" in text or "_handle_restore" in text \
        or "M1.S5" in text or "backup-restore-polish" in text
    # B21 cross-reference
    assert "B21" in text


