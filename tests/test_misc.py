"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: misc.

Run: uv run python -m pytest tests/test_misc.py -v
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


# ---------- schema ----------

def test_brief_schema_rejects_empty():
    tmp = _mktmp_project()
    try:
        brief = tmp / "briefs" / "x.md"
        brief.parent.mkdir()
        brief.write_text("")
        ok, errs = schema.validate_brief(brief)
        assert not ok
        assert any("Missing" in e for e in errs)
    finally:
        shutil.rmtree(tmp)


def test_brief_schema_rejects_immeasurable_success():
    tmp = _mktmp_project()
    try:
        brief = tmp / "briefs" / "x.md"
        brief.parent.mkdir()
        brief.write_text("""## Goal
Make checkout better for our users.

## Success criteria
It should be good and fast.

## Type
new_feature
""")
        ok, errs = schema.validate_brief(brief)
        assert not ok
    finally:
        shutil.rmtree(tmp)


def test_brief_schema_accepts_valid():
    tmp = _mktmp_project()
    try:
        brief = tmp / "briefs" / "x.md"
        brief.parent.mkdir()
        brief.write_text("""## Goal
Add email receipts on successful checkout.

## Success criteria
Receipt arrives within 10s of payment with no errors.

## Type
new_feature
""")
        ok, errs = schema.validate_brief(brief)
        assert ok, f"errors: {errs}"
    finally:
        shutil.rmtree(tmp)



# ---------- extract_path_token ----------

def test_mark_fallback_logs_ledger_event():
    """v0.5.0: prusik gate mark-fallback appends reviewer_fallback_used event."""
    tmp = _mktmp_project()
    try:
        args = argparse.Namespace(role="brief-critic", feature="feat")
        rc = gate.mark_fallback(args)
        assert rc == 0
        from prusik.ledger import read_all
        events = [r for r in read_all() if r["event"] == "reviewer_fallback_used"]
        assert events
        assert events[-1]["role"] == "brief-critic"
        assert events[-1]["feature"] == "feat"
    finally:
        shutil.rmtree(tmp)


def test_validate_milestone_noop_when_roadmap_not_configured():
    """v0.5.0: no roadmap block → no milestone requirement."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        scope = tmp / "scope.md"
        (tmp / "api").mkdir()
        scope.write_text("""## Goal recap
Test scope without milestone declared.

## Modules touched
- api

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small

## Domains
- backend

## Risks
- Low

## Open questions
- (none)
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert ok, f"no roadmap config → milestone should be optional, got: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_validate_milestone_required_when_roadmap_configured():
    """v0.5.0: roadmap configured → missing ## Milestone fails validation."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config_path = tmp / ".claude" / "sprint-config.yaml"
        cfg_text = config_path.read_text().replace(
            'roadmap:\n  source: ""\n  milestone_pattern: ""',
            'roadmap:\n  source: design/roadmap.md\n  milestone_pattern: "M\\\\d+\\\\.S\\\\d+"',
        )
        config_path.write_text(cfg_text)
        (tmp / "api").mkdir()
        scope = tmp / "scope.md"
        scope.write_text("""## Goal recap
Test scope WITHOUT declared milestone but roadmap is configured.

## Modules touched
- api

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small

## Domains
- backend

## Risks
- Low

## Open questions
- (none)
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert not ok
        assert any("Milestone" in e for e in errs)
    finally:
        shutil.rmtree(tmp)


def test_validate_milestone_matches_pattern():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config_path = tmp / ".claude" / "sprint-config.yaml"
        cfg_text = config_path.read_text().replace(
            'roadmap:\n  source: ""\n  milestone_pattern: ""',
            'roadmap:\n  source: design/roadmap.md\n  milestone_pattern: "M\\\\d+\\\\.S\\\\d+"',
        )
        config_path.write_text(cfg_text)
        (tmp / "api").mkdir()
        scope = tmp / "scope.md"
        scope.write_text("""## Goal recap
Test scope with well-formed milestone declaration.

## Modules touched
- api

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small

## Domains
- backend

## Risks
- Low

## Open questions
- (none)

## Milestone
M1.S1
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert ok, f"well-formed milestone should validate, got: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_validate_milestone_rejects_pattern_mismatch():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        config_path = tmp / ".claude" / "sprint-config.yaml"
        cfg_text = config_path.read_text().replace(
            'roadmap:\n  source: ""\n  milestone_pattern: ""',
            'roadmap:\n  source: design/roadmap.md\n  milestone_pattern: "M\\\\d+\\\\.S\\\\d+"',
        )
        config_path.write_text(cfg_text)
        (tmp / "api").mkdir()
        scope = tmp / "scope.md"
        scope.write_text("""## Goal recap
Test scope with malformed milestone declaration.

## Modules touched
- api

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small

## Domains
- backend

## Risks
- Low

## Open questions
- (none)

## Milestone
phase-one
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert not ok
        assert any("does not match" in e for e in errs)
    finally:
        shutil.rmtree(tmp)


def test_triage_persists_milestone_in_decision():
    """v0.5.0: decisions/<feature>.json scope_summary includes milestone."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        (tmp / "api").mkdir()
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "feat.md").write_text("""## Goal
Add small feature with clear outcome.

## Success criteria
Works within 1 minute of deploy with no errors.

## Type
new_feature
""")
        scope_dir = tmp / "design" / "feat"
        scope_dir.mkdir(parents=True)
        (scope_dir / "scope.md").write_text("""## Goal recap
Test milestone persistence through triage.

## Modules touched
- api

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small

## Domains
- backend

## Risks
- Low

## Open questions
- (none)

## Milestone
M2.S3
""")
        assert triage.run("feat") == 0
        decision = json.loads((tmp / "decisions" / "feat.json").read_text())
        assert decision["scope_summary"].get("milestone") == "M2.S3"
    finally:
        shutil.rmtree(tmp)


def test_digest_by_size_groups_sprint_durations():
    """v0.5.0: prusik digest --by-size reports mean duration per size bucket."""
    tmp = _mktmp_project()
    try:
        from prusik.ledger import append as _append
        _append("sprint_complete", feature="a",
                predicted={"mode": "solo", "size": "S"},
                actual={"mode": "solo", "duration_min": 20})
        _append("sprint_complete", feature="b",
                predicted={"mode": "solo", "size": "S"},
                actual={"mode": "solo", "duration_min": 40})
        _append("sprint_complete", feature="c",
                predicted={"mode": "team", "size": "L"},
                actual={"mode": "team", "duration_min": 180})
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ledger_digest(by_size=True)
        out = buf.getvalue()
        assert "Sprint outcomes by size" in out
        # S bucket: n=2 mean=30; L bucket: n=1 mean=180
        assert "n=  2" in out or "n=2" in out
        assert "mean=  30" in out or "mean=30" in out or "30.0" in out
    finally:
        shutil.rmtree(tmp)


def test_digest_without_by_size_flag_unchanged():
    tmp = _mktmp_project()
    try:
        from prusik.ledger import append as _append
        _append("sprint_complete", feature="a",
                predicted={"mode": "solo", "size": "S"},
                actual={"mode": "solo", "duration_min": 20})
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ledger_digest()
        assert "Sprint outcomes by size" not in buf.getvalue()
    finally:
        shutil.rmtree(tmp)


def test_extract_enum_strips_markdown():
    """v0.4.1: **S**, *S*, `S`, _S_ all extract as S for enum matching."""
    assert schema._extract_enum_value("**S**") == "S"
    assert schema._extract_enum_value("*S*  small thing") == "S"
    assert schema._extract_enum_value("`S`") == "S"
    assert schema._extract_enum_value("_S_") == "S"
    assert schema._extract_enum_value("S  — plain still works") == "S"


def test_scope_validator_accepts_markdown_wrapped_size():
    tmp = _mktmp_project()
    try:
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "foo.py").write_text("")
        scope = tmp / "scope.md"
        scope.write_text("""## Goal recap
Test scope with markdown-wrapped size value.

## Modules touched
- scripts/foo.py

## Blast radius
- (none)

## Related work
- (none)

## Size
**S**

## Domains
- backend

## Risks
- Some risk here.

## Open questions
- (none)
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert ok, f"**S** should validate as size S, got: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_scope_validator_accepts_new_file_prefix():
    """v0.4.1: `+ path` prefix marks new-file; skips existence check."""
    tmp = _mktmp_project()
    try:
        # decisions/ parent exists; oq-auth-flow.md does NOT
        (tmp / "decisions").mkdir()
        scope = tmp / "scope.md"
        scope.write_text("""## Goal recap
Test new-file sprint scenario for validator.

## Modules touched
- + decisions/oq-auth-flow.md
- + decisions/oq-multitenant-db.md

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small

## Domains
- doc

## Risks
- Might be vague.

## Open questions
- (none)
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert ok, f"+ prefix should mark as new-file, got: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_scope_validator_accepts_plus_inside_backticks():
    """v0.5.1 B18: `- \\`+ path\\`` (plus inside backtick-wrap) should work."""
    tmp = _mktmp_project()
    try:
        scope = tmp / "scope.md"
        scope.write_text("""## Goal recap
Greenfield sprint creating a fresh pyproject and source tree.

## Modules touched
- `+ pyproject.toml`
- `+ src/pkg/main.py`

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small greenfield

## Domains
- backend

## Risks
- Might choose wrong layout.

## Open questions
- (none)
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert ok, f"backtick-wrapped + prefix should validate, got: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_scope_validator_accepts_plus_with_bold_wrapper():
    tmp = _mktmp_project()
    try:
        scope = tmp / "scope.md"
        scope.write_text("""## Goal recap
Bold-wrapped path with plus marker for a new file.

## Modules touched
- **+ docker-compose.yml**

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small

## Domains
- infra

## Risks
- Low

## Open questions
- (none)
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert ok, f"bold-wrapped + prefix should validate, got: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_scope_validator_greenfield_no_parent_exists():
    """v0.5.1 B19: new files whose parents don't exist are now accepted."""
    tmp = _mktmp_project()
    try:
        scope = tmp / "scope.md"
        scope.write_text("""## Goal recap
Pure greenfield — nothing on disk yet. Scoping is declarative.

## Modules touched
- + src/unlock_trading/domain/invoice.py
- + tests/domain/test_invoice.py
- + alembic/versions/0001_initial.py

## Blast radius
- (none)

## Related work
- (none)

## Size
L — foundation sprint

## Domains
- backend

## Risks
- Architectural choices may need revisiting.

## Open questions
- Preferred migration tool?
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert ok, f"greenfield paths should validate without mkdir, got: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_scope_validator_non_plus_paths_still_must_exist():
    """Regression guard: removing the parent check must NOT accept typos on
    existing-file bullets (no `+` marker)."""
    tmp = _mktmp_project()
    try:
        scope = tmp / "scope.md"
        scope.write_text("""## Goal recap
Scope claims an existing file that isn't there.

## Modules touched
- scripts/does_not_exist.py

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small

## Domains
- backend

## Risks
- Typo.

## Open questions
- (none)
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert not ok
        # Error message should mention the `+ ` hint
        assert any("+ " in e or "prefix" in e.lower() for e in errs)
    finally:
        shutil.rmtree(tmp)


# Removed in v0.5.1 B19: parent-existence check for `+ `-marked paths dropped.
# Greenfield sprints no longer forced to pre-mkdir stub directories from the
# scoping phase. See test_scope_validator_greenfield_no_parent_exists above
# for the new behavior guard.


def test_extract_path_token_strips_backticks():
    assert schema.extract_path_token("`scripts/foo.py`  — does stuff") == "scripts/foo.py"


def test_extract_path_token_strips_bold_and_italic():
    assert schema.extract_path_token("**api/billing/** touched") == "api/billing/"
    assert schema.extract_path_token("*web/cart/* — added") == "web/cart/"


def test_extract_path_token_strips_trailing_punctuation():
    assert schema.extract_path_token("web/checkout/, related") == "web/checkout/"
    assert schema.extract_path_token("api/foo.py.") == "api/foo.py"


def test_extract_path_token_unwraps_markdown_link():
    assert schema.extract_path_token("[see here](api/foo.py)") == "api/foo.py"


def test_extract_path_token_passthrough_when_plain():
    assert schema.extract_path_token("api/billing/ — touched") == "api/billing/"


def test_scope_validator_accepts_backticked_module_path():
    tmp = _mktmp_project()
    try:
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "foo.py").write_text("")
        scope = tmp / "scope.md"
        scope.write_text("""## Goal recap
Test scope with markdown-formatted paths.

## Modules touched
- `scripts/foo.py`  — does stuff

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small change.

## Domains
- backend

## Risks
- Might break something.

## Open questions
- (none)
""")
        ok, errs = schema.validate_scope(scope, tmp)
        assert ok, f"backticked path should validate, got: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_plan_within_scope_ignores_markdown_wrappers():
    tmp = _mktmp_project()
    try:
        f = "feat"
        (tmp / "design" / f).mkdir(parents=True)
        (tmp / "design" / f / "scope.md").write_text("""## Modules touched
- `api/billing/`
- **web/checkout/**
""")
        (tmp / "design" / f / "plan.md").write_text("""## Modules touched
- api/billing/
- web/checkout/
""")
        # Same modules, different markdown — should not flag scope creep
        assert consistency.plan_within_scope(tmp, f) == []
    finally:
        shutil.rmtree(tmp)



# ---------- deny_bash heredoc handling ----------

def test_strip_heredocs_removes_single_heredoc_body():
    cmd = """cat <<EOF >> file
some prose mentioning git merge and git push
EOF"""
    assert "git merge" not in gate._strip_heredocs(cmd)
    assert "git push" not in gate._strip_heredocs(cmd)


def test_strip_heredocs_preserves_commands_outside_heredoc():
    cmd = """git status
cat <<EOF
git merge in prose
EOF
git log"""
    stripped = gate._strip_heredocs(cmd)
    assert "git status" in stripped
    assert "git log" in stripped
    assert "git merge in prose" not in stripped


def test_strip_heredocs_handles_quoted_delimiter():
    cmd = """cat <<'EOF'
git push inside heredoc
EOF"""
    assert "git push" not in gate._strip_heredocs(cmd)


def test_strip_heredocs_leaves_no_heredocs_alone():
    cmd = "git status && git diff"
    assert gate._strip_heredocs(cmd) == cmd


def test_pre_tool_allows_heredoc_prose_mentioning_forbidden_command():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "solo_execute", "feature": "feat"})
        # Simulate a Bash tool call whose heredoc body mentions git merge
        payload = {
            "tool_name": "Bash",
            "tool_input": {
                "command": "cat <<EOF >> /tmp/test.md\ndiscussion of git merge patterns\nEOF"
            },
        }
        import io
        from contextlib import redirect_stdout
        _stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = gate.pre_tool()
        finally:
            sys.stdin = _stdin_backup
        assert rc == 0
        # No deny payload emitted
        assert buf.getvalue().strip() == "" or '"permissionDecision": "deny"' not in buf.getvalue()
    finally:
        shutil.rmtree(tmp)


def test_pre_tool_blocks_actual_git_merge_command():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "solo_execute", "feature": "feat"})
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git merge main"},
        }
        import io
        from contextlib import redirect_stdout
        _stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = gate.pre_tool()
        finally:
            sys.stdin = _stdin_backup
        assert rc == 0
        out = json.loads(buf.getvalue())
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    finally:
        shutil.rmtree(tmp)



# ---------- triage ----------

def _make_brief_and_scope(tmp, feature, brief_text, scope_text, existing_dirs=()):
    (tmp / "briefs").mkdir(exist_ok=True)
    (tmp / "briefs" / f"{feature}.md").write_text(brief_text)
    scope_dir = tmp / "design" / feature
    scope_dir.mkdir(parents=True, exist_ok=True)
    for d in existing_dirs:
        (tmp / d).mkdir(parents=True, exist_ok=True)
    (scope_dir / "scope.md").write_text(scope_text)


def test_triage_routes_bug_fix_to_solo():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        _make_brief_and_scope(
            tmp, "bug",
            """## Goal
Fix crash on empty cart checkout.

## Success criteria
No crashes within 1 hour of deploy across at least 10 checkouts.

## Type
bug_fix
""",
            """## Goal recap
Fix the empty cart crash.

## Modules touched
- api

## Blast radius
- (none)

## Related work
- (none)

## Size
S — one function change.

## Domains
- backend

## Risks
- May mask a deeper validation bug.

## Open questions
- (none)
""",
            existing_dirs=["api"],
        )
        assert triage.run("bug") == 0
        decision = json.loads((tmp / "decisions" / "bug.json").read_text())
        assert decision["mode"] == "solo"
    finally:
        shutil.rmtree(tmp)


def test_triage_routes_large_new_feature_to_team():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        _make_brief_and_scope(
            tmp, "big",
            """## Goal
Build a multi-tenant billing system.

## Success criteria
Supports at least 100 tenants with under 200ms latency.

## Type
new_feature
""",
            """## Goal recap
Multi-tenant billing.

## Modules touched
- api
- web
- infra

## Blast radius
- all

## Related work
- (none)

## Size
L — multi-service.

## Domains
- backend
- frontend
- infra

## Risks
- Tenant isolation bugs.

## Open questions
- Billing provider?
""",
            existing_dirs=["api", "web", "infra"],
        )
        assert triage.run("big") == 0
        decision = json.loads((tmp / "decisions" / "big.json").read_text())
        assert decision["mode"] == "team"
    finally:
        shutil.rmtree(tmp)



# ---------- discovery plugins ----------

def test_discovery_multi_language():
    tmp = _mktmp_project()
    try:
        (tmp / "pkg").mkdir()
        (tmp / "pkg" / "a.py").write_text("import os\nfrom json import loads\n")
        (tmp / "web").mkdir()
        (tmp / "web" / "app.ts").write_text("""
import { foo } from './bar';
import React from 'react';
const lodash = require('lodash');
""")
        (tmp / "svc").mkdir()
        (tmp / "svc" / "main.go").write_text("""
package main

import "fmt"
import (
    "net/http"
    "encoding/json"
)
""")
        assert discovery.dep_graph() == 0
        graph = json.loads((tmp / ".sprint" / "dep-graph.json").read_text())
        assert "python" in graph["stats"]["by_language"]
        assert "typescript" in graph["stats"]["by_language"]
        assert "go" in graph["stats"]["by_language"]
        ts_deps = graph["forward"]["web/app.ts"]
        assert "react" in ts_deps
        assert "lodash" in ts_deps
        assert "./bar" in ts_deps
        go_deps = graph["forward"]["svc/main.go"]
        assert "fmt" in go_deps
        assert "net/http" in go_deps
        assert "encoding/json" in go_deps
    finally:
        shutil.rmtree(tmp)


def test_blast_radius():
    tmp = _mktmp_project()
    try:
        (tmp / "pkg").mkdir()
        (tmp / "pkg" / "a.py").write_text("x = 1\n")
        (tmp / "pkg" / "b.py").write_text("from pkg import a\n")
        (tmp / "pkg" / "c.py").write_text("import pkg.a\n")
        discovery.dep_graph()
        hits = discovery.blast_radius("pkg")
        assert "pkg/b.py" in hits
        assert "pkg/c.py" in hits
    finally:
        shutil.rmtree(tmp)



# ---------- deny_commands (v0.3.5) ----------

def test_command_denied_exact_match():
    assert gate._command_denied("git push origin main", "git push") is True


def test_command_denied_quoted_string_bypass():
    # prose inside a quoted arg should NOT match
    assert gate._command_denied("echo 'git push'", "git push") is False


def test_command_denied_heredoc_prose_bypass():
    # heredoc-stripped body — engine strips BEFORE checking, but test standalone too
    cmd = "cat <<EOF >> x.md\ngit merge discussion\nEOF"
    assert gate._command_denied(gate._strip_heredocs(cmd), "git merge") is False


def test_command_denied_second_statement_still_caught():
    assert gate._command_denied("git status && git push", "git push") is True
    assert gate._command_denied("ls; git merge main", "git merge") is True


def test_command_denied_unrelated_command_passes():
    assert gate._command_denied("git status", "git push") is False
    assert gate._command_denied("grep foo bar", "git push") is False


def test_command_denied_multiple_spaces_collapse():
    assert gate._command_denied("git    push   origin", "git push") is True



# ---------- prusik agents doctor (v0.3.5) ----------

def test_agents_doctor_flags_yaml_list_tools():
    tmp = _mktmp_project()
    try:
        agents = tmp / ".claude" / "agents"
        agents.mkdir(parents=True)
        # Buggy file (v0.3.1 style)
        (agents / "bad.md").write_text("""---
name: bad
description: Buggy one
tools: [Read, Glob]
---
body
""")
        # Good file
        (agents / "good.md").write_text("""---
name: good
description: Clean one
tools: Read, Glob
---
body
""")
        rc = agents_doctor.doctor(tmp)
        assert rc == 1  # issues found → nonzero
    finally:
        shutil.rmtree(tmp)


def test_agents_doctor_passes_clean_set():
    tmp = _mktmp_project()
    try:
        agents = tmp / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "ok1.md").write_text("""---
name: ok1
description: Fine
tools: Read
---
body
""")
        (agents / "ok2.md").write_text("""---
name: ok2
description: Also fine
---
body
""")
        rc = agents_doctor.doctor(tmp)
        assert rc == 0
    finally:
        shutil.rmtree(tmp)


def test_agents_doctor_flags_name_mismatch():
    tmp = _mktmp_project()
    try:
        agents = tmp / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "cartographer.md").write_text("""---
name: mapmaker
description: Name does not match filename
---
body
""")
        rc = agents_doctor.doctor(tmp)
        assert rc == 1
    finally:
        shutil.rmtree(tmp)


def test_agents_doctor_flags_declared_output_without_write():
    """v0.5.2: role with `**Output:** some-file.md` but no Write in tools → FAIL."""
    tmp = _mktmp_project()
    try:
        agents = tmp / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "mismatched.md").write_text("""---
name: mismatched
description: Claims to produce a file but can't actually write.
tools: Read, Glob, Grep
---

**Output: reports/{feature}/mismatched.txt**

I claim to produce this file but I don't have Write!
""")
        rc = agents_doctor.doctor(tmp)
        assert rc == 1
    finally:
        shutil.rmtree(tmp)


def test_agents_doctor_accepts_write_or_edit_for_output():
    """Role with Output + Write passes. Role with Output + Edit passes too."""
    tmp = _mktmp_project()
    try:
        agents = tmp / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "ok-write.md").write_text("""---
name: ok-write
description: Has Write.
tools: Read, Write, Glob
---

**Output: reports/{feature}/ok.txt**
""")
        (agents / "ok-edit.md").write_text("""---
name: ok-edit
description: Has Edit.
tools: Read, Edit, Glob
---

**Output: reports/{feature}/ok.txt**
""")
        rc = agents_doctor.doctor(tmp)
        assert rc == 0
    finally:
        shutil.rmtree(tmp)


def test_agents_doctor_shipped_templates_all_pass():
    """Regression guard: every agent file in prusik/templates/.claude/agents/
    must pass the doctor check."""
    import tempfile as _tf
    tmp = Path(_tf.mkdtemp(prefix="kit-doctor-"))
    try:
        # Mirror the shipped templates as if they were installed
        src = Path(__file__).parent.parent / "prusik" / "templates" / ".claude" / "agents"
        dest = tmp / ".claude" / "agents"
        dest.mkdir(parents=True)
        for f in src.glob("*.md"):
            shutil.copy(f, dest / f.name)
        rc = agents_doctor.doctor(tmp)
        assert rc == 0, "shipped templates must all pass agents-doctor"
    finally:
        shutil.rmtree(tmp)


def test_permissions_audit_passes_on_fresh_init():
    """v0.5.3: shipped settings.json template includes the baseline."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        rc = kit_permissions.audit(tmp)
        assert rc == 0
    finally:
        shutil.rmtree(tmp)


def test_permissions_audit_flags_minimal_allow_list():
    """Minimal allow list (only Bash(prusik *)) should fail audit."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Truncate settings.json's allow list to just one entry
        settings = tmp / ".claude" / "settings.json"
        data = json.loads(settings.read_text())
        data["permissions"]["allow"] = ["Bash(prusik *)"]
        settings.write_text(json.dumps(data, indent=2))
        rc = kit_permissions.audit(tmp)
        assert rc == 1
    finally:
        shutil.rmtree(tmp)


def test_permissions_audit_combines_settings_and_local():
    """Entries in settings.local.json count toward the audit too."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Move all but one entry out of settings.json into settings.local.json
        settings = tmp / ".claude" / "settings.json"
        data = json.loads(settings.read_text())
        all_entries = data["permissions"]["allow"]
        data["permissions"]["allow"] = all_entries[:1]
        settings.write_text(json.dumps(data, indent=2))
        local = tmp / ".claude" / "settings.local.json"
        local.write_text(json.dumps({
            "permissions": {"allow": all_entries[1:]}
        }, indent=2))
        rc = kit_permissions.audit(tmp)
        assert rc == 0  # combined coverage is full
    finally:
        shutil.rmtree(tmp)


def test_agents_doctor_handles_no_agents_dir():
    tmp = _mktmp_project()
    try:
        rc = agents_doctor.doctor(tmp)
        assert rc == 1
    finally:
        shutil.rmtree(tmp)



# ---------- v0.3.8 bash redirect gate ----------

def test_bash_redirect_targets_extracts_simple():
    assert "foo.log" in gate._bash_redirect_targets("echo hi > foo.log")
    assert "foo.log" in gate._bash_redirect_targets("echo hi >> foo.log")
    assert "foo.log" in gate._bash_redirect_targets("cmd &> foo.log")
    assert "err.log" in gate._bash_redirect_targets("cmd 2> err.log")


def test_bash_redirect_targets_ignores_python_ge_operator():
    """v0.3.11 fix: `>=` inside `python -c "..."` must not match."""
    cmd = """python3 -c "x = re.search(r'\\d{3}', s); assert len(backlog) >= 30" """
    assert gate._bash_redirect_targets(cmd) == []


def test_bash_redirect_targets_ignores_python_comparison_in_c_payload():
    cmd = """python3 -c "assert x > 3 and y == 5" """
    assert gate._bash_redirect_targets(cmd) == []


def test_bash_redirect_targets_ignores_for_loop_colon():
    cmd = """python3 -c "for i, t in enumerate(BACKLOG, 1): print(i, t)" """
    assert gate._bash_redirect_targets(cmd) == []


def test_bash_redirect_targets_ignores_uv_run_payload():
    cmd = """uv run python -c "print(len(backlog) >= 84)" """
    assert gate._bash_redirect_targets(cmd) == []


def test_bash_redirect_targets_catches_real_redirect_after_c_payload():
    """Regression guard: stripping -c payload must not eat actual redirects."""
    cmd = """python3 -c "print('hi')" > real-output.log"""
    assert "real-output.log" in gate._bash_redirect_targets(cmd)


def test_looks_like_file_target_rejects_operators():
    assert gate._looks_like_file_target("=30") is False
    assert gate._looks_like_file_target(":foo") is False
    assert gate._looks_like_file_target("(x") is False
    assert gate._looks_like_file_target("=") is False


def test_looks_like_file_target_accepts_paths():
    assert gate._looks_like_file_target("/abs/path") is True
    assert gate._looks_like_file_target("./rel.txt") is True
    assert gate._looks_like_file_target("~/home.log") is True
    assert gate._looks_like_file_target("subdir/file.yaml") is True
    assert gate._looks_like_file_target("logfile") is True
    assert gate._looks_like_file_target("out.log") is True


def test_bash_redirect_targets_extracts_tee():
    assert "bar.log" in gate._bash_redirect_targets("echo | tee bar.log")
    assert "bar.log" in gate._bash_redirect_targets("echo | tee -a bar.log")


def test_bash_redirect_targets_skips_dev_null():
    targets = gate._bash_redirect_targets("cmd > /dev/null 2>&1")
    assert "/dev/null" not in targets


def test_bash_redirect_targets_handles_quoted_paths():
    targets = gate._bash_redirect_targets('echo hi > "path with spaces.log"')
    assert "path with spaces.log" in targets


def test_pre_tool_blocks_bash_redirect_outside_writable():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "scoping", "feature": "feat"})
        # scoping only writes design/feat/scope.md — redirect to api/foo.py
        # must be blocked even though it's a Bash call, not Write/Edit.
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo hi > api/foo.py"},
        }
        import io
        from contextlib import redirect_stdout
        _stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = gate.pre_tool()
        finally:
            sys.stdin = _stdin_backup
        assert rc == 0
        out = json.loads(buf.getvalue())
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "api/foo.py" in out["hookSpecificOutput"]["permissionDecisionReason"]
    finally:
        shutil.rmtree(tmp)


def test_pre_tool_allows_bash_redirect_to_always_writable_bridge():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "scoping", "feature": "feat"})
        # Bridge path is in always_writable default
        bridge = str(Path.home() / ".claude" / "prusik" / "bridges" / "x" / "bridge.md")
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": f"echo hi >> {bridge}"},
        }
        import io
        from contextlib import redirect_stdout
        _stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = gate.pre_tool()
        finally:
            sys.stdin = _stdin_backup
        assert rc == 0
        # Should NOT deny
        out = buf.getvalue().strip()
        if out:
            payload_out = json.loads(out)
            assert payload_out.get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    finally:
        shutil.rmtree(tmp)



# ---------- v0.3.8 prusik pause / resume ----------

def test_pause_marker_created():
    tmp = _mktmp_project()
    try:
        kit_pause.pause()
        assert kit_pause.is_paused()
    finally:
        shutil.rmtree(tmp)


def test_resume_removes_marker():
    tmp = _mktmp_project()
    try:
        kit_pause.pause()
        kit_pause.resume()
        assert not kit_pause.is_paused()
    finally:
        shutil.rmtree(tmp)


def test_stop_hook_skips_enforcement_when_paused():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "scoping", "feature": "feat"})
        # No exit artifacts present; Stop hook WOULD normally block
        # But prusik pause flips that off.
        kit_pause.pause()
        payload = {"stop_hook_active": False}
        import io
        from contextlib import redirect_stdout
        _stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = gate.stop()
        finally:
            sys.stdin = _stdin_backup
        assert rc == 0
        # No block payload emitted
        assert "decision" not in buf.getvalue() or '"block"' not in buf.getvalue()
    finally:
        shutil.rmtree(tmp)


def test_sprint_start_wipes_stale_worktrees():
    """v0.3.9: sprint-start must clear worktrees/ subdirs from prior sprints."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        # Valid brief + satisfied pre-sprint gates
        (tmp / "briefs").mkdir()
        (tmp / "briefs" / "feat.md").write_text(_VALID_BRIEF)
        (tmp / "reports" / "feat").mkdir(parents=True)
        (tmp / "reports" / "feat" / "brief-critique.txt").write_text("PASS\n")
        (tmp / ".sprint").mkdir(exist_ok=True)
        (tmp / ".sprint" / "dep-graph.json").write_text(json.dumps(
            {"forward": {}, "reverse": {}, "stats": {"by_language": {}}}
        ))
        (tmp / "design").mkdir()
        (tmp / "design" / "map.md").write_text("map")
        discovery.fingerprint_map()

        # Seed a contaminated worktree from a "prior sprint"
        contaminated = tmp / "worktrees" / "solo" / "fixtures" / "leftover.yaml"
        contaminated.parent.mkdir(parents=True)
        contaminated.write_text("leftover: true\n")
        (tmp / "worktrees" / "keepme.md").write_text("README at worktrees/ root\n")

        assert contaminated.exists()
        args = argparse.Namespace(feature="feat")
        rc = gate.sprint_start(args)
        assert rc == 0
        # Subdirectory gone
        assert not contaminated.exists()
        assert not (tmp / "worktrees" / "solo").exists()
        # File directly under worktrees/ preserved (not a subdir)
        assert (tmp / "worktrees" / "keepme.md").exists()
    finally:
        shutil.rmtree(tmp)


def test_clean_worktrees_handles_missing_dir():
    tmp = _mktmp_project()
    try:
        assert gate._clean_worktrees(tmp) == []
    finally:
        shutil.rmtree(tmp)


def test_rewind_blocked_without_flag():
    """v0.3.10: advance to earlier phase requires --allow-rewind."""
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
        args = argparse.Namespace(phase="solo_execute", feature="feat", allow_rewind=False)
        rc = gate.advance(args)
        assert rc == 2, "rewind without flag must be refused"
        # State unchanged
        assert phases.current_sprint_state()["phase"] == "reviewing"
    finally:
        shutil.rmtree(tmp)


def test_rewind_allowed_with_flag_records_phase_rewind_event():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "reviewing", "feature": "feat"})
        args = argparse.Namespace(phase="solo_execute", feature="feat", allow_rewind=True)
        rc = gate.advance(args)
        assert rc == 0
        assert phases.current_sprint_state()["phase"] == "solo_execute"
        # Ledger has phase_rewind, not phase_advance
        from prusik.ledger import read_all
        events = [r for r in read_all()
                  if r.get("from_phase") == "reviewing"
                  and r.get("to_phase") == "solo_execute"]
        assert any(e["event"] == "phase_rewind" for e in events)
        assert not any(e["event"] == "phase_advance" for e in events)
    finally:
        shutil.rmtree(tmp)


def test_forward_advance_still_phase_advance_event():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        # scope.md + scope-approval so scoping→triage has satisfied artifacts
        (tmp / "api").mkdir()
        scope = tmp / "design" / "feat" / "scope.md"
        scope.parent.mkdir(parents=True)
        scope.write_text("""## Goal recap
Short scope for a forward-advance test case.

## Modules touched
- api

## Blast radius
- (none)

## Related work
- (none)

## Size
S — small.

## Domains
- backend

## Risks
- Low.

## Open questions
- (none)
""")
        approval = tmp / "reports" / "feat" / "scope-approval.txt"
        approval.parent.mkdir(parents=True)
        approval.write_text("APPROVED\nlooks good\n")
        phases.set_sprint_state({"phase": "scoping", "feature": "feat"})
        args = argparse.Namespace(phase="triage", feature="feat", allow_rewind=False)
        rc = gate.advance(args)
        assert rc == 0
        from prusik.ledger import read_all
        events = [r for r in read_all()
                  if r.get("from_phase") == "scoping"
                  and r.get("to_phase") == "triage"]
        assert any(e["event"] == "phase_advance" for e in events)
    finally:
        shutil.rmtree(tmp)


def test_is_rewind_canonical_order():
    assert phases.is_rewind("reviewing", "solo_execute") is True
    assert phases.is_rewind("integrating", "scoping") is True
    assert phases.is_rewind("scoping", "triage") is False
    assert phases.is_rewind("scoping", "scoping") is False
    # Unknown phase (e.g. custom extension) → don't enforce
    assert phases.is_rewind("custom-phase", "scoping") is False


def test_clean_worktrees_reports_cleaned_names():
    tmp = _mktmp_project()
    try:
        wt = tmp / "worktrees"
        (wt / "backend-builder").mkdir(parents=True)
        (wt / "test-writer").mkdir()
        cleaned = gate._clean_worktrees(tmp)
        assert set(cleaned) == {"backend-builder", "test-writer"}
        assert not (wt / "backend-builder").exists()
    finally:
        shutil.rmtree(tmp)


def test_stop_hook_enforces_after_resume():
    tmp = _mktmp_project()
    try:
        _copy_sprint_config(tmp)
        phases.set_sprint_state({"phase": "scoping", "feature": "feat"})
        kit_pause.pause()
        kit_pause.resume()
        # Now exit-artifact enforcement is back on; scoping has no artifacts
        # so Stop should block.
        payload = {"stop_hook_active": False}
        import io
        from contextlib import redirect_stdout
        _stdin_backup = sys.stdin
        sys.stdin = io.StringIO(json.dumps(payload))
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = gate.stop()
        finally:
            sys.stdin = _stdin_backup
        assert rc == 2
        out = json.loads(buf.getvalue())
        assert out.get("decision") == "block"
    finally:
        shutil.rmtree(tmp)



# ---------- build-report meta-artifact carve-out (v0.5.6) ----------

def _scope_and_plan_for_meta_test(tmp: Path) -> None:
    """Write a scope+plan pair where builder writes land at both
    plan-listed paths and meta-artifact paths."""
    (tmp / "src").mkdir()
    (tmp / "src" / "api.py").write_text("#\n")
    scope = tmp / "design" / "feat" / "scope.md"
    scope.parent.mkdir(parents=True)
    scope.write_text("""## Goal recap
Ship the API bit.

## Modules touched
- `src/api.py` — wire new endpoint

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
    (tmp / "design" / "feat" / "plan.md").write_text("""## Goal recap
Ship the API bit.

## Modules touched
- `src/api.py` — new endpoint

## Build order
- 1. endpoint

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


def test_builder_writes_allows_build_report_meta_artifact():
    """v0.5.6: each builder's reports/<feature>/build-<role>.txt is a
    sanctioned meta-artifact — /sprint-run requires it, plan.md rightly
    doesn't list it. Must not trip the cross-artifact gate."""
    tmp = _mktmp_project()
    try:
        _scope_and_plan_for_meta_test(tmp)
        # backend-builder writes both a plan-listed file and its build report
        wt = tmp / "worktrees" / "backend-builder"
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "api.py").write_text("# new endpoint\n")
        (wt / "reports" / "feat").mkdir(parents=True)
        (wt / "reports" / "feat" / "build-backend.txt").write_text("PASS\nrationale\n")

        errs = consistency.builder_writes_within_plan(tmp, "feat")
        assert errs == [], f"build-backend.txt should be allowlisted: {errs}"
    finally:
        shutil.rmtree(tmp)


def test_builder_writes_still_flags_non_build_reports_files():
    """The carve-out is narrow — only reports/<feature>/build-*.txt is
    allowlisted. Other files under reports/ must still trip the gate,
    so random junk under worktrees/<role>/reports/ doesn't get a free pass."""
    tmp = _mktmp_project()
    try:
        _scope_and_plan_for_meta_test(tmp)
        wt = tmp / "worktrees" / "backend-builder"
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "api.py").write_text("#\n")
        (wt / "reports" / "feat").mkdir(parents=True)
        # Not a build report — a random artifact that should still be flagged
        (wt / "reports" / "feat" / "scratch.txt").write_text("notes\n")

        errs = consistency.builder_writes_within_plan(tmp, "feat")
        assert errs, "non-build-report under reports/ should still flag"
        assert any("scratch.txt" in e for e in errs), errs
    finally:
        shutil.rmtree(tmp)


def test_builder_writes_allowlist_scoped_to_current_feature():
    """`reports/<feature>/build-*.txt` — the feature segment must match.
    A builder dropping a build report under a DIFFERENT feature's reports/
    tree is still a violation (shouldn't happen, but guard against it)."""
    tmp = _mktmp_project()
    try:
        _scope_and_plan_for_meta_test(tmp)
        wt = tmp / "worktrees" / "backend-builder"
        (wt / "src").mkdir(parents=True)
        (wt / "src" / "api.py").write_text("#\n")
        # Wrong feature name — should NOT be allowlisted
        (wt / "reports" / "other-feat").mkdir(parents=True)
        (wt / "reports" / "other-feat" / "build-backend.txt").write_text("PASS\n")

        errs = consistency.builder_writes_within_plan(tmp, "feat")
        assert errs, "build-*.txt under other feature dir should flag"
    finally:
        shutil.rmtree(tmp)



# ---------- v0.6.3 pause-with-reason (B8) ----------

def test_pause_accepts_reason_and_records_to_marker():
    tmp = _mktmp_project()
    try:
        rc = kit_pause.pause(reason="yielding to user for checkpoint")
        assert rc == 0
        state = kit_pause._read_marker(tmp)
        assert state is not None
        assert state["reason"] == "yielding to user for checkpoint"
        assert "started_at" in state
        # Ledger event recorded
        from prusik.ledger import read_all
        events = [r for r in read_all() if r["event"] == "pause_started"]
        assert events
        assert events[-1]["reason"] == "yielding to user for checkpoint"
    finally:
        shutil.rmtree(tmp)


def test_pause_without_reason_works_unchanged():
    """Backward compat: prusik pause with no reason still works."""
    tmp = _mktmp_project()
    try:
        rc = kit_pause.pause()
        assert rc == 0
        assert kit_pause.is_paused(tmp)
        assert kit_pause.paused_reason(tmp) is None
    finally:
        shutil.rmtree(tmp)


def test_pause_legacy_empty_marker_still_recognized():
    """Pre-v0.6.3 markers were touch-empty files. New code must still
    treat them as paused (no reason)."""
    tmp = _mktmp_project()
    try:
        marker = tmp / ".sprint" / "paused"
        marker.parent.mkdir()
        marker.touch()  # legacy form: empty file
        assert kit_pause.is_paused(tmp)
        assert kit_pause.paused_reason(tmp) is None
        state = kit_pause._read_marker(tmp)
        assert state == {}
    finally:
        shutil.rmtree(tmp)


def test_resume_records_duration_and_reason_in_ledger():
    tmp = _mktmp_project()
    try:
        kit_pause.pause(reason="brief test")
        time.sleep(0.05)  # ensure non-zero elapsed
        rc = kit_pause.resume()
        assert rc == 0
        from prusik.ledger import read_all
        ends = [r for r in read_all() if r["event"] == "pause_ended"]
        assert ends
        assert ends[-1]["reason"] == "brief test"
        assert ends[-1].get("duration_sec") is not None
    finally:
        shutil.rmtree(tmp)


def test_pause_cli_accepts_variadic_reason():
    """`prusik pause yielding to user — checkpoint` (unquoted, multi-word)
    must NOT trip argparse's unrecognized-arguments error."""
    tmp = _mktmp_project()
    try:
        sys.argv = ["prusik", "pause", "yielding", "to", "user", "—", "checkpoint"]
        from prusik.__main__ import main as _main
        rc = _main()
        assert rc == 0
        reason = kit_pause.paused_reason(tmp)
        assert reason == "yielding to user — checkpoint"
    finally:
        shutil.rmtree(tmp)


def test_pause_cli_no_args_still_works():
    """`prusik pause` bare (zero positional) must continue to work."""
    tmp = _mktmp_project()
    try:
        sys.argv = ["prusik", "pause"]
        from prusik.__main__ import main as _main
        rc = _main()
        assert rc == 0
        assert kit_pause.is_paused(tmp)
        assert kit_pause.paused_reason(tmp) is None
    finally:
        shutil.rmtree(tmp)


def test_print_status_shows_pause_reason():
    """When paused with a reason, prusik status must surface it."""
    tmp = _mktmp_project()
    try:
        phases.set_sprint_state({"phase": "scoping", "feature": "feat"}, root=tmp)
        kit_pause.pause(reason="awaiting human ack on scope.md")
        # Capture stdout
        import io as _io
        import contextlib as _ctx
        buf = _io.StringIO()
        with _ctx.redirect_stdout(buf):
            phases.print_status()
        out = buf.getvalue()
        assert "Paused:   yes" in out
        assert "awaiting human ack on scope.md" in out
    finally:
        shutil.rmtree(tmp)


def test_sprint_pause_slash_command_forwards_arguments():
    """v0.6.3 (B8): /sprint-pause prompt must instruct $ARGUMENTS forwarding
    so the slash command surface and prusik CLI agree on the contract."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "commands" / "sprint-pause.md")
    text = p.read_text()
    assert "prusik pause $ARGUMENTS" in text, \
        "/sprint-pause must forward $ARGUMENTS to prusik pause"



# ---------- v0.6.4 slug validation (B9) ----------

def test_slug_validator_accepts_clean_slugs():
    from prusik.__main__ import _slug
    for good in ["domain-schema", "cli-foundation", "feat", "m1-s3-stitcher",
                 "abc123", "a", "feat-2026"]:
        assert _slug(good) == good


def test_slug_validator_rejects_garbage():
    from prusik.__main__ import _slug
    bads = [
        "—",                             # em dash (the actual B9 trigger)
        "domain-schema —",               # slug + trailing prose
        "Domain-Schema",                 # uppercase
        "domain schema",                 # space
        "domain_schema",                 # underscore (prusik convention is hyphen)
        "1domain",                       # starts with digit
        "-domain",                       # starts with hyphen
        "domain.schema",                 # dot
        "domain/schema",                 # slash (path injection guard)
        "domain;ls",                     # shell metachar
        "",                              # empty
        "  ",                            # whitespace only
    ]
    for bad in bads:
        try:
            _slug(bad)
            assert False, f"should have rejected: {bad!r}"
        except argparse.ArgumentTypeError as e:
            assert "Invalid feature slug" in str(e)


def test_kit_gate_rejects_invalid_feature_slug_at_cli():
    """End-to-end: argparse rejects garbage --feature with exit 2."""
    tmp = _mktmp_project()
    try:
        sys.argv = ["prusik", "gate", "advance", "scoping", "--feature", "—"]
        from prusik.__main__ import main as _main
        try:
            _main()
            assert False, "should have raised SystemExit"
        except SystemExit as e:
            assert e.code == 2  # argparse error exit code
    finally:
        shutil.rmtree(tmp)


def test_sprint_start_positional_also_validates_slug():
    """sprint-start takes a positional <feature>, not --feature; the
    validator must apply there too (B9 defense-in-depth)."""
    tmp = _mktmp_project()
    try:
        sys.argv = ["prusik", "gate", "sprint-start", "—"]
        from prusik.__main__ import main as _main
        try:
            _main()
            assert False, "should have raised SystemExit"
        except SystemExit as e:
            assert e.code == 2
    finally:
        shutil.rmtree(tmp)


def test_sprint_run_template_has_preflight_validation():
    """v0.6.4 (B9): /sprint-run prompt must include explicit Step −1 that
    validates the slug shape before any other action. Catches the bug at
    the slash-command surface, where cost-of-failure is highest."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "commands" / "sprint-run.md")
    text = p.read_text()
    # Pre-flight section header (any reasonable variant)
    assert "pre-flight" in text.lower()
    # Slug regex pinned in the prompt
    assert "[a-z][a-z0-9-]*" in text or "[a-z0-9][a-z0-9-]*" in text
    # Tells agent to STOP and surface the error
    assert "STOP" in text
    # Names the failure modes from B9 (em dash + free-form prose)
    lower = text.lower()
    assert "em-dash" in lower or "em dash" in lower or "—" in text


def test_sprint_run_preflight_diagnoses_skill_invocation_separately():
    """v0.6.5 (B10): when $ARGUMENTS is empty (likely Skill-tool invocation that
    didn't forward args), the pre-flight error must point at that specific
    failure mode and the user-typed workaround. Distinct from the prose-in-args
    case (Case B), where the failure mode is different."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "commands" / "sprint-run.md")
    text = p.read_text()
    # Case A specifically named
    assert "Case A" in text and "empty" in text.lower()
    # Case B specifically named
    assert "Case B" in text
    # Skill tool invocation pattern explicitly mentioned (now in B11-corrected
    # form — points at prusik's own pre-v0.6.6 $1-vs-$ARGUMENTS bug as the
    # primary suspect, with Skill tool as secondary)
    assert "Skill" in text
    # B11 corrects B10 — both bridge references should be findable
    assert "B11" in text


def test_all_command_templates_use_dollar_arguments_not_dollar_one():
    """v0.6.6 (B11): prusik slash-command templates must use $ARGUMENTS, never
    bare $1/$2/$@/$* placeholders. CC substitutes $ARGUMENTS but does NOT
    substitute $1; using $1 leaves empty slots in the rendered prompt and
    silently breaks every prusik command embedded in the template.

    Discovered when /sprint-run domain-schema rendered with empty slots
    everywhere, after we'd already shipped /sprint-pause with $ARGUMENTS
    and seen it work. Same template region, different placeholder, different
    behavior. Lock the convention here."""
    cmds = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
            / "commands")
    # Match $1, $2, etc. as standalone tokens — but EXCLUDE prose mentions
    # in code spans / quoted historical context (e.g., "templates used $1").
    # The rule: any $<digit> appearing OUTSIDE a backtick-quoted span or
    # outside a documentation paragraph that explicitly cites the pre-v0.6.6
    # bug.
    placeholder_re = re.compile(r"(?<![`\\])\$(\d+)\b")
    offenders: list[str] = []
    for f in sorted(cmds.glob("*.md")):
        for i, line in enumerate(f.read_text().splitlines(), 1):
            # Ignore lines that are clearly prose-about-the-bug (mention "used
            # $1" or "v0.6.6" as historical context).
            if "pre-v0.6.6" in line or "used `$1`" in line or "used $1" in line:
                continue
            for m in placeholder_re.finditer(line):
                # Allow $<digit> inside backtick-quoted spans (prose example).
                # Crude check: count backticks before the match position.
                pre = line[:m.start()]
                if pre.count("`") % 2 == 1:
                    continue
                offenders.append(f"{f.name}:{i}: {line.strip()}")
    assert not offenders, (
        "kit slash-command templates must use $ARGUMENTS not $<digit>:\n  "
        + "\n  ".join(offenders)
    )


def test_sprint_advance_template_parses_arguments_into_two_tokens():
    """sprint-advance is the only multi-arg command; its template must
    instruct the agent to whitespace-split $ARGUMENTS into two tokens
    (phase + feature) rather than passing $ARGUMENTS verbatim to the CLI
    (which expects positional <phase> + --feature flag, not two positionals)."""
    p = (Path(__file__).parent.parent / "prusik" / "templates" / ".claude"
         / "commands" / "sprint-advance.md")
    text = p.read_text()
    assert "$ARGUMENTS" in text, "must reference $ARGUMENTS"
    # The prose must explicitly tell the agent to split into two tokens
    assert ("split" in text.lower() or "parse" in text.lower())
    assert "two tokens" in text.lower() or "exactly two" in text.lower()
    # Must NOT instruct passing $ARGUMENTS verbatim to prusik gate advance
    # (would mismatch the CLI shape)
    assert "prusik gate advance $ARGUMENTS" not in text


