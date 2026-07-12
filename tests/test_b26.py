"""Smoke tests — exercise the engine without Claude Code in the loop.

Domain: b26.

Run: uv run python -m pytest tests/test_b26.py -v
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


# ---------- v0.8.1 — B26 fabrication-pattern lint guard ----------
#
# Background: m2-s4-settings-preview filed B26 — three subagent dispatches
# (2x regression-sentinel, 1x integrator) reported "Bash denied" without
# ever attempting Bash. Prusik has no subagent-aware deny path; the role
# specs were teaching the fabrication via "If Bash is denied (subagent
# contexts may have stricter permissions): write FAIL with reason..."
#
# Two lint checks below prevent the pattern from recurring as new role
# specs are added or existing ones edited:
#   1. No role spec may contain the false-anchoring phrases that prime
#      the fabrication (subagents do NOT have stricter permissions; the
#      prusik doesn't even check who the caller is).
#   2. Any role-spec mention of bash/test/linter denial that prescribes
#      FAIL behavior must include verify-before-claim language within a
#      reasonable window — references to `[prusik-gate]`, observation, or
#      quoting actual output.

def _agent_template_paths():
    agents_dir = (Path(__file__).parent.parent / "prusik" / "templates"
                  / ".claude" / "agents")
    return sorted(agents_dir.glob("*.md"))


def test_b26_no_role_spec_anchors_false_belief_about_subagent_permissions():
    """v0.8.1 (B26): no role spec may state or imply that subagents have
    stricter Bash permissions than the main session. Prusik's PreToolUse
    hook (kit/gate.py:pre_tool) reads tool_name + tool_input from stdin —
    the payload contains no caller-identity field, so prusik cannot
    discriminate against subagents. Stating that they have stricter
    permissions primes LLM agents to claim deny without actually
    attempting Bash."""
    forbidden_phrases = [
        # The exact phrase from regression-sentinel.md pre-v0.8.1
        "subagent contexts may have stricter permissions",
        "subagents may have stricter permissions",
        "stricter permissions than the main",
        "denied for this subagent",
        "denied for subagents",
        "subagent permission scope",
    ]
    offenders: list[tuple[str, str]] = []
    for path in _agent_template_paths():
        text = path.read_text()
        lower = text.lower()
        for phrase in forbidden_phrases:
            if phrase.lower() in lower:
                offenders.append((path.name, phrase))
    assert not offenders, (
        f"role specs must not anchor the false belief that subagents "
        f"have different Bash permissions (prusik has no "
        f"subagent-aware deny path — see kit/gate.py:pre_tool). "
        f"Found: {offenders}"
    )


def test_b26_role_spec_deny_clauses_require_verify_before_claim():
    """v0.8.1 (B26): any role-spec section that gives the agent an escape
    hatch on Bash/test-runner/linter denial must require observed
    evidence before the agent claims deny. Specifically: a line that
    triggers on 'denied' / 'cannot run' / 'unable to run' AND prescribes
    FAIL behavior must have, within ±10 lines, at least one of:
      - the literal `[prusik-gate]` (prusik's deny-message prefix)
      - the word 'observe' / 'observed'
      - the word 'quote' (instructing the agent to quote actual output)
      - the word 'attempt' (instructing the agent to actually try first)
    This is mechanical and self-enforcing. New role specs that violate
    the pattern fail this test at template-edit time, not at
    sprint-failure time."""
    # Lines that trigger the rule — these are the entry conditions where
    # an agent might be tempted to bail without verifying.
    trigger_re = re.compile(
        r"(if\s+bash\s+(?:is\s+)?(?:returns?\s+a\s+)?(?:kit-gate\s+)?den|"
        r"if\s+(?:you\s+)?cannot\s+run|"
        r"if\s+(?:bash\s+)?(?:is\s+)?denied|"
        r"bash\s+(?:is|was)\s+denied)",
        re.IGNORECASE,
    )
    # Verification-language patterns that satisfy the rule.
    verify_re = re.compile(
        r"\[prusik-gate\]|observ(?:e|ed|ing)|\bquote\b|"
        r"actually\s+attempt|must\s+have\s+attempted|"
        r"must\s+(?:actually\s+)?attempt",
        re.IGNORECASE,
    )
    window = 10  # ±10 lines around the trigger line
    offenders: list[str] = []
    for path in _agent_template_paths():
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            if not trigger_re.search(line):
                continue
            start = max(0, i - window)
            end = min(len(lines), i + window + 1)
            window_text = "\n".join(lines[start:end])
            if not verify_re.search(window_text):
                offenders.append(f"{path.name}:{i+1}: {line.strip()[:120]}")
    assert not offenders, (
        "role specs with 'if denied' / 'cannot run' escape hatches must "
        "require verify-before-claim — observed [prusik-gate] output, "
        "explicit 'attempt' instruction, or 'quote' the actual deny "
        "message. Without this, LLM agents pattern-match onto the "
        "prescribed FAIL text without verifying. Offenders:\n  "
        + "\n  ".join(offenders)
    )



# ---------- v0.8.2 — B26 reviewer-fabrication detector ----------
#
# Branch B from the [13:11] DIAGNOSTIC was confirmed by live-cc's [13:20]
# UPDATE: ledger has zero gate_blocked Bash events during the failed
# regression-sentinel dispatches; agents fabricated the [prusik-gate] denial
# claims without attempting Bash. v0.8.2 adds an orchestrator-side
# detector at fix_round.start + gate.advance + a standalone CLI command.
# These tests cover the four detection branches.

def _setup_fabrication_test(tmp, *, feature="feat", reviewer_text=None,
                            ledger_events=None):
    """Helper: scaffold a reviewer artifact + ledger entries for a feature."""
    if reviewer_text is not None:
        artifact = tmp / "reports" / feature / "regression.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(reviewer_text)
    if ledger_events:
        from prusik import ledger as _ledger
        for ev in ledger_events:
            _ledger.append(**ev)


def test_b26_detector_pass_artifact_not_flagged():
    """Detector must not flag a PASS reviewer artifact."""
    tmp = _mktmp_project()
    try:
        _setup_fabrication_test(
            tmp,
            reviewer_text="PASS\nAll 100 tests passed.\n",
        )
        suspects = consistency.detect_reviewer_fabrication(tmp, "feat")
        assert suspects == [], "PASS artifact must not be flagged"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b26_detector_fail_without_deny_language_not_flagged():
    """Detector must not flag a FAIL artifact that doesn't claim Bash deny."""
    tmp = _mktmp_project()
    try:
        _setup_fabrication_test(
            tmp,
            reviewer_text=(
                "FAIL\n"
                "test_payment_flow failed: AssertionError on line 42.\n"
                "test_invoice_generate failed: KeyError 'amount'.\n"
            ),
        )
        suspects = consistency.detect_reviewer_fabrication(tmp, "feat")
        assert suspects == [], "FAIL with real test failures must not be flagged"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b26_detector_pure_fabrication_flagged():
    """Detector flags FAIL with deny language + no [prusik-gate] quote +
    no ledger event = pure fabrication (B26 canonical shape)."""
    tmp = _mktmp_project()
    try:
        _setup_fabrication_test(
            tmp,
            reviewer_text=(
                "FAIL\n"
                "Bash tool access was denied for this agent invocation. "
                "Need Bash(uv run *) added to settings.json permissions.allow.\n"
            ),
            ledger_events=[],  # no gate_blocked events
        )
        suspects = consistency.detect_reviewer_fabrication(tmp, "feat")
        assert len(suspects) == 1, f"expected 1 suspect, got {suspects}"
        s = suspects[0]
        assert s["shape"] == "pure_fabrication"
        assert s["role"] == "regression-sentinel"
        assert "B26" in s["reason"]
        assert "no `[prusik-gate]` message quoted" in s["reason"]
        assert "no gate_blocked Bash event" in s["reason"]
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b26_detector_legitimate_deny_not_flagged():
    """FAIL with [prusik-gate] quote + matching ledger gate_blocked event =
    legitimate deny, must NOT be flagged."""
    tmp = _mktmp_project()
    try:
        _setup_fabrication_test(
            tmp,
            reviewer_text=(
                "FAIL\n"
                "Test command failed at runtime:\n"
                "[prusik-gate] phase 'reviewing' blocks command: 'pytest'\n"
                "Unable to run tests under current permission set; "
                "need Bash(pytest *) in permissions.allow.\n"
            ),
            ledger_events=[
                {
                    "event_type": "gate_blocked",
                    "tool": "Bash",
                    "feature": "feat",
                    "reason": "deny command: pytest",
                }
            ],
        )
        suspects = consistency.detect_reviewer_fabrication(tmp, "feat")
        assert suspects == [], (
            f"legitimate deny (quote + ledger event) must not be flagged; "
            f"got {suspects}"
        )
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b26_detector_fabricated_quote_flagged():
    """Detector flags FAIL with [prusik-gate] quote BUT no ledger event =
    the agent fabricated even the quoted message."""
    tmp = _mktmp_project()
    try:
        _setup_fabrication_test(
            tmp,
            reviewer_text=(
                "FAIL\n"
                "[prusik-gate] phase 'reviewing' blocks command: 'pytest'\n"
                "Bash tool access was denied; need Bash(pytest *) in "
                "permissions.allow.\n"
            ),
            ledger_events=[],  # no real gate_blocked event despite the quote
        )
        suspects = consistency.detect_reviewer_fabrication(tmp, "feat")
        assert len(suspects) == 1, f"expected 1 suspect, got {suspects}"
        s = suspects[0]
        assert s["shape"] == "fabricated_quote"
        assert "no gate_blocked event" in s["reason"]
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b26_detector_incomplete_report_flagged():
    """Detector flags FAIL with deny language but no [prusik-gate] quote +
    ledger HAS a real deny = agent should have quoted it."""
    tmp = _mktmp_project()
    try:
        _setup_fabrication_test(
            tmp,
            reviewer_text=(
                "FAIL\n"
                "Bash tool access was denied for this agent invocation. "
                "Need Bash(uv run *) added to settings.json permissions.allow.\n"
            ),
            ledger_events=[
                {
                    "event_type": "gate_blocked",
                    "tool": "Bash",
                    "feature": "feat",
                    "reason": "deny command: uv",
                }
            ],
        )
        suspects = consistency.detect_reviewer_fabrication(tmp, "feat")
        assert len(suspects) == 1, f"expected 1 suspect, got {suspects}"
        s = suspects[0]
        assert s["shape"] == "incomplete_report"
        assert "should quote" in s["reason"] or "verbatim" in s["reason"]
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b26_detector_emits_ledger_event_on_warning():
    """emit_fabrication_warnings must log a reviewer_fabrication_suspected
    event per suspect to the ledger, so prusik digest can surface counts."""
    tmp = _mktmp_project()
    try:
        from prusik import ledger as _ledger
        suspects = [{
            "role": "regression-sentinel",
            "artifact": "reports/feat/regression.txt",
            "feature": "feat",
            "shape": "pure_fabrication",
            "reason": "test reason",
        }]
        consistency.emit_fabrication_warnings(suspects)
        events = [r for r in _ledger.read_all()
                  if r.get("event") == "reviewer_fabrication_suspected"]
        assert len(events) == 1
        assert events[0]["role"] == "regression-sentinel"
        assert events[0]["shape"] == "pure_fabrication"
        assert events[0]["feature"] == "feat"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b26_detector_handles_both_reviewer_artifacts():
    """Detector covers both regression-sentinel and conventions-enforcer
    artifacts. A fabrication in conventions.txt is flagged just like one
    in regression.txt."""
    tmp = _mktmp_project()
    try:
        # Both artifacts fabricated
        for fname in ("regression.txt", "conventions.txt"):
            artifact = tmp / "reports" / "feat" / fname
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(
                "FAIL\nBash tool access was denied. "
                "Need Bash(ruff *) in permissions.allow.\n"
            )
        suspects = consistency.detect_reviewer_fabrication(tmp, "feat")
        roles = {s["role"] for s in suspects}
        assert roles == {"regression-sentinel", "conventions-enforcer"}, \
            f"both reviewer artifacts must be checked; got roles {roles}"
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b26_verify_reviewer_cli_command():
    """The standalone `prusik gate verify-reviewer --feature X` command runs
    the detector and exits 0 (informational, not blocking)."""
    tmp = _mktmp_project()
    try:
        _setup_fabrication_test(
            tmp,
            reviewer_text=(
                "FAIL\n"
                "Bash tool access was denied for this agent invocation. "
                "Need Bash(uv run *) added to settings.json permissions.allow.\n"
            ),
        )
        args = argparse.Namespace(feature="feat")
        rc = gate.verify_reviewer(args)
        assert rc == 0, "verify-reviewer must always exit 0 (informational)"
        # Confirm the ledger event was emitted
        from prusik import ledger as _ledger
        events = [r for r in _ledger.read_all()
                  if r.get("event") == "reviewer_fabrication_suspected"]
        assert len(events) == 1
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)


def test_b26_verify_reviewer_no_artifacts_passes_clean():
    """verify-reviewer on a feature with no reviewer artifacts (e.g.,
    sprint hasn't reached reviewing yet) prints a clean message and exits 0."""
    tmp = _mktmp_project()
    try:
        args = argparse.Namespace(feature="feat")
        rc = gate.verify_reviewer(args)
        assert rc == 0
        # Should produce no fabrication ledger events
        from prusik import ledger as _ledger
        events = [r for r in _ledger.read_all()
                  if r.get("event") == "reviewer_fabrication_suspected"]
        assert events == []
    finally:
        os.chdir("/")
        shutil.rmtree(tmp)



# ---------- v0.8.9 — gate_blocked deny includes worktree-redirect hint ----------
#
# Driven by live-cc's [13:42] OBSERVATION on m4-s2d-test-debt-cleanup
# bridge: 6.5h stall on May 9 01:34 because the deny message named the
# constraint but not the route around it. The fix routes the agent to
# the correct path at the moment of failure instead of forcing a
# diagnostic round-trip. One-line per dispatch; high ROI.


def test_v089_redirect_hint_solo_phase_concrete_role():
    """When solo_execute writable pattern is `worktrees/solo/**` and a
    Write to a non-worktree path is denied, the deny message includes a
    redirect hint pointing at the worktree mirror."""
    config = {
        "phases": [
            {"name": "solo_execute", "writable": ["worktrees/solo/**"]}
        ],
    }
    hint = gate._worktree_redirect_hint(
        "tests/integration/conftest.py",
        config,
        "solo_execute",
        "feat",
    )
    assert hint is not None, "redirect hint must fire when worktrees pattern exists"
    assert "worktrees/solo/tests/integration/conftest.py" in hint, \
        f"hint must compute the worktree-mirror path; got {hint!r}"
    assert "solo_execute" in hint, "hint should name the phase"


def test_v089_redirect_hint_wildcard_role_uses_placeholder():
    """When building phase has `worktrees/*/**` (wildcard role), the hint
    suggests `worktrees/<your-role>/...` since prusik can't know which
    builder role the agent is."""
    config = {
        "phases": [
            {"name": "building", "writable": ["worktrees/*/**"]}
        ],
    }
    hint = gate._worktree_redirect_hint(
        "src/foo.py",
        config,
        "building",
        "feat",
    )
    assert hint is not None
    assert "worktrees/<your-role>/src/foo.py" in hint
    assert "<your-role>" in hint


def test_v089_redirect_hint_skipped_when_no_worktree_pattern():
    """Phases without any worktrees/* pattern (e.g. integrating with
    `**` writable, or scoping with design-only paths) should not get a
    redirect hint — there's no useful redirect to suggest."""
    config = {
        "phases": [
            {"name": "scoping", "writable": ["design/{feature}/scope.md"]}
        ],
    }
    hint = gate._worktree_redirect_hint(
        "src/foo.py",
        config,
        "scoping",
        "feat",
    )
    assert hint is None, "phases without worktrees pattern must not generate a hint"


def test_v089_redirect_hint_skipped_when_target_already_in_worktrees():
    """If the agent already targeted a worktrees/* path (and it didn't
    match for some other reason), don't suggest a worktrees/worktrees/
    path. Return None so the deny message stands alone."""
    config = {
        "phases": [
            {"name": "solo_execute", "writable": ["worktrees/solo/**"]}
        ],
    }
    hint = gate._worktree_redirect_hint(
        "worktrees/test-writer/foo.py",  # already-worktrees path
        config,
        "solo_execute",
        "feat",
    )
    assert hint is None


def test_v089_redirect_hint_prefers_concrete_role_over_wildcard():
    """When phase has BOTH `worktrees/solo/**` AND `worktrees/*/**` (e.g.
    fix-round expansion stacked on solo), prefer the concrete role-name
    suggestion over the wildcard placeholder. Operator-friendlier."""
    config = {
        "phases": [
            {"name": "reviewing",
             "writable": ["worktrees/solo/**", "worktrees/*/**"]}
        ],
    }
    hint = gate._worktree_redirect_hint(
        "tests/integration/test_x.py",
        config,
        "reviewing",
        "feat",
    )
    assert hint is not None
    assert "worktrees/solo/" in hint, "concrete role 'solo' should win over wildcard"
    assert "<your-role>" not in hint


def test_v089_iface_deny_message_includes_redirect_arrow_marker():
    """Operator-facing string contract: when a redirect hint fires, the
    deny message contains the `→` arrow followed by the hint. This is the
    marker that operators / CI parsers can scan for."""
    p = (Path(__file__).parent.parent / "prusik" / "gate.py").read_text()
    assert "msg += f\"\\n  → {hint}\"" in p, \
        "deny construction must use the `→` arrow marker for the redirect hint"


