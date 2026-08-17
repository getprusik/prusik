"""Stable gate-class labels for block events — the ONE surface that answers
"which gate blocked, as a stable key?", so a repeat-bounce (an agent re-hitting
the SAME gate class within one sprint = the remedy didn't land the first time)
is MEASURABLE rather than reconstructed from free-text reasons.

WHY THIS EXISTS. Block events (`gate_blocked` / `advance_blocked` /
`sprint_start_blocked`) carried only a human `reason` string. A retrospective
over the fleet's ledgers found ~half of all block events were wasted
re-bounces — but attributing them meant normalizing prose, and the dominant
`advance_blocked` cases carried NO reason at all (only structured `missing=` /
`inconsistencies=` fields). Prose is also exactly what gets reworded when a
remedy is improved (the whole point of measuring bounces), so a reason-derived
key is self-defeating. This module makes the class a STABLE key.

TWO ENTRY POINTS THAT MUST AGREE (pinned by the completeness test):
  - EMISSION — each block-event site passes `gate_class=<a GATE_CLASSES value>`.
    Precise, self-declared, refactor-proof: rewording a reason can't move it.
  - READ — `classify(event)` returns the class for ANY block event: the explicit
    `gate_class` when present, else DERIVED from the event's own fields
    (reason/missing/inconsistencies), so the whole HISTORICAL ledger — written
    before the field existed — still classifies. An event that genuinely
    recorded nothing identifying is `unclassified` (honest, never guessed).

THE MAINTENANCE CONTRACT — a new block class is: (1) a constant here, (2) its
value in GATE_CLASSES, (3) a derive-rule in `classify` for pre-field history,
(4) the `gate_class=` kwarg at its emission site, (5) a test row. The
completeness test pins constants↔GATE_CLASSES so none is added without a name.
"""

from __future__ import annotations

# The closed set of stable classes. APPEND here when a new block site is added.
WRITER_LEASE = "writer_lease"                       # shared-tree writer lease held by another session
WRITABLE_SCOPE = "writable_scope"                   # write/redirect outside the phase's writable set
DENY_COMMAND = "deny_command"                        # deny_commands / deny_bash phase policy
UNMET_EXIT_ARTIFACTS = "unmet_exit_artifacts"        # advance: required exit artifacts absent
CROSS_ARTIFACT_INCONSISTENCY = "cross_artifact_inconsistency"  # advance: cross-artifact drift
FULL_SUITE_NOT_PROVEN = "full_suite_not_proven"      # advance: full-suite evidence missing
REWIND_GUARD = "rewind_guard"                        # advance: rewind without --allow-rewind
PUSH_OR_PARK = "push_or_park"                         # advance: unpushed sprint work
CI_OBSERVE = "ci_observe"                             # advance: CI-job deliverable never observed running
SPRINT_START_GATE = "sprint_start_gate"              # sprint-start: unmet pre-sprint gate(s)
UNCLASSIFIED = "unclassified"                         # recorded nothing identifying — never guessed

# Completeness anchor — the test pins this to the module's constants so a class
# can't be emitted without a registered name (and therefore without a derive-rule
# + observability). `unclassified` is the honest sink, always last.
GATE_CLASSES = (
    WRITER_LEASE,
    WRITABLE_SCOPE,
    DENY_COMMAND,
    UNMET_EXIT_ARTIFACTS,
    CROSS_ARTIFACT_INCONSISTENCY,
    FULL_SUITE_NOT_PROVEN,
    REWIND_GUARD,
    PUSH_OR_PARK,
    CI_OBSERVE,
    SPRINT_START_GATE,
    UNCLASSIFIED,
)

# Block event types this module classifies. Anything else → not a block.
BLOCK_EVENTS = frozenset(("gate_blocked", "advance_blocked", "sprint_start_blocked"))


def _derive(event: dict) -> str:
    """Class for a block event that carries NO explicit `gate_class` (the whole
    historical ledger). Derived from the event's own structured fields first,
    then reason keywords; `unclassified` when nothing identifies it — the
    reason field was genuinely empty at some legacy sites, and a guess would
    pollute the very signal we're measuring."""
    ev = event.get("event")
    reason = (event.get("reason") or "").lower()

    if ev == "sprint_start_blocked":
        return SPRINT_START_GATE

    if ev == "gate_blocked":
        if reason.startswith("shared-tree writer lease"):
            return WRITER_LEASE
        if reason.startswith("deny command") or reason.startswith("deny pattern"):
            return DENY_COMMAND
        # L149 reason comes from is_path_writable ("… not in writable patterns …");
        # L170 is "bash redirect to unwriteable path …". Both are one class.
        if ("writable pattern" in reason or "unwriteable" in reason
                or "unwritable" in reason):
            return WRITABLE_SCOPE
        return UNCLASSIFIED

    if ev == "advance_blocked":
        # Structured fields identify the reason-less legacy sites precisely.
        if "missing" in event:
            return UNMET_EXIT_ARTIFACTS
        if "inconsistencies" in event:
            return CROSS_ARTIFACT_INCONSISTENCY
        if reason.startswith("full-suite not proven"):
            return FULL_SUITE_NOT_PROVEN
        if reason.startswith("rewind"):
            return REWIND_GUARD
        if reason.startswith("push_or_park"):
            return PUSH_OR_PARK
        if reason.startswith("ci_observe"):
            return CI_OBSERVE
        return UNCLASSIFIED

    return UNCLASSIFIED


def classify(event: dict) -> str:
    """The stable gate class for a block event. Explicit `gate_class` (emitted at
    the site) is authoritative; otherwise derived from the event's fields so
    pre-field history still classifies. A validated `gate_class` outside
    GATE_CLASSES is ignored in favour of derivation (fail toward the known set)."""
    explicit = event.get("gate_class")
    if explicit in GATE_CLASSES:
        return explicit
    return _derive(event)
