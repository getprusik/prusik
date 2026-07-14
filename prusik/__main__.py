import argparse
import re
import sys

from prusik import __version__


# v0.6.4 (B9): single-source-of-truth slug validator. Applied to every
# --feature argument via argparse type= so invalid slugs are rejected
# at the CLI boundary with a clear error, before any downstream code
# tries to use the slug as a path component.
#
# Surfaced when /sprint-run domain-schema — strict on-rails: ... rendered
# the em dash as the feature slug everywhere. Defense-in-depth: prusik engine
# rejects garbage slugs even if the slash-command pre-flight is bypassed.
_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _slug(value: str) -> str:
    if not _SLUG_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"Invalid feature slug {value!r}: must be lowercase alphanumeric + "
            f"hyphens, starting with a letter (e.g. 'domain-schema'). "
            f"Operator notes / guardrails belong in briefs/<feature>.md, not "
            f"in --feature args."
        )
    return value


def main():
    parser = argparse.ArgumentParser(prog="prusik", description="Prusik")
    parser.add_argument("--version", action="version",
                        version=f"prusik {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Initialize prusik in current project")
    p_init.add_argument("--conventions", default=None,
                        help="Path to a conventions pack to ingest")
    p_init.add_argument("--force", action="store_true",
                        help="Nuke .claude/ and copy fresh (preserves "
                             "conventions/ and projects/ subdirs). Default "
                             "is additive merge: skip-on-conflict per file, "
                             "surgical merge for settings.json.")
    p_init.add_argument("--no-merge-additions", action="store_true",
                        help="Skip the surgical settings.json merge; user's "
                             "settings.json is left exactly as-is. Files "
                             "still skip-on-conflict. Mirrors `prusik refresh` "
                             "--no-merge-additions for consistency.")
    p_init.add_argument("--stack", default=None, metavar="NAME",
                        help="v0.17.0 — overlay a per-stack sprint-config "
                             "preset after the generic template lands. "
                             "Available: fastapi-postgres (more queued). "
                             "Use --list-stacks to see all.")
    p_init.add_argument("--list-stacks", action="store_true",
                        help="List available --stack preset names and exit.")
    p_init.add_argument("--allow-dirty", action="store_true",
                        help="Override the pre-flight clean-tree check and "
                             "install over uncommitted changes (you own the "
                             "clean-uninstall verification).")
    p_init.add_argument("--merge-hooks", action="store_true",
                        help="When the project already has a .claude/settings.json "
                             "`hooks` block, APPEND prusik's gate hooks alongside "
                             "it (non-destructive) so the FSM enforces. Without "
                             "this, existing hooks are kept and prusik's are NOT "
                             "wired (the harness stays inert). Uninstall reverts.")
    p_init.add_argument("--minimal-perms", action="store_true",
                        help="Add ONLY the harness-required permission "
                             "(Bash(prusik *)) to settings.json, not the full "
                             "convenience allowlist — so an existing repo's "
                             "auto-approve posture isn't broadened. Composes with "
                             "--merge-hooks.")

    p_gate = sub.add_parser("gate", help="Hook-invoked and CLI gate commands")
    gsub = p_gate.add_subparsers(dest="gate_cmd", required=True)
    gsub.add_parser("pre-tool", help="PreToolUse hook")
    gsub.add_parser("post-tool", help="PostToolUse hook — convergence-stall detector")
    gsub.add_parser("stop", help="Stop hook")
    gsub.add_parser("session-start", help="SessionStart hook")
    p_adv = gsub.add_parser("advance", help="Advance to next phase")
    p_adv.add_argument("phase")
    p_adv.add_argument("--feature", required=True, type=_slug)
    p_adv.add_argument("--allow-rewind", action="store_true",
                       help="Allow advancing to an earlier phase (recorded as phase_rewind)")
    p_brief = gsub.add_parser("brief", help="Validate a brief file")
    p_brief.add_argument("path")
    p_fallback = gsub.add_parser("mark-fallback",
                                  help="Log that a reviewer-artifact fallback was used "
                                       "(slash-command writes the artifact from agent text)")
    p_fallback.add_argument("--role", required=True,
                             help="reviewer role name (brief-critic, scope-critic, etc.)")
    p_fallback.add_argument("--feature", required=True, type=_slug)
    # v0.10.0 Fix 3 — content-addressed re-gating
    p_recv = gsub.add_parser("record-verdict",
                              help="Bind a critic verdict to the substantive "
                                   "hash of what it judged (carry-forward across rewinds)")
    p_recv.add_argument("--role", required=True)
    p_recv.add_argument("--feature", required=True, type=_slug)
    p_recv.add_argument("--artifact", required=True,
                         help="source artifact the verdict judges, e.g. design/{f}/scope.md")
    p_recv.add_argument("--verdict", required=True,
                         choices=["APPROVED", "REJECTED", "PASS", "FAIL"])
    p_vcur = gsub.add_parser("verdict-current",
                              help="Exit 0 iff a prior APPROVED verdict's hash "
                                   "matches the artifact now (skip critic re-dispatch)")
    p_vcur.add_argument("--role", required=True)
    p_vcur.add_argument("--feature", required=True, type=_slug)
    p_vcur.add_argument("--artifact", required=True)
    p_cap = gsub.add_parser("capture",
                             help="Run a reviewer suite, record prusik-captured "
                                  "execution evidence, exit with its code "
                                  "(v0.12.0 F — gate honors PASS only against "
                                  "this, never the agent's word)")
    p_cap.add_argument("--feature", required=True, type=_slug)
    p_cap.add_argument("--phase", required=True,
                        help="reviewer phase, e.g. regression | conventions")
    p_cap.add_argument("--kind", required=True,
                        choices=["tests", "lint", "types"],
                        help="primitive parser: tests=executed count "
                             "(strong); lint/types=tool-completed signal")
    p_cap.add_argument("--baseline-domain", default=None,
                        help="v0.18.0 — declare the domain this baseline covers "
                             "(e.g. 'integration+behavior'). Required by the gate "
                             "alongside --baseline-source when --baseline-known-"
                             "failures is 0 (closes the §3.5 false-clean class)")
    p_cap.add_argument("--baseline-source", default=None,
                        help="v0.18.0 — where the baseline was captured from "
                             "(e.g. 'post-integration-gate', 'production-trace')")
    p_cap.add_argument("--baseline-known-failures", default=None, type=int,
                        help="v0.18.0 — count of known_failures in this baseline "
                             "(int >= 0). Zero requires domain+source.")
    p_cap.add_argument("--reset", action="store_true",
                        help="Discard prior evidence entries for this phase before "
                             "appending. Use `--reset` ALONE (no `-- <cmd>`) to just "
                             "CLEAR the evidence — never fake a clear with a no-op "
                             "like `-- echo reset` (it appends a tests=0 entry that "
                             "trips the false-clean guard at every advance).")
    p_cap.add_argument("command", nargs=argparse.REMAINDER,
                        help="-- <verbatim command to run>")
    p_scope = gsub.add_parser("scope", help="Validate a scope file")
    p_scope.add_argument("path")
    p_plan = gsub.add_parser("plan", help="Validate a plan file "
                                          "(structure + plan ⊆ scope cross-ref)")
    p_plan.add_argument("path")
    p_fr = gsub.add_parser("fix-round",
                            help="Open/close a reviewer fix-round window "
                                 "(v0.5.7; expands writable to worktrees/*/** "
                                 "during reviewing phase)")
    frsub = p_fr.add_subparsers(dest="fix_round_cmd", required=True)
    p_fr_start = frsub.add_parser("start", help="Begin a fix round (max 2/sprint)")
    p_fr_start.add_argument("--feature", required=True, type=_slug)
    p_fr_end = frsub.add_parser("end", help="End the active fix round")
    p_fr_end.add_argument("--feature", required=True, type=_slug)
    p_fr_esc = frsub.add_parser("escalate",
                                 help="At the fix-round cap, record an IN-PRUSIK "
                                      "operator decision instead of an out-of-"
                                      "prusik STOP (v0.11.0 #3)")
    p_fr_esc.add_argument("--feature", required=True, type=_slug)
    p_fr_esc.add_argument("--decision",
                           choices=["extend-once", "integrate-with-flag", "abandon"],
                           help="The decision to record (required unless --auto)")
    p_fr_esc.add_argument("--rationale",
                           help="Why (required unless --auto; recorded in the ledger)")
    p_fr_esc.add_argument("--auto", action="store_true",
                           help="v0.70.0 — read the recorded residual classification "
                                "(fix-round classify) and RECOMMEND a decision "
                                "(extend-once only when residual is test-fixable / "
                                "zero source defects). Advisory — never auto-applies.")
    # v0.70.0 — fix-round classify: the sentinel records its a/b/c residual split
    # (test-fixable / source-defect / pre-existing) so `escalate --auto` can read it.
    p_fr_cls = frsub.add_parser("classify",
                                help="Record the sentinel's residual classification "
                                     "(test-fixable / source-defect / pre-existing) "
                                     "so `escalate --auto` can recommend (field finding #3)")
    p_fr_cls.add_argument("--feature", required=True, type=_slug)
    p_fr_cls.add_argument("--test-fixable", type=int, default=0,
                          help="Residual failures fixable by updating tests only")
    p_fr_cls.add_argument("--source-defect", type=int, default=0,
                          help="Residual failures from a real source defect")
    p_fr_cls.add_argument("--pre-existing", type=int, default=0,
                          help="Residual failures that pre-date this sprint (inherited)")
    p_fr_cls.add_argument("--note", default="", help="Optional context")
    frsub.add_parser("status",
                      help="Print active fix-round metadata or '(no active fix-round)'. "
                           "Used by reviewer agents to decide narrow-vs-full mode (v0.8.7).")
    # v0.73.0 — known-failure baselines (field finding #4): tolerate a git-stash-PROVEN
    # pre-existing flake without ever laundering a new failure.
    p_bl = gsub.add_parser("baseline",
                           help="Known-failure baselines — prove a pre-existing "
                                "flake (git-stash), list/prune, or emit deselect args")
    p_bl.add_argument("action",
                      choices=["prove", "prove-flaky", "list", "prune", "deselect-args"])
    p_bl.add_argument("--feature", type=_slug)
    p_bl.add_argument("--test", help="Test id to prove (e.g. tests/x.py::test_flaky)")
    p_bl.add_argument("--command",
                      help="prove: the command that runs ONLY that test. prove-flaky: "
                           "the command that EXHIBITS the flake (e.g. the full suite).")
    p_bl.add_argument("--days", type=int, default=30,
                      help="Days until the baseline ages out (default 30)")
    p_bl.add_argument("--runs", type=int, default=5,
                      help="prove-flaky: executions to demonstrate non-determinism "
                           "(default 5; a flake must both pass AND fail across them)")
    p_tr = gsub.add_parser("check-test-reach",
                              help="v0.20.0 — scan tests outside the touched "
                                   "set for references to contracts the sprint "
                                   "touched. Flag-only, NOT gating; closes "
                                   "the cross-touch-set coverage gap (m4-gate-"
                                   "domain-debt + m4-suspect-skip-audit "
                                   "recurrences). The post-integration "
                                   "full-suite gate remains the load-bearing "
                                   "backstop by design.")
    p_tr.add_argument("--feature", required=True, type=_slug)
    p_tr.add_argument("--touched-set", nargs="*", default=None,
                        help="explicit list of touched files; if omitted, "
                             "scans worktrees/* subtree")
    p_chk = gsub.add_parser("check-bindings",
                              help="v0.19.0 — scan touched files for binding "
                                   "mismatches (template-fetch-URL ↔ route-path; "
                                   "form-name ↔ handler-key). Flag-only, NOT gating; "
                                   "closes the DEV-1 assertion-depth gap class")
    p_chk.add_argument("--feature", required=True, type=_slug)
    p_chk.add_argument("--touched-set", nargs="*", default=None,
                        help="explicit list of touched files; if omitted, "
                             "scans worktrees/* subtree (prusik's standard "
                             "authoring location)")
    p_verify_rev = gsub.add_parser("verify-reviewer",
                                    help="Cross-check reviewer artifacts against the "
                                         "ledger for fabricated Bash-denial claims "
                                         "(v0.8.2, B26). Informational; does not block.")
    p_verify_rev.add_argument("--feature", required=True, type=_slug)
    p_start = gsub.add_parser("sprint-start", help="Start a sprint for a feature")
    p_start.add_argument("feature", type=_slug)
    p_start.add_argument("--trivial", action="store_true",
                          help="Proportional-ceremony lane for genuinely small "
                               "changes (bug_fix/doc/config/test/chore briefs only). "
                               "Skips scope-critic/plan-critic/triage; keeps the "
                               "brief-critic front gate AND the full reviewing "
                               "correctness floor. Rejected for new_feature/"
                               "refactor/migration briefs (ungameable).")
    p_start.add_argument("--force-clean", action="store_true",
                          help="Discard a DIFFERENT paused/active sprint (and its "
                               "worktrees) that would otherwise block this start. "
                               "Explicit opt-in to data loss; without it sprint-start "
                               "refuses rather than destroy another sprint's work.")
    p_pfit = gsub.add_parser("product-fit",
                             help="Check (or --bootstrap) the holistic product-fit "
                                  "acknowledgement: does this feature's brief resolve "
                                  "against design/product.md (pillars + glossary) + the "
                                  "existing feature set? Dormant until a charter exists.")
    p_pfit.add_argument("feature", type=_slug)
    p_pfit.add_argument("--bootstrap", action="store_true",
                         help="Draft design/product.md (the product charter) from the "
                              "template, seeded with the existing feature list, then "
                              "ratify it — this arms the imperative fit gate.")
    p_pfit.add_argument("--json", action="store_true",
                         help="Emit machine-readable JSON instead of text")
    p_pred = gsub.add_parser("prove-red",
                             help="Acceptance-TDD: capture the RED baseline for "
                                  "prove_red criteria — prove each verify FAILS "
                                  "WITHOUT the change (it's load-bearing, not "
                                  "vacuous-green). Run BEFORE implementing; "
                                  "sprint-complete then requires red-baseline + green.")
    p_pred.add_argument("--feature", required=True, type=_slug)
    p_pred.add_argument("--id", default=None,
                         help="Capture just this criterion id (default: all prove_red)")
    p_sprintinit = gsub.add_parser("sprint-init",
                                    help="Orchestrator: run discovery + fingerprint + "
                                         "sprint-start, guide agent steps")
    p_sprintinit.add_argument("--feature", required=True, type=_slug)
    p_sprintinit.add_argument("--skip-lint", action="store_true",
                               help="Skip brief-lint step (use if near-miss flags are false-positives)")
    p_done = gsub.add_parser("sprint-complete", help="Close a sprint; record actuals")
    p_done.add_argument("--feature", required=True, type=_slug)
    p_done.add_argument("--duration-min", type=int, default=None)
    p_done.add_argument("--tokens", type=int, default=None)
    p_done.add_argument("--escalated", action="store_true",
                        help="Set if the sprint escalated solo→team mid-flight")

    p_disc = sub.add_parser("discovery", help="Deterministic discovery tools")
    dsub = p_disc.add_subparsers(dest="disc_cmd", required=True)
    dsub.add_parser("inventory")
    dsub.add_parser("dep-graph")
    dsub.add_parser("fingerprint-map",
                    help="Snapshot dep-graph as baseline for map.md staleness checks")
    dsub.add_parser("all")

    p_sprint = sub.add_parser("sprint",
                                help="v0.17.0 — CLI pre-flight orchestrator: "
                                     "lint brief + forward risk + schema check + "
                                     "lane decision + sprint-start, in one command")
    p_sprint.add_argument("feature", type=_slug,
                            help="feature slug (briefs/<feature>.md must exist)")
    p_sprint.add_argument("--lane", choices=["trivial", "full"], default=None,
                            help="override the auto-detected lane recommendation")
    p_sprint.add_argument("--yes", action="store_true",
                            help="skip confirmation prompts (CI-friendly)")

    p_triage = sub.add_parser("triage", help="Route feature to solo or team (pure-code)")
    p_triage.add_argument("--feature", required=True, type=_slug)

    p_blint = sub.add_parser("brief-lint",
                              help="Lint briefs for structural issues + near-miss references")
    p_blint.add_argument("path", nargs="?", default=None,
                          help="Specific brief (default: all briefs under briefs/)")
    p_blint.add_argument("--cutoff", type=float, default=0.80,
                          help="difflib similarity threshold for near-miss warnings (default 0.80)")

    p_agents = sub.add_parser("agents",
                               help="Diagnose agent frontmatter / registry issues")
    asub = p_agents.add_subparsers(dest="agents_cmd", required=True)
    asub.add_parser("doctor",
                    help="Lint .claude/agents/*.md for registration-blocking issues")

    p_perms = sub.add_parser("permissions",
                              help="Diagnose CC permissions.allow vs prusik baseline")
    psub = p_perms.add_subparsers(dest="perms_cmd", required=True)
    psub.add_parser("audit",
                    help="Compare project allow list against prusik's recommended baseline")
    # v0.75.0 — controlled, audited add (field finding #6b). The phase-gate-bypassing path
    # to grant a permission without the rejected always-writable .claude carve-out.
    p_perms_add = psub.add_parser("add",
                                  help="Add a permission rule to settings.local.json "
                                       "(validated, danger-refused, audited)")
    p_perms_add.add_argument("rule", help="e.g. 'Bash(pnpm *)' or 'WebFetch'")
    p_perms_add.add_argument("--reason", default="",
                             help="Why (recorded in the ledger for the audit trail)")

    p_issues = sub.add_parser("issues", help="Issue tracker sync")
    isub = p_issues.add_subparsers(dest="issues_cmd", required=True)
    isub.add_parser("sync")
    p_isearch = isub.add_parser("search")
    p_isearch.add_argument("query")
    p_isearch.add_argument("--limit", type=int, default=5)

    p_watch = sub.add_parser("watchdog", help="Poll heartbeats + phase staleness")
    p_watch.add_argument("--poll", type=float, default=None,
                         metavar="MIN",
                         help="Run forever, polling every MIN minutes (else: single check)")

    p_serve = sub.add_parser("serve", help="Local web form for authoring briefs")
    p_serve.add_argument("--port", type=int, default=8765)

    # Bridge — OPT-IN live-collaboration channel (default OFF; `bridge on` to use).
    p_bridge = sub.add_parser("bridge",
                              help="Opt-in shared-document bridge between a live CC "
                                   "session and a separate author/operator (default OFF)")
    bsub = p_bridge.add_subparsers(dest="bridge_cmd", required=True)
    p_bon = bsub.add_parser("on", help="Turn the bridge ON (provision dir + wire UserPromptSubmit hook)")
    p_bon.add_argument("--slug", default=None, help="Optional slug for bridge dir")
    bsub.add_parser("off", help="Turn the bridge OFF (remove hook + env var; keep the bridge file)")
    # enable/disable retained as back-compat aliases for on/off.
    p_benable = bsub.add_parser("enable", help="Alias for `on`")
    p_benable.add_argument("--slug", default=None, help="Optional slug for bridge dir")
    bsub.add_parser("disable", help="Alias for `off`")
    bsub.add_parser("status", help="Show bridge on/off state, path, hook, last-seen offset")
    bsub.add_parser("poll", help="Hook entry point: inject new prusik-author entries as context")
    p_bwrite = bsub.add_parser("write", help="Append a structured entry to the bridge")
    p_bwrite.add_argument("--role", required=True,
                           choices=["live-cc", "prusik-author"])
    p_bwrite.add_argument("--kind", required=True,
                           help="QUESTION|BUG|OBSERVATION|FIX|GUIDANCE|DIAGNOSTIC|UPDATE")
    p_bwrite.add_argument("--body", required=True)

    p_uninstall = sub.add_parser("uninstall",
                                  help="Remove only what prusik installed (reads manifest)")
    p_uninstall.add_argument("--force", action="store_true",
                              help="Also remove files that have been modified since install")
    p_uninstall.add_argument("--keep-artifacts", action="store_true",
                              help="Keep .sprint/ (ledger, inventory, dep-graph) in place")

    p_refresh = sub.add_parser("refresh",
                                help="Sync new template files into this project (commands/agents/etc) "
                                     "without clobbering user-modified files")
    p_refresh.add_argument("--force", action="store_true",
                            help="Also overwrite user-modified files")
    p_refresh.add_argument("--no-auto-adopt", action="store_true",
                            help="Conservative mode: treat files missing from manifest as user-modified "
                                 "(pre-v0.4.2 behavior). Default is to auto-adopt stale stock templates.")
    p_refresh.add_argument("--no-merge-additions", action="store_true",
                            help="Skip surgical additive merge for .claude/settings.json when "
                                 "user-modified (pre-v0.5.8 behavior: skip entirely). Default is "
                                 "to union template's permissions.allow|deny|ask into project's, "
                                 "preserving user customizations.")

    sub.add_parser("disable", help="Pause hooks without removing files")
    sub.add_parser("enable", help="Resume hooks after disable")

    p_pause = sub.add_parser("pause",
                    help="Suspend Stop-hook exit-artifact enforcement for deliberate mid-phase pauses")
    # v0.6.3 (B8): accept variadic positional so unquoted reasons from slash
    # commands don't trip argparse's "unrecognized arguments". The reason is
    # recorded in .sprint/paused JSON and the pause_started ledger event.
    p_pause.add_argument("reason", nargs="*",
                          help="Optional reason for the pause (recorded for prusik status + ledger)")
    sub.add_parser("resume",
                    help="Re-engage Stop-hook enforcement after `prusik pause`")

    p_digest = sub.add_parser("digest", help="Summarize the ledger")
    p_digest.add_argument("--by-size", action="store_true",
                           help="Also group sprint durations by predicted size (S/M/L/XL)")
    p_metrics = sub.add_parser("metrics",
                                help="Defect-prevention scorecard from the ledger "
                                     "(what prusik flagged/caught/blocked). Factual "
                                     "event counts. --json for dashboards.")
    p_metrics.add_argument("--since", default=None,
                            help="Only count events at/after this ISO8601 timestamp")
    p_metrics.add_argument("--json", action="store_true",
                            help="Emit machine-readable JSON instead of text")

    # v0.45.0 — catch-quality ledger: was each gate/critic fire a true catch
    # or a false block? Per-gate precision = the friction-value ratio.
    p_catches = sub.add_parser("catches",
                                help="Per-gate true-catch vs false-block ratio "
                                     "(the friction-value ratio). --json for all.")
    p_catches.add_argument("--json", action="store_true",
                            help="Emit machine-readable JSON instead of text")
    # v0.172.0 — critic-recall: the unmeasured half of trust (precision = catches,
    # recall = MISSES). A miss leaves no critic-ledger trail, so it's inferred from
    # downstream catches + recorded via `critic-miss` (An adopter's escape taxonomy).
    p_recall_c = sub.add_parser("critic-recall",
                                help="What the critics MISSED — recall over confirmed "
                                     "misses + candidates inferred from downstream "
                                     "catches. --json for all.")
    p_recall_c.add_argument("--json", action="store_true",
                            help="Emit machine-readable JSON instead of text")
    from prusik import critic_recall as _critic_recall
    p_cmiss = sub.add_parser("critic-miss",
                             help="Record an observed escape (a real defect a critic "
                                  "passed), with the class + the owner that should "
                                  "have caught it.")
    p_cmiss.add_argument("--class", dest="defect_class", required=True,
                         choices=list(_critic_recall.ESCAPE_CLASSES),
                         help="Escape class (absence | cross_integration | "
                              "narrative_claim | unexamined_delta)")
    p_cmiss.add_argument("--owner", required=True,
                         help="Critic role that should have caught it (or the later "
                              "layer that did)")
    p_cmiss.add_argument("--feature", default=None, help="Feature the escape was in")
    p_cmiss.add_argument("--source", default="operator",
                         help="Who caught it downstream (operator/integrator/ci/…)")
    p_cmiss.add_argument("--reason", default="", help="What escaped + how it surfaced")
    # v0.47.0 — effort-telemetry: the VALUE lens (catches = the TRUST lens).
    # Time-per-phase, churn, friction per feature — derived from the ledger.
    p_effort = sub.add_parser("effort",
                              help="Per-feature journey cost: time-per-phase, "
                                   "rewind-churn, friction. --json for all.")
    p_effort.add_argument("--json", action="store_true",
                          help="Emit machine-readable JSON instead of text")

    # v0.48.0 — showcase: the composed trust narrative. Brings the ledger
    # timeline + adversarial verdicts + evidence + catch-quality + effort into
    # one legible per-feature story (intent→…→objective). Trust is christened
    # by showing the proof-of-work, not the work.
    p_showcase = sub.add_parser("showcase",
                                help="Composed per-feature trust narrative "
                                     "(omit feature for the journey roster).")
    p_showcase.add_argument("feature", nargs="?",
                            help="Feature to render the trust dossier for")
    p_showcase.add_argument("--json", action="store_true",
                            help="Emit machine-readable JSON instead of text")

    # v0.162.0 — adopter TRUST REPORT (Horizon-2 D): a per-REPO ROI dossier (vs
    # showcase's per-FEATURE narrative) — fidelity probe + verification catches +
    # prevention activity + throughput, all from THIS repo's ledger. Adopter-facing.
    p_trust = sub.add_parser("trust-report",
                             help="Per-repo trust dossier: what prusik gated, caught, "
                                  "and prevented here, plus a live fidelity probe.")
    p_trust.add_argument("--json", action="store_true",
                         help="Emit machine-readable JSON instead of text")
    p_trust.add_argument("--html", default=None,
                         help="Write a shareable self-contained HTML report to this path")

    # v0.50.0 — divergence-injection harness (instrument layer, TRUST keystone).
    # Inject known defects (scope drift / premature push / fabricated done) and
    # prove this config's deterministic guardrails catch them. Dual-use: our
    # efficacy proof + a customer self-verifying on their own config. rc≠0 on a
    # miss or false block.
    p_inject = sub.add_parser("inject",
                              help="Prove this config's deterministic guardrails "
                                   "catch known injected defects (rc≠0 on a gap).")
    p_inject.add_argument("--json", action="store_true",
                          help="Emit machine-readable JSON instead of text")

    # v0.51.0 / v0.194.0 — self-learning loop. Per-gate calibration signal +
    # recommendation from the labeled outcome ledger (PIPE). Per-adopter it stays
    # advisory (one codebase's labels overfit); the loop CLOSES across the fleet —
    # `calibrate apply` promotes a fleet-proven advisory detector to gating
    # (tightening-only, human-approved; HQ's `hq.calibrate` names the candidates).
    p_calibrate = sub.add_parser(
        "calibrate",
        help="Advisory per-gate calibration from labeled outcomes; "
             "`calibrate apply` promotes a fleet-proven advisory detector to "
             "gating (tightening only, human-approved).")
    p_calibrate.add_argument("--json", action="store_true",
                             help="Emit machine-readable JSON instead of text")
    cal_sub = p_calibrate.add_subparsers(dest="calibrate_cmd")
    p_cal_apply = cal_sub.add_parser(
        "apply",
        help="Promote ONE advisory recall detector to a hard gate (advisory→"
             "gating). Tightening-only and guarded: refuses preventive controls, "
             "already-gating gates, the reserved (human-adjudicated) detector, and "
             "anything unknown. The actuator that closes the self-learning loop.")
    p_cal_apply.add_argument("detector",
                             help="Detector to promote, e.g. absence_detector "
                                  "(see `python -m hq.calibrate` for what the "
                                  "fleet has proven).")

    # v0.54.0 — report: one composed per-product health snapshot (TRUST +
    # VALUE + improvement), value-chain framed. Composes the instrument layer.
    p_report = sub.add_parser("report",
                              help="One composed product-health snapshot "
                                   "(trust + value + progress). --json for all.")
    p_report.add_argument("--json", action="store_true",
                          help="Emit machine-readable JSON instead of text")
    p_report.add_argument("--export", dest="export_artifact", action="store_true",
                          help="v0.56.0 — write an ANONYMIZED, portable export "
                               "artifact (aggregate metrics only; no feature "
                               "names/paths/source) for opt-in sharing with HQ. "
                               "prusik never transmits it.")
    p_report.add_argument("--product", default="",
                          help="Human label for this product in the export "
                               "(you control it; default unnamed-product)")
    p_report.add_argument("--full", dest="export_full", action="store_true",
                          help="HQ-INTERNAL: full detail, NO anonymization (real "
                               "feature names + verbatim finding detail + open-"
                               "feature names). For HQ collecting products you own "
                               "into the private control room — NEVER for an "
                               "external adopter's self-export/share.")
    p_report.add_argument("--out", default=None,
                          help="Export file path (default .sprint/report-export.json)")
    p_report.add_argument("--stdout", dest="export_stdout", action="store_true",
                          help="Print the export to stdout instead of a file")

    # v0.84.0 — prusik update: package-staleness check + template refresh + the
    # restart reminder (the three-part update, one command). Multi-host distribution.
    p_update = sub.add_parser("update",
                              help="Check for a newer prusik release, sync this "
                                   "project's templates, and remind you to restart")
    p_update.add_argument("--timeout", type=float, default=3.0,
                          help="Seconds to wait on the version check (default 3)")

    p_catch = sub.add_parser("catch", help="Label one catch true/false")
    p_catch.add_argument("id", help="Catch id (from `prusik catches`)")
    cg = p_catch.add_mutually_exclusive_group(required=True)
    cg.add_argument("--true", dest="verdict", action="store_const",
                    const="true_catch", help="A real catch (forced a fix)")
    cg.add_argument("--false", dest="verdict", action="store_const",
                    const="false_block", help="A false block (friction)")
    p_catch.add_argument("--reason", default="", help="Optional note")

    sub.add_parser("status", help="Show current sprint state or prusik enabled/disabled state")

    p_doctor = sub.add_parser("doctor",
                                help="Self-assess harness health (5-subsystem 1-5 score) "
                                     "+ detect project drift since install (v0.8.5)")
    p_doctor.add_argument("--json", action="store_true",
                            help="Emit machine-readable JSON instead of text report")
    p_doctor.add_argument("--suggest-permissions", action="store_true",
                            help="v0.16.0 — mine the gate_blocked log and propose "
                                 "exact additive patches (permissions.allow / "
                                 "writable patterns) for recurring deny classes")
    p_doctor.add_argument("--insights", action="store_true",
                            help="v0.16.0 — pattern detection across past sprints "
                                 "(rewind clusters, fix-round spikes, calendar drift, "
                                 "F evidence uptake) with suggested actions")
    p_doctor.add_argument("--apply", action="store_true",
                            help="v0.17.0 — with --suggest-permissions, after y/N "
                                 "confirmation, write the suggested patches into "
                                 ".claude/settings.json + sprint-config.yaml via "
                                 "the additive merge (no clobber)")
    p_doctor.add_argument("--insights-for-brief", metavar="PATH", default=None,
                            help="v0.17.0 — forward-looking risk signals for a "
                                 "specific brief (Type, thin-Goal, history baseline)")
    p_doctor.add_argument("--sprint", metavar="FEATURE", default=None,
                            help="v0.17.0 — single-sprint retrospective view "
                                 "(durations, rewinds, fix-rounds, gate-blocks, "
                                 "retro signals)")

    # v0.28.0 — prusik verify-loop (closed-loop verification)
    p_vl = sub.add_parser("verify-loop",
                            help="Closed-loop verification: record findings at "
                                 "T0; check at T1 whether agent fixed them + "
                                 "added the suggested tests (v0.28.0)")
    vsub = p_vl.add_subparsers(dest="vl_cmd", required=True)
    p_vlr = vsub.add_parser("record",
                              help="Snapshot current findings as T0 baseline")
    p_vlr.add_argument("--feature", default="default",
                         help="Checkpoint key (default: 'default')")
    p_vlc = vsub.add_parser("check",
                              help="Compare T1 (now) against T0 checkpoint; "
                                   "rc=0 only if all T0 findings resolved")
    p_vlc.add_argument("--feature", default="default",
                         help="Checkpoint key (default: 'default')")
    p_vlc.add_argument("--run-tests", action="store_true",
                         help="Actually invoke pytest/jest on suggested tests "
                              "(default: grep-based 'is it in the suite' only)")
    p_vlc.add_argument("--json", action="store_true",
                         help="Emit machine-readable JSON instead of text")

    # v0.26.0 — prusik findings (agent-readable JSON contract)
    p_find = sub.add_parser("findings",
                              help="Emit findings (binding flags, gate blocks, "
                                   "etc.) as agent-readable JSON. v0.26.0.")
    p_find.add_argument("--since", default=None,
                          help="ISO8601 ts OR 'last-turn' (cursor since last "
                               "findings_consumed event)")
    p_find.add_argument("--source", default="ledger",
                          choices=("ledger", "scan", "both"),
                          help="Findings source (default: ledger). 'scan' "
                               "runs a fresh prusik scan; 'both' merges.")
    p_find.add_argument("--consume", action="store_true",
                          help="Append a findings_consumed event after "
                               "emitting (advances --since last-turn cursor)")
    p_find.add_argument("--text", action="store_true",
                          help="Human-readable output (default is JSON for "
                               "agent consumption)")

    # v0.24.0 — prusik scan (day-1 binding-mismatch + test-reach scan, no FSM dep)
    p_scan = sub.add_parser("scan",
                              help="Day-1 catch: scan an existing repo for "
                                   "binding-mismatch + test-reach risks WITHOUT "
                                   "requiring prusik FSM adoption (v0.24.0)")
    p_scan.add_argument("path", nargs="?", default=None,
                          help="Directory to scan (default: cwd)")
    p_scan.add_argument("--limit", type=int, default=5000,
                          help="Max files to walk before truncating (default: 5000)")
    p_scan.add_argument("--json", action="store_true",
                          help="Emit machine-readable JSON instead of text")
    p_scan.add_argument("--sarif", action="store_true",
                          help="Emit SARIF 2.1.0 (GitHub code-scanning / CI). "
                               "Takes precedence over --json")
    p_scan.add_argument("--include-test-reach", action="store_true",
                          help="Also run test-reach detection (usually empty in "
                               "scan-mode since touched-set = whole repo)")
    p_scan.add_argument("--detectors", default=None,
                          help="Comma-separated detector names to run (overrides "
                               "config; default: all registered). e.g. binding,my-check")
    p_scan.add_argument("--no-local-detectors", action="store_true",
                          help="Don't load project-local .claude/detectors/*.py")

    # v0.74.0 — prusik worktree-setup (JS/TS worktree deps + workspace build;
    # field seam #2, findings #10/#11)
    p_ws = sub.add_parser("worktree-setup",
                          help="Emit/run the deps-install + workspace-build setup "
                               "a fresh JS/TS worktree needs before it can "
                               "typecheck/test (no-op for non-JS stacks)")
    p_ws.add_argument("--dir", default=None,
                      help="Worktree dir to run setup in (default: project root)")
    p_ws.add_argument("--run", action="store_true",
                      help="Run the commands (fail-closed); default just prints them")
    p_ws.add_argument("--json", action="store_true",
                      help="Emit machine-readable JSON instead of text")

    # v0.69.0 — prusik affected-tests (fail-fast test selection; field finding #5)
    p_aff = sub.add_parser("affected-tests",
                           help="The touched/reach test subset to run FIRST "
                                "(fail-fast). Full suite still required at green.")
    p_aff.add_argument("feature", help="Feature name")
    p_aff.add_argument("--json", action="store_true",
                       help="Emit machine-readable JSON instead of text")

    # v0.68.0 — prusik cross-check (cross-builder contract drift; field finding #7)
    p_xcheck = sub.add_parser("cross-check",
                              help="Cross-builder drift: symbols defined in >1 "
                                   "worktree (parallel-builder collisions caught "
                                   "before the expensive sentinel)")
    p_xcheck.add_argument("feature", help="Feature name")
    p_xcheck.add_argument("--json", action="store_true",
                          help="Emit machine-readable JSON instead of text")

    # v0.65.0 — prusik infra-check (pre-flight infra gate; field finding #1)
    p_infra = sub.add_parser("infra-check",
                             help="Health-check the infra a feature's criteria "
                                  "declare (criteria.yaml `requires:`) before "
                                  "verify_commands — fail fast if DB/server down")
    p_infra.add_argument("feature", help="Feature name (briefs/<feature>.criteria.yaml)")
    p_infra.add_argument("--json", action="store_true",
                         help="Emit machine-readable JSON instead of text")
    p_infra.add_argument("--timeout", type=float, default=3.0,
                         help="Per-check timeout in seconds (default 3.0)")

    # v0.105.0 — prusik criterion resolve (defer→resolve complement; field finding #22)
    p_crit = sub.add_parser("criterion",
                            help="Resolve a DEFERRED (blocked_external) success "
                                 "criterion in-band: run its verify_command for "
                                 "real and, if it passes, record evidence + clear "
                                 "the deferral. Not a backdoor — guarded to "
                                 "blocked_external criteria only.")
    p_crit.add_argument("action", choices=["resolve"], help="resolve")
    p_crit.add_argument("feature", help="Feature name (briefs/<feature>.criteria.yaml)")
    p_crit.add_argument("criterion_id", help="The criterion id to resolve")
    p_crit.add_argument("--strict", action="store_true",
                        help="rc≠0 when the criterion still doesn't pass")

    # v0.63.0 — prusik plan-reach (plan-time blast-radius; field finding #2)
    p_planreach = sub.add_parser("plan-reach",
                                 help="Plan-time blast-radius: tests OUTSIDE the "
                                      "plan's module set that reference contracts "
                                      "it changes (catch ripples before the build)")
    p_planreach.add_argument("feature", help="Feature name (design/<feature>/plan.md)")
    p_planreach.add_argument("--json", action="store_true",
                             help="Emit machine-readable JSON instead of text")

    # v0.96.0 — prusik blast-verify (reviewing-side: was the plan-time prediction
    # consumed? Field retro #1 — predictions become gates, not ignored prose)
    p_blastverify = sub.add_parser("blast-verify",
                                   help="Reviewing-time: verify the plan's "
                                        "blast-radius prediction was consumed — "
                                        "predicted at-risk tests that were never "
                                        "updated this sprint")
    p_blastverify.add_argument("feature", help="Feature name")
    p_blastverify.add_argument("--json", action="store_true",
                               help="Emit machine-readable JSON instead of text")
    p_blastverify.add_argument("--strict", action="store_true",
                               help="rc≠0 on any unconsumed prediction (gate mode)")

    # v0.115.0 — prusik blast-recall (measure the gate's recall; an adopter: observe
    # the miss once, encode the edge-class forever)
    p_recall = sub.add_parser("blast-recall",
                              help="Run the regression and measure how many real "
                                   "failures the blast-radius prediction caught — "
                                   "surfaces SILENT MISSES (un-predicted breaks) "
                                   "as the next edge-class to encode.")
    p_recall.add_argument("feature", help="Feature name")
    p_recall.add_argument("--json", action="store_true")
    p_recall.add_argument("command", nargs=argparse.REMAINDER,
                          help="-- <regression command> (e.g. -- pytest -q)")

    # v0.173.0 — prusik absence-check (recall detector #1: the out-of-diff
    # "planned deliverable silently not produced" class — field escape #1)
    p_absence = sub.add_parser("absence-check",
                               help="Reconcile plan-declared files against the "
                                    "worktree — catch a promised deliverable (e.g. "
                                    "a test) that was never produced. --strict for rc≠0.")
    p_absence.add_argument("feature", help="Feature name")
    p_absence.add_argument("--json", action="store_true")
    p_absence.add_argument("--strict", action="store_true",
                           help="Return rc≠0 when a declared deliverable is absent")

    # v0.174.0 — prusik narrative-check (recall detector #2: gate the BUILDER's
    # "baseline-proven / pre-existing / flake" prose the way prove-flaky gates the
    # reviewer's — field escape #3, un-gated narrative claim)
    p_narr = sub.add_parser("narrative-check",
                            help="Reconcile a build report's proof-claims "
                                 "(baseline-proven / fails-on-main / flaky) against "
                                 "actual baseline proof. --strict for rc≠0.")
    p_narr.add_argument("feature", help="Feature name")
    p_narr.add_argument("--json", action="store_true")
    p_narr.add_argument("--strict", action="store_true",
                        help="Return rc≠0 when a proof-claim has no backing proof")

    # v0.175.0 — prusik delta-check (recall detector #3: tests that silently stop
    # running between worktree and integrated tree — field escape #4)
    p_delta = sub.add_parser("delta-check",
                             help="Run the suite on the integrated tree and compare "
                                  "its executed count to the worktree capture — catch "
                                  "tests that silently stopped running. --strict for rc≠0.")
    p_delta.add_argument("feature", help="Feature name")
    p_delta.add_argument("--json", action="store_true")
    p_delta.add_argument("--strict", action="store_true",
                         help="Return rc≠0 when fewer tests run than in the worktree")
    p_delta.add_argument("command", nargs=argparse.REMAINDER,
                         help="-- <full-suite command> (e.g. -- pytest -q)")

    # v0.186.0 — prusik ui-e2e-check (recall detector #4: UI-layer false confidence —
    # UI files changed but no rendered browser e2e covers them; fb-d4c9453120cd)
    p_uie2e = sub.add_parser("ui-e2e-check",
                             help="Flag a UI-touching feature whose acceptance criteria "
                                  "carry no rendered (browser) e2e — API-level e2e gives "
                                  "false UI-layer confidence. Advisory; --strict for rc≠0.")
    p_uie2e.add_argument("feature", help="Feature name")
    p_uie2e.add_argument("--json", action="store_true")
    p_uie2e.add_argument("--strict", action="store_true",
                         help="Return rc≠0 when the UI layer has no rendered e2e")

    # v0.97.0 — prusik feedback (structured findings capture; Phase 3 Pillar C —
    # the scale backbone that rides the export to the HQ findings-spine)
    from prusik import feedback as _feedback
    p_fb = sub.add_parser("feedback",
                          help="File a structured prusik finding (bug/friction/"
                               "request) — rides the export to HQ, tracked to a "
                               "release. The scale path; bridge is design-partners only.")
    p_fb.add_argument("title", nargs="?",
                      help="One-line finding title — OR a ticket verb: "
                           "show | reply | resolve | verify")
    p_fb.add_argument("ref", nargs="?", help="fb-<id> when `title` is a verb")
    p_fb.add_argument("--kind", choices=_feedback.KINDS, default="friction",
                      help="Finding kind (default: friction)")
    p_fb.add_argument("--severity", choices=_feedback.SEVERITIES, default=None)
    p_fb.add_argument("--detail", default="", help="Optional body / repro detail")
    p_fb.add_argument("--repro", default="", help="Repro command (carried on the ticket)")
    p_fb.add_argument("--list", action="store_true",
                      help="List findings filed in this project")
    # ── ticket verbs (the per-finding loop; designed with live-cc) ──
    p_fb.add_argument("--body", default="", help="reply: the comment body")
    p_fb.add_argument("--role", choices=["adopter", "prusik-author"],
                      default="adopter", help="reply: who is speaking")
    p_fb.add_argument("--fix", action="store_true",
                      help="resolve: mark fixed (REQUIRES --verify-cmd; closes only "
                           "on a green run)")
    p_fb.add_argument("--reject", action="store_true",
                      help="resolve: aligned-rejection → wontfix (REQUIRES --reason)")
    p_fb.add_argument("--verify-cmd", dest="verify_cmd", default="",
                      help="resolve --fix: command whose green run closes the finding")
    p_fb.add_argument("--verify-kind", dest="verify_kind", default="tests",
                      choices=["tests", "lint", "types"])
    p_fb.add_argument("--fixed-in", dest="fixed_in", default="",
                      help="resolve --fix: commit/version that carries the fix")
    p_fb.add_argument("--reason", default="", help="resolve --reject: the reason")
    p_fb.add_argument("--all-closed", dest="all_closed", action="store_true",
                      help="verify: re-run EVERY verified-closed finding (sweep)")
    p_fb.add_argument("--touched", nargs="*", default=None,
                      help="verify: re-run closed findings referencing these modules")

    # v0.35.0 — prusik prove (standalone anti-fabrication gate, no FSM)
    p_prove = sub.add_parser("prove",
                             help="Prove a test/lint/type command ACTUALLY ran "
                                  "clean from its own output (not the agent's "
                                  "word). No init/sprint needed. rc=0 only if "
                                  "proven. e.g. `prusik prove -- pytest -q`")
    p_prove.add_argument("--kind", default="tests",
                          choices=("tests", "lint", "types"),
                          help="What the command is (default: tests). 'tests' "
                               "requires real executed tests, not just exit 0.")
    p_prove.add_argument("--min", type=int, default=1, dest="min_executed",
                          help="Minimum executed tests required (kind=tests; default 1)")
    p_prove.add_argument("--json", action="store_true",
                          help="Emit machine-readable verdict JSON")
    p_prove.add_argument("--sarif", action="store_true",
                          help="Emit SARIF 2.1.0 verdict (GitHub code-scanning / "
                               "CI); a NOT-PROVEN run is one error result")
    p_prove.add_argument("command", nargs=argparse.REMAINDER,
                          help="-- <verbatim test/lint/type command>")

    # v0.29.x — prusik ci-comment (format scan/verify-loop/findings JSON as a
    # GitHub PR-comment markdown body for the composite Action at action.yml)
    p_cic = sub.add_parser("ci-comment",
                             help="Format `prusik scan|verify-loop check|findings` "
                                  "--json output as GitHub PR-comment markdown "
                                  "(stdin or path). Decision-support, not a gate.")
    p_cic.add_argument("input", nargs="?", default=None,
                         help="Path to findings JSON (default/'-': read stdin)")

    # v0.21.0 — prusik eval suite
    p_eval = sub.add_parser("eval",
                              help="Empirical benchmark of prusik's checks "
                                   "against observed defect classes (v0.21.0)")
    esub = p_eval.add_subparsers(dest="eval_cmd", required=True)
    esub.add_parser("list", help="List corpus cases")
    p_erun = esub.add_parser("run",
                              help="Run the eval suite; rc=0 all-pass, rc=1 any-miss")
    p_erun.add_argument("--case", default=None,
                          help="Run a single case (matched by id-prefix)")
    p_erun.add_argument("--json", action="store_true",
                          help="Emit machine-readable JSON instead of text")
    # v0.25.0 — agent-control comparison
    p_eac = esub.add_parser("agent-control",
                              help="prusik-on vs prusik-off (vibe-coding) comparison "
                                   "on the corpus (v0.25.0). Substantiates: "
                                   "'prusik catches what vibe-coding misses.'")
    p_eac.add_argument("--case", default=None,
                          help="Run a single case (matched by id-prefix)")
    p_eac.add_argument("--json", action="store_true",
                          help="Emit machine-readable JSON instead of text")
    # v0.161.0 — the unified, version-stamped fidelity SCORECARD (the keystone): folds
    # divergence-injection + corpus catch-rate + agent-control into ONE artifact with a
    # pass/fail FLOOR. rc≠0 if any signal regresses → a gate-weakening change fails it.
    p_esc = esub.add_parser("scorecard",
                              help="Unified fidelity scorecard (injection + corpus + "
                                   "agent-control) with a pass/fail floor; rc≠0 on any "
                                   "regression. The evidence layer for the trust report.")
    p_esc.add_argument("--json", action="store_true",
                          help="Emit machine-readable JSON instead of text")
    p_esc.add_argument("--out", default=None,
                          help="Also write the scorecard JSON to this path")

    args = parser.parse_args()

    if args.cmd == "init":
        from prusik.init import run as init_run
        if args.list_stacks:
            from pathlib import Path as _P
            preset_dir = _P(__file__).parent / "templates" / ".claude" / "sprint-config-presets"
            stacks = sorted(p.stem for p in preset_dir.glob("*.yaml"))
            print("Available --stack presets:")
            for s in stacks:
                print(f"  - {s}")
            print("\n(default: no preset, uses the generic prusik template)")
            return 0
        return init_run(conventions=args.conventions, force=args.force,
                        merge_settings=not args.no_merge_additions,
                        stack=args.stack, allow_dirty=args.allow_dirty,
                        merge_hooks=args.merge_hooks,
                        minimal_perms=args.minimal_perms)
    if args.cmd == "gate":
        from prusik import gate
        fn = args.gate_cmd.replace("-", "_")
        return getattr(gate, fn)(args)
    if args.cmd == "discovery":
        from prusik import discovery
        return discovery.dispatch(args.disc_cmd)
    if args.cmd == "sprint":
        from prusik.sprint_cli import run as sprint_run
        return sprint_run(args.feature, force_lane=args.lane, yes=args.yes)
    if args.cmd == "triage":
        from prusik.triage import run as triage_run
        return triage_run(args.feature)
    if args.cmd == "brief-lint":
        from prusik.brief_lint import lint as _lint
        return _lint(args.path, cutoff=args.cutoff)
    if args.cmd == "agents":
        if args.agents_cmd == "doctor":
            from prusik.agents_doctor import doctor
            return doctor()
    if args.cmd == "permissions":
        if args.perms_cmd == "audit":
            from prusik.permissions import audit
            return audit()
        if args.perms_cmd == "add":
            from prusik.permissions import add
            return add(args.rule, reason=args.reason)
    if args.cmd == "issues":
        from prusik import issues
        if args.issues_cmd == "sync":
            return issues.sync()
        if args.issues_cmd == "search":
            import json
            for r in issues.search(args.query, args.limit):
                print(json.dumps(r))
            return 0
    if args.cmd == "watchdog":
        from prusik import watchdog
        if args.poll:
            return watchdog.poll(args.poll)
        return watchdog.check()
    if args.cmd == "serve":
        from prusik import serve
        return serve.serve(args.port)
    if args.cmd == "bridge":
        from prusik import bridge
        if args.bridge_cmd in ("on", "enable"):
            return bridge.on(args.slug)
        if args.bridge_cmd in ("off", "disable"):
            return bridge.off()
        if args.bridge_cmd == "status":
            return bridge.status()
        if args.bridge_cmd == "poll":
            return bridge.poll()
        if args.bridge_cmd == "write":
            return bridge.write_entry(args.role, args.kind, args.body)
    if args.cmd == "uninstall":
        from prusik.uninstall import run as uninstall_run
        return uninstall_run(keep_artifacts=args.keep_artifacts, force=args.force)
    if args.cmd == "refresh":
        from prusik.refresh import run as refresh_run
        return refresh_run(force=args.force, no_auto_adopt=args.no_auto_adopt,
                           no_merge_additions=args.no_merge_additions)
    if args.cmd == "disable":
        from prusik.toggle import disable
        return disable()
    if args.cmd == "enable":
        from prusik.toggle import enable
        return enable()
    if args.cmd == "pause":
        from prusik.pause import pause
        # Variadic positional → join with spaces for the recorded reason text.
        reason_words = getattr(args, "reason", None) or []
        reason = " ".join(reason_words).strip() or None
        return pause(reason=reason)
    if args.cmd == "resume":
        from prusik.pause import resume
        return resume()
    if args.cmd == "digest":
        from prusik.ledger import digest
        return digest(by_size=getattr(args, "by_size", False))
    if args.cmd == "metrics":
        from prusik import metrics
        return metrics.run(since=args.since, json_output=args.json)
    if args.cmd == "catches":
        from prusik import catch_quality
        return catch_quality.run(json_output=args.json)
    if args.cmd == "critic-recall":
        from prusik import critic_recall
        return critic_recall.run(json_output=args.json)
    if args.cmd == "absence-check":
        from prusik import absence
        return absence.run(args.feature, json_output=args.json, strict=args.strict)
    if args.cmd == "narrative-check":
        from prusik import narrative_claim
        return narrative_claim.run(args.feature, json_output=args.json,
                                   strict=args.strict)
    if args.cmd == "delta-check":
        from prusik import suite_delta
        return suite_delta.run(args.feature, args.command, json_output=args.json,
                               strict=args.strict)
    if args.cmd == "ui-e2e-check":
        from prusik import ui_coverage
        return ui_coverage.run(args.feature, json_output=args.json, strict=args.strict)
    if args.cmd == "critic-miss":
        from prusik import critic_recall
        return critic_recall.record_miss(
            args.defect_class, args.owner, feature=args.feature,
            source=args.source, reason=args.reason)
    if args.cmd == "effort":
        from prusik import effort
        return effort.run(json_output=args.json)
    if args.cmd == "trust-report":
        from prusik import trust_report
        return trust_report.run(json_output=args.json,
                                html_out=getattr(args, "html", None))

    if args.cmd == "showcase":
        from prusik import showcase
        return showcase.run(feature=args.feature, json_output=args.json)
    if args.cmd == "inject":
        from prusik import injection
        return injection.run(json_output=args.json)
    if args.cmd == "calibrate":
        from prusik import calibration
        if getattr(args, "calibrate_cmd", None) == "apply":
            return calibration.apply(args.detector)
        return calibration.run(json_output=args.json)
    if args.cmd == "report":
        from prusik import report
        return report.run(json_output=args.json,
                          export_artifact=args.export_artifact,
                          product=args.product, out=args.out,
                          to_stdout=args.export_stdout,
                          full_detail=getattr(args, "export_full", False))
    if args.cmd == "catch":
        from prusik import catch_quality
        return catch_quality.resolve(args.id, args.verdict, reason=args.reason)
    if args.cmd == "status":
        from prusik.phases import print_status
        return print_status()
    if args.cmd == "doctor":
        from prusik import doctor
        return doctor.run(json_output=args.json,
                          suggest_permissions=args.suggest_permissions,
                          suggest_apply=args.apply,
                          insights=args.insights,
                          insights_for_brief=args.insights_for_brief,
                          sprint=args.sprint)
    if args.cmd == "verify-loop":
        from prusik import verify_loop
        if args.vl_cmd == "record":
            return verify_loop.record(feature=args.feature)
        if args.vl_cmd == "check":
            return verify_loop.check(feature=args.feature,
                                       run_tests=args.run_tests,
                                       json_output=args.json)
    if args.cmd == "findings":
        from prusik import findings as kit_findings
        return kit_findings.run(since=args.since, source=args.source,
                                  consume=args.consume,
                                  json_output=not args.text)
    if args.cmd == "prove":
        from prusik import prove
        return prove.run(args.command, kind=args.kind,
                         min_executed=args.min_executed, json_output=args.json,
                         sarif_output=args.sarif)
    if args.cmd == "plan-reach":
        from prusik import blast_plan
        return blast_plan.run(args.feature, json_output=args.json)
    if args.cmd == "blast-verify":
        from prusik import blast_plan
        return blast_plan.verify_run(args.feature, json_output=args.json,
                                     strict=args.strict)
    if args.cmd == "blast-recall":
        from prusik import blast_plan
        return blast_plan.recall_run(args.feature, args.command,
                                     json_output=args.json)
    if args.cmd == "feedback":
        from prusik import feedback
        return feedback.run(args)
    if args.cmd == "infra-check":
        from prusik import infra_check
        return infra_check.run(args.feature, json_output=args.json,
                               timeout=args.timeout)
    if args.cmd == "criterion":
        from prusik import criterion
        return criterion.resolve(args.feature, args.criterion_id,
                                 strict=args.strict)
    if args.cmd == "cross-check":
        from prusik import cross_builder
        return cross_builder.run(args.feature, json_output=args.json)
    if args.cmd == "affected-tests":
        from prusik import affected
        return affected.run(args.feature, json_output=args.json)
    if args.cmd == "worktree-setup":
        from prusik import worktree_setup
        return worktree_setup.run(dir_=args.dir, do_run=args.run,
                                  json_output=args.json)
    if args.cmd == "update":
        from prusik import update_cmd
        return update_cmd.run(timeout=args.timeout)
    if args.cmd == "ci-comment":
        from prusik import ci_comment
        return ci_comment.run(args.input)
    if args.cmd == "scan":
        from prusik import scan as kit_scan
        from pathlib import Path as _P
        root = _P(args.path).resolve() if args.path else None
        names = ([n.strip() for n in args.detectors.split(",") if n.strip()]
                 if args.detectors else None)
        return kit_scan.scan(root=root,
                              file_limit=args.limit,
                              json_output=args.json,
                              sarif_output=args.sarif,
                              include_test_reach=args.include_test_reach,
                              detector_names=names,
                              allow_local=not args.no_local_detectors)
    if args.cmd == "eval":
        from prusik import eval as kit_eval
        if args.eval_cmd == "list":
            cases = kit_eval.list_cases()
            if not cases:
                print("[prusik-eval] no corpus cases found.")
                return 0
            print(f"[prusik-eval] {len(cases)} corpus case(s):\n")
            for c in cases:
                print(f"  • {c['id']}")
                print(f"      defect_class: {c['defect_class']}")
                print(f"      trial_origin: {c['trial_origin']}")
            return 0
        if args.eval_cmd == "run":
            return kit_eval.run(case_filter=args.case,
                                  json_output=args.json)
        if args.eval_cmd == "agent-control":
            return kit_eval.run_agent_control(case_filter=args.case,
                                                json_output=args.json)
        if args.eval_cmd == "scorecard":
            return kit_eval.scorecard(json_output=args.json,
                                      out=getattr(args, "out", None))
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
