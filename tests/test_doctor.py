"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: doctor.

Run: uv run python -m pytest tests/test_doctor.py -v
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


# ---------- v0.8.5 — prusik doctor (5-subsystem scoring + drift detection) ----------
#
# Phase 1.1 of the "drop-in 360 harness" plan. Self-assessment via 5
# subsystems from learn-harness-engineering's framework, scored 0-5
# each. Drift detection compares current detect_project() output
# against manifest's recorded snapshot from v0.8.4 init.

from prusik import doctor as kit_doctor  # noqa: E402


def test_v085_doctor_refuses_when_no_claude_dir():
    """`prusik doctor` exits 1 with a clear message when run outside a
    kit-installed project."""
    tmp = _mktmp_project()
    try:
        rc = kit_doctor.run()
        assert rc == 1, "doctor must refuse to score when no .claude/ exists"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_doctor_runs_clean_on_fresh_init():
    """Fresh prusik init produces a doctor scorecard with zero crashes."""
    tmp = _mktmp_project()
    try:
        rc = kit_init.run()
        assert rc == 0
        rc = kit_doctor.run()
        assert rc == 0
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_doctor_json_mode_returns_valid_json():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Capture stdout via pytest's capsys would be cleaner but we
        # just want to verify it doesn't crash and produces parseable
        # output. Simulate by importing directly and re-routing print.
        import io
        import contextlib  # noqa: E402
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = kit_doctor.run(json_output=True)
        assert rc == 0
        out = json.loads(buf.getvalue())
        assert "scores" in out
        assert "drift" in out
        assert "lowest" in out
        for axis in ("instructions", "state", "verification", "scope",
                     "session_lifecycle"):
            assert axis in out["scores"]
            assert "score" in out["scores"][axis]
            assert isinstance(out["scores"][axis]["score"], int)
            assert 0 <= out["scores"][axis]["score"] <= 5
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_score_instructions_full_library_scores_high():
    """Project with CLAUDE.md + full role library + commands + schemas
    should score at the top of the Instructions axis."""
    tmp = _mktmp_project()
    try:
        kit_init.run()  # Installs full role library by default
        # Add a CLAUDE.md
        (tmp / "CLAUDE.md").write_text("# Project\n\n## Conventions\n\n- Be careful\n")
        score, evidence = kit_doctor._score_instructions(tmp, tmp / ".claude")
        assert score >= 4, f"full library + CLAUDE.md should score ≥4, got {score}"
        assert any("CLAUDE.md" in e for e in evidence)
        assert any("full role library" in e for e in evidence)
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_score_instructions_empty_scores_low():
    tmp = _mktmp_project()
    try:
        # Bare .claude/ with no agents/, no commands/, no schemas/, no CLAUDE.md
        (tmp / ".claude").mkdir()
        score, evidence = kit_doctor._score_instructions(tmp, tmp / ".claude")
        assert score == 0, f"empty .claude/ should score 0, got {score}"
        # All evidence lines should be ⚠ or ·, none ✓
        assert not any(e.startswith("✓") for e in evidence)
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_score_state_with_active_ledger():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        from prusik import ledger as _ledger
        # Synthesize 12 ledger events to bump score
        for i in range(12):
            _ledger.append("test_event", n=i)
        score, evidence = kit_doctor._score_state(tmp)
        assert score >= 4, f"with 12 events + all dirs, should score ≥4, got {score}"
        assert any("Ledger has 12 events" in e for e in evidence)
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_score_verification_flags_missing_project_policy_when_pre_commit_exists():
    """When project has pre-commit but project_policy block isn't enabled,
    Verification scorer must flag this as a missing signal."""
    tmp = _mktmp_project()
    try:
        (tmp / ".pre-commit-config.yaml").write_text("repos: []\n")
        (tmp / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.pytest.ini_options]\n"
        )
        kit_init.run()
        detection = kit_detect.detect_project(tmp)
        score, evidence = kit_doctor._score_verification(tmp, detection)
        # Should flag the missing project_policy
        assert any("project_policy NOT enabled" in e and "pre-commit exists" in e
                   for e in evidence), \
            f"should warn about pre-commit-without-project_policy; got {evidence}"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_score_scope_zero_for_empty_briefs():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Fresh init has empty briefs/, design/, decisions/
        score, evidence = kit_doctor._score_scope(tmp)
        assert score == 0, f"empty scope dirs should score 0, got {score}"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_score_scope_high_with_real_briefs():
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Add 6 briefs + matching scope.md + plan.md + 6 decisions
        for i in range(6):
            (tmp / "briefs" / f"feat-{i}.md").write_text("# brief\n")
            (tmp / "design" / f"feat-{i}").mkdir(parents=True, exist_ok=True)
            (tmp / "design" / f"feat-{i}" / "scope.md").write_text("# scope\n")
            (tmp / "design" / f"feat-{i}" / "plan.md").write_text("# plan\n")
            (tmp / "decisions" / f"feat-{i}.json").write_text('{"feature":"f"}')
        score, evidence = kit_doctor._score_scope(tmp)
        assert score == 5, f"full scope discipline should score 5, got {score}"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_score_session_lifecycle_full_when_hooks_wired():
    tmp = _mktmp_project()
    try:
        kit_init.run()  # Installs hooks via shipped settings.json
        # Synthesize a phase_advance event in the ledger
        from prusik import ledger as _ledger
        _ledger.append("phase_advance", from_phase="scoping", to_phase="building",
                       feature="x")
        score, evidence = kit_doctor._score_session_lifecycle(tmp, tmp / ".claude")
        assert score >= 4, f"with all hooks + phase event + worktrees, should score ≥4, got {score}"
        assert any("All three prusik gate hooks wired" in e for e in evidence)
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_drift_detected_when_test_command_changes():
    """If detection at install said 'pytest' and current detection
    sees a different command, drift surfaces it."""
    tmp = _mktmp_project()
    try:
        # Install with pytest detected
        (tmp / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.pytest.ini_options]\n"
        )
        kit_init.run()
        # Now mutate the project — remove pytest config, add Cargo.toml
        (tmp / "pyproject.toml").unlink()
        (tmp / "Cargo.toml").write_text("[package]\nname='x'\nversion='0.1.0'\n")
        # Re-detect
        manifest = json.loads((tmp / ".claude" / ".prusik-manifest.json").read_text())
        current = kit_detect.detect_project(tmp)
        drift = kit_doctor._detect_drift(manifest, current)
        # Should flag stack change AND test command change
        assert "stacks" in drift, f"expected stacks drift; got {drift}"
        assert "test_command" in drift, f"expected test_command drift; got {drift}"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_drift_clean_when_no_changes():
    tmp = _mktmp_project()
    try:
        (tmp / "pyproject.toml").write_text(
            "[project]\nname='x'\n[tool.pytest.ini_options]\n"
        )
        kit_init.run()
        manifest = json.loads((tmp / ".claude" / ".prusik-manifest.json").read_text())
        current = kit_detect.detect_project(tmp)
        drift = kit_doctor._detect_drift(manifest, current)
        assert drift == {}, f"no changes should produce no drift; got {drift}"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_v085_lowest_subsystem_identification():
    """The lowest-scoring subsystem must be named correctly even with ties."""
    fake_scores = {
        "instructions": (5, ["all good"]),
        "state": (3, ["medium"]),
        "verification": (1, ["worst"]),
        "scope": (4, ["solid"]),
        "session_lifecycle": (1, ["also worst"]),
    }
    lowest = kit_doctor._lowest_subsystem(fake_scores)
    # Tie at 1: alphabetical breaks → 'session_lifecycle' or 'verification'
    # _lowest_subsystem sorts by (score, name); 's' > 'v' so verification wins
    assert lowest == "session_lifecycle", \
        f"alphabetical tie-break should pick session_lifecycle; got {lowest}"


def test_v085_doctor_handles_pre_v084_manifest():
    """Manifest from before v0.8.4 doesn't have a `detection` key.
    Drift detection must handle this gracefully."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        manifest_path = tmp / ".claude" / ".prusik-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        # Strip the detection key (simulating older manifest)
        del manifest["detection"]
        manifest_path.write_text(json.dumps(manifest))
        # Re-load and check drift
        loaded = json.loads(manifest_path.read_text())
        current = kit_detect.detect_project(tmp)
        drift = kit_doctor._detect_drift(loaded, current)
        assert drift.get("_meta") == "manifest_predates_detection"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)



# ---------- v0.16.0 — prusik doctor suggest-permissions + insights ----------

def _write_ledger(tmp, events):
    """Helper: write a list of dict events as ledger.jsonl."""
    sp = tmp / ".sprint"
    sp.mkdir(parents=True, exist_ok=True)
    (sp / "ledger.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )


def test_v0160_suggest_permissions_recurrence_trigger():
    """Only deny patterns with N≥2 produce suggestions; one-offs filtered."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _write_ledger(tmp, [
            {"ts": "2026-01-01T00:00:00+00:00", "event": "gate_blocked",
             "tool": "Bash", "command": "alembic upgrade head", "reason": ""},
            {"ts": "2026-01-01T00:00:01+00:00", "event": "gate_blocked",
             "tool": "Bash", "command": "alembic downgrade -1", "reason": ""},
            {"ts": "2026-01-01T00:00:02+00:00", "event": "gate_blocked",
             "tool": "Bash", "command": "exotic-one-off", "reason": ""},
        ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            from prusik import doctor as kit_doctor
            kit_doctor.run(suggest_permissions=True)
        out = buf.getvalue()
        assert "Bash(alembic *)" in out, f"recurring alembic should be suggested:\n{out}"
        assert "exotic-one-off" not in out, "single occurrence must not suggest"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0160_suggest_permissions_skips_v015_engine_baked_scratch():
    """v0.15.0+ already handles /tmp/* engine-baked — don't re-suggest."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _write_ledger(tmp, [
            {"ts": "2026-01-01T00:00:00+00:00", "event": "gate_blocked",
             "tool": "Bash", "command": "echo x > /tmp/foo",
             "reason": "bash redirect to unwriteable path: /tmp/foo (scratch)"},
            {"ts": "2026-01-01T00:00:01+00:00", "event": "gate_blocked",
             "tool": "Bash", "command": "echo y > /tmp/bar",
             "reason": "bash redirect to unwriteable path: /tmp/bar (scratch)"},
        ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            from prusik import doctor as kit_doctor
            kit_doctor.run(suggest_permissions=True)
        out = buf.getvalue()
        assert "/tmp" not in out, \
            f"v0.15.0+ engine-baked /tmp should not be re-suggested:\n{out}"
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0160_insights_detects_rewind_heavy_sprints():
    """>1 phase_rewind per sprint surfaces as brief_clarity insight."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _write_ledger(tmp, [
            {"ts": "2026-01-01T00:00:00+00:00", "event": "sprint_started",
             "feature": "vague-feat"},
            {"ts": "2026-01-01T01:00:00+00:00", "event": "phase_rewind",
             "feature": "vague-feat"},
            {"ts": "2026-01-01T02:00:00+00:00", "event": "phase_rewind",
             "feature": "vague-feat"},
            {"ts": "2026-01-01T03:00:00+00:00", "event": "sprint_complete",
             "feature": "vague-feat"},
        ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            from prusik import doctor as kit_doctor
            kit_doctor.run(insights=True)
        out = buf.getvalue()
        assert "brief_clarity" in out
        assert "vague-feat" in out
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0160_insights_calendar_drift_threshold():
    """Sprints with >90% idle wall-clock surface as calendar_drift."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        # Sprint that "starts," has one event 1 minute later, and ends 24h
        # later — wall=24h, active=~1min → >99% idle.
        _write_ledger(tmp, [
            {"ts": "2026-01-01T00:00:00+00:00", "event": "sprint_started",
             "feature": "drifty"},
            {"ts": "2026-01-01T00:01:00+00:00", "event": "phase_advance",
             "feature": "drifty"},
            {"ts": "2026-01-02T00:00:00+00:00", "event": "sprint_complete",
             "feature": "drifty"},
        ])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            from prusik import doctor as kit_doctor
            kit_doctor.run(insights=True)
        out = buf.getvalue()
        assert "calendar_drift" in out
        assert "drifty" in out
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


def test_v0160_doctor_subcommands_are_read_only():
    """Like doctor itself (v0.13.0 invariant), the new subcommands must
    not mutate manifest/ledger as a side effect."""
    tmp = _mktmp_project()
    try:
        kit_init.run()
        _write_ledger(tmp, [
            {"ts": "2026-01-01T00:00:00+00:00", "event": "gate_blocked",
             "tool": "Bash", "command": "ruff check", "reason": ""},
            {"ts": "2026-01-01T00:00:01+00:00", "event": "gate_blocked",
             "tool": "Bash", "command": "ruff format", "reason": ""},
        ])
        before_ledger = (tmp / ".sprint" / "ledger.jsonl").read_text()
        before_manifest = (tmp / ".claude" / ".prusik-manifest.json").read_text()
        with contextlib.redirect_stdout(io.StringIO()):
            from prusik import doctor as kit_doctor
            kit_doctor.run(suggest_permissions=True)
            kit_doctor.run(insights=True)
        assert (tmp / ".sprint" / "ledger.jsonl").read_text() == before_ledger
        assert (tmp / ".claude" / ".prusik-manifest.json").read_text() == before_manifest
    finally:
        os.chdir("/"); shutil.rmtree(tmp)


