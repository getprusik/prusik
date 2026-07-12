"""Fix-round state management (v0.5.7).

When a reviewer returns FAIL with small fixable defects, the orchestrator
(/sprint-run step 6) triggers a fix round: temporarily expand writable scope
so builders can patch in `worktrees/*/**`, re-run the reviewer, PASS or
escalate. Max 2 rounds per sprint; the third FAIL escalates to a bridge BUG.

State lives at `.sprint/fix-round.json`:
    {"feature": "...", "round": 1, "started_at": "..."}

`phases.is_path_writable` checks for this marker and extends writable when
active AND current phase is `reviewing`. The marker is also visible to
`prusik status`. Ended rounds are cleared; the ledger keeps the history.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from prusik import ledger

MAX_ROUNDS = 2


def _marker_path(root: Path) -> Path:
    return root / ".sprint" / "fix-round.json"


def current_state(root: Path | None = None) -> dict | None:
    root = root or ledger.project_root()
    p = _marker_path(root)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _load_fan_out_files(feature: str, root: Path) -> list[str]:
    """The blast-radius PREDICTION's reverse-dep files (system-computed from the
    dep-graph, persisted by blast_plan.record_prediction) — the SANCTIONED fan-out set
    a fix-round may touch in project root. Bounded EXACTLY to what the prediction names:
    a reverse-dep the prediction MISSED is a recall gap to encode via the blast-recall
    loop, never a reason to widen the writable boundary to un-predicted files."""
    from prusik import blast_plan
    p = blast_plan._prediction_path(feature, root)
    if not p.exists():
        return []
    try:
        pred = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    return sorted(set((pred.get("at_risk_tests") or [])
                      + (pred.get("symbol_leak_tests") or [])))


def fan_out_files(root: Path | None = None) -> list[str]:
    """The predicted reverse-dep files made writable by the active fix-round's fan-out
    lane (empty when no round is active or no prediction exists)."""
    st = current_state(root)
    return list((st or {}).get("fan_out_files") or [])


def is_active(root: Path | None = None) -> bool:
    return current_state(root) is not None


def reap(root: Path | None = None, *, reason: str = "") -> dict | None:
    """Delete any fix-round marker and log it. Idempotent — no-op if none.
    Returns the reaped state (for caller logging) or None.

    v0.11.1 (Candidate S): a fix-round marker must never outlive its owning
    sprint or leak into another. m4-s8c→#13: an open round from a bypassed
    (no `sprint_complete`) sprint survived ~26h and silently granted
    `worktrees/*/**` writable-expansion to a *different* sprint's reviewing
    phase — an isolation-invariant break. Reaping at sprint terminals AND
    on foreign-feature `start` closes it.
    """
    root = root or ledger.project_root()
    st = current_state(root)
    if st is None:
        return None
    try:
        _marker_path(root).unlink()
    except OSError:
        pass
    ledger.append("fix_round_reaped", feature=st.get("feature"),
                   round=st.get("round"), reason=reason)
    return st


def _count_rounds_from_ledger(feature: str) -> int:
    """Count how many fix_round_start events have fired for this feature.
    Used to enforce the hard cap across start/end cycles (ending a round
    must NOT reset the counter)."""
    count = 0
    for r in ledger.read_all():
        if r.get("event") == "fix_round_start" and r.get("feature") == feature:
            count += 1
    return count


# v0.11.0 #3 — in-prusik escalation. The MAX_ROUNDS cap previously dead-ended
# into "/sprint-run step 6: write a BUG to the bridge and STOP" — a manual
# halt OUTSIDE prusik. That is the exact precursor to the m4-s8c bypass
# ("operator chose direct-integrate, no sprint_complete record — prusik
# pipeline bypassed"). The cap's real purpose (prevent unbounded auto-
# retry) is preserved; what changes is that hitting it no longer forces
# the operator OUT of the system to act. The decision stays IN the ledger.
# Ledger-driven (the #1/#2 lesson: derive from the audit trail, not new
# state keys): effective cap and the integrate override are both computed
# from `fix_round_escalation` events — no parallel FSM, no state flag.
_ESCALATION_DECISIONS = ("extend-once", "integrate-with-flag", "abandon")


def _count_escalations(feature: str, decision: str) -> int:
    n = 0
    for r in ledger.read_all():
        if (r.get("event") == "fix_round_escalation"
                and r.get("feature") == feature
                and r.get("decision") == decision):
            n += 1
    return n


def latest_integrate_escalation(feature: str) -> dict | None:
    """Most recent integrate-with-flag escalation for this feature, or None.
    Read by the advance path to honor a recorded, rationale-bearing
    operator override of the reviewing correctness gate (loud + audited —
    NOT a fabricated PASS; reports still say FAIL, the ledger says why)."""
    found = None
    for r in ledger.read_all():
        if (r.get("event") == "fix_round_escalation"
                and r.get("feature") == feature
                and r.get("decision") == "integrate-with-flag"):
            found = r
    return found


def _failing_reviewers(feature: str, root: Path) -> list[str]:
    out: list[str] = []
    for name in ("regression.txt", "conventions.txt"):
        p = root / "reports" / feature / name
        if p.exists():
            first = (p.read_text().splitlines() or [""])[0].strip()
            if first and not first.startswith("PASS"):
                out.append(f"{name}:{first[:40]}")
        else:
            out.append(f"{name}:absent")
    return out


def classify(feature: str, test_fixable: int = 0, source_defect: int = 0,
             pre_existing: int = 0, note: str = "",
             root: Path | None = None) -> int:
    """Record the sentinel's residual classification (v0.70.0, field finding #3) so
    `escalate --auto` can recommend a decision from it. The sentinel already
    judges each residual failure semantically (test-fixable / real source defect
    / pre-existing inherited debt); this stores that split structurally instead
    of leaving it as prose the operator must read and weigh by hand."""
    root = root or ledger.project_root()
    ledger.append("fix_round_residual", feature=feature,
                  test_fixable=int(test_fixable),
                  source_defect=int(source_defect),
                  pre_existing=int(pre_existing), note=note.strip())
    print(f"[fix-round] residual recorded for '{feature}': "
          f"test-fixable={test_fixable} source-defect={source_defect} "
          f"pre-existing={pre_existing}")
    rec, why = recommend_decision(feature, root)
    if rec:
        print(f"            → recommended: {rec} — {why}")
    return 0


def _latest_residual(feature: str, root: Path) -> dict | None:
    latest = None
    for r in ledger.read_all():
        if (r.get("event") == "fix_round_residual"
                and r.get("feature") == feature):
            latest = r
    return latest


def recommend_decision(feature: str, root: Path) -> tuple[str | None, str]:
    """Derive an escalation recommendation from the latest residual split.
    Returns (recommendation, rationale). The ONLY auto-actionable case is
    'extend-once' on a test-fixable / zero-source-defect residual — a bounded
    extra round. A real source defect or inherited debt routes to a human; the
    convergence control is never auto-loosened on uncertain ground."""
    res = _latest_residual(feature, root)
    if not res:
        return None, "no residual classification recorded"
    tf = res.get("test_fixable", 0) or 0
    sd = res.get("source_defect", 0) or 0
    pe = res.get("pre_existing", 0) or 0
    if sd > 0:
        return "human-review", (
            f"{sd} source-defect residual(s) — a real defect needs builder "
            f"work, not just a bounded extra round; review before extending.")
    if tf > 0:
        return "extend-once", (
            f"{tf} test-fixable residual(s), 0 source defects — a bounded extra "
            f"round greens the tests. Safe to extend once.")
    if pe > 0:
        return "human-review", (
            f"{pe} pre-existing residual(s), 0 from this sprint — inherited "
            f"debt; consider integrate-with-flag or a known-failure baseline, "
            f"not an extend.")
    return None, "residual is all-zero — nothing to escalate"


def _escalate_auto(feature: str, root: Path) -> int:
    """Advisory: read the recorded residual classification and RECOMMEND a
    decision. Never auto-applies — the operator (or a future calibrated
    actuator) runs the apply command, keeping the decision explicit."""
    rec, why = recommend_decision(feature, root)
    if rec is None:
        print(f"[fix-round] --auto: {why}.")
        print(f"            Record it first: `prusik gate fix-round classify "
              f"--feature {feature} --test-fixable N --source-defect M "
              f"--pre-existing K` (the sentinel's a/b/c split).")
        return 2
    print(f"[fix-round] --auto recommendation for '{feature}': {rec}")
    print(f"            {why}")
    if rec == "extend-once":
        print(f"            apply:  prusik gate fix-round escalate --feature "
              f"{feature} --decision extend-once --rationale \"{why}\"")
    else:
        print("            → human review recommended — NOT an auto-extend.")
    return 0


def escalate(feature: str, decision: str | None = None, rationale: str | None = None,
             root: Path | None = None, auto: bool = False) -> int:
    """Record an in-prusik operator decision at the fix-round cap.

    Exit codes: 0 — recorded / recommendation printed; 2 — invalid decision /
    missing rationale / escalation not warranted yet (cap not reached).

      extend-once       grant exactly ONE more fix round (cap += 1 for
                        this feature). Bounded — re-escalation needed
                        again at the new cap.
      integrate-with-flag  accept the sprint despite reviewer FAIL. The
                        advance path honors this as a recorded override;
                        reports keep saying FAIL, the ledger records the
                        rationale + which gates were overridden.
      abandon           stop the sprint, recorded. Honest terminal state
                        instead of an ambiguous out-of-prusik STOP.
    """
    root = root or ledger.project_root()
    if auto:
        return _escalate_auto(feature, root)
    if decision not in _ESCALATION_DECISIONS:
        print(f"[fix-round] invalid --decision {decision!r}; "
              f"choose one of {list(_ESCALATION_DECISIONS)} (or use --auto).")
        return 2
    if not rationale or not rationale.strip():
        print("[fix-round] --rationale is required (no silent override). "
              "Escalation is a recorded decision, not a bypass.")
        return 2
    prior = _count_rounds_from_ledger(feature)
    effective_cap = MAX_ROUNDS + _count_escalations(feature, "extend-once")
    if prior < effective_cap:
        print(f"[fix-round] escalation not warranted: feature '{feature}' "
              f"has used {prior}/{effective_cap} rounds. Use "
              f"`prusik gate fix-round start --feature {feature}` — you still "
              f"have a round.")
        return 2
    failing = _failing_reviewers(feature, root)
    ledger.append("fix_round_escalation", feature=feature, decision=decision,
                   rationale=rationale.strip(), prior_rounds=prior,
                   failing_reviewers=failing)
    if decision == "extend-once":
        print(f"[fix-round] ESCALATION recorded: one extra round granted for "
              f"'{feature}' (cap now {effective_cap + 1}). Rationale logged.")
        print(f"            Run `prusik gate fix-round start --feature {feature}`.")
    elif decision == "integrate-with-flag":
        print(f"[fix-round] ESCALATION recorded: '{feature}' will integrate "
              f"DESPITE reviewer FAIL ({failing}). This is loud + audited — "
              f"reports keep their FAIL; the ledger records why.")
        print("            Advance to integrating; the gate will honor this "
              "recorded override and emit `integrated_under_escalation`.")
    else:  # abandon
        from prusik import phases
        st = phases.current_sprint_state() or {}
        if st.get("feature") == feature:
            phases.clear_sprint_state(root)
        if is_active(root):
            _marker_path(root).unlink()
        print(f"[fix-round] ESCALATION recorded: '{feature}' ABANDONED by "
              f"operator decision. Sprint state cleared. Rationale logged — "
              f"an honest terminal state, not an out-of-prusik STOP.")
    return 0


def start(feature: str, root: Path | None = None) -> int:
    """Begin a fix round. Fails if one is already active or if the hard cap
    has been reached.

    Exit codes:
      0 — started
      1 — already active (call end first)
      2 — cap exceeded (escalate to bridge)
    """
    root = root or ledger.project_root()
    st = current_state(root)
    if st is not None:
        owner = st.get("feature")
        if owner == feature:
            print(f"[fix-round] A fix round is already active for '{feature}'; "
                  f"run `prusik gate fix-round end --feature {feature}` first.")
            return 1
        # v0.11.1 (Candidate S): the marker is owned by a DIFFERENT feature
        # — a stale orphan from a non-current sprint (prusik runs one
        # sprint at a time). Reap it rather than silently blocking this
        # sprint behind another's dead state (the m4-s8c→#13 leak), and
        # name the owner so the mismatch is diagnosable here, not only at
        # `end` (the second reported defect).
        reap(root, reason=f"orphan: owned by {owner!r}, starting {feature!r}")
        print(f"[fix-round] Reaped stale fix-round owned by '{owner}' "
              f"(not this sprint) before starting '{feature}'.")
    prior = _count_rounds_from_ledger(feature)
    # v0.11.0 #3: the cap is MAX_ROUNDS plus any operator-granted
    # extend-once escalations (ledger-derived — no state key). Hitting it
    # now points at the in-prusik escalation gate, not an out-of-prusik STOP.
    effective_cap = MAX_ROUNDS + _count_escalations(feature, "extend-once")
    if prior >= effective_cap:
        print(f"[fix-round] Feature '{feature}' has used {prior} fix round(s) "
              f"(cap: {effective_cap}). This is no longer a dead-end — record "
              f"an in-prusik decision instead of leaving the system:")
        print(f"            prusik gate fix-round escalate --feature {feature} "
              f"--decision <extend-once|integrate-with-flag|abandon> "
              f"--rationale \"...\"")
        ledger.append("fix_round_cap_hit", feature=feature, prior_rounds=prior,
                       effective_cap=effective_cap)
        return 2
    round_n = prior + 1
    _marker_path(root).parent.mkdir(parents=True, exist_ok=True)
    fan_out = _load_fan_out_files(feature, root)
    state = {
        "feature": feature,
        "round": round_n,
        "started_at": datetime.now(timezone.utc).isoformat(),
        # FAN-OUT LANE (fb-7ab319116f42): the reverse-dep files the blast-radius
        # gate PREDICTED (system-computed from the dep-graph) become writable in project
        # root this round, so a field-adding sprint can fix its predicted fan-out
        # in-flow instead of dead-ending into an integrate-with-flag override. Bounded
        # EXACTLY to the prediction — never an agent-named set.
        "fan_out_files": fan_out,
    }
    _marker_path(root).write_text(json.dumps(state, indent=2))
    ledger.append("fix_round_start", feature=feature, round=round_n,
                  fan_out_count=len(fan_out))
    print(f"[fix-round] Started round {round_n}/{MAX_ROUNDS} for feature '{feature}'.")
    if fan_out:
        print(f"[fix-round] fan-out lane: {len(fan_out)} blast-radius-PREDICTED "
              f"reverse-dep file(s) writable in project root this round (the gate "
              f"named these pre-build) — fix them in place, no override needed:")
        for f in fan_out[:12]:
            print(f"             + {f}")
        if len(fan_out) > 12:
            print(f"             … and {len(fan_out) - 12} more (see blast-prediction)")
    print("            Writable expanded to include worktrees/*/** while active.")
    print(f"            Run `prusik gate fix-round end --feature {feature}` when fix is applied.")

    # Thrash signal (fb-983dac02ac8d): multiple fix-rounds without a reviewer
    # PASS usually means a STRUCTURAL / prusik-mechanics blocker, not a product defect —
    # surface it HERE, as another round is about to be spent, so the operator can
    # intervene before the token burn (the fastest fix for a prusik blocker is escalation).
    from prusik import convergence
    advisory = convergence.thrash_advisory(ledger.read_all(), feature)
    if advisory:
        print(advisory)

    # v0.8.2 (B26): cross-check reviewer artifacts before opening the
    # fix-round, so operator sees a fabrication warning BEFORE spending
    # builder dispatches on a fix that doesn't exist.
    from prusik import consistency
    suspects = consistency.detect_reviewer_fabrication(root, feature)
    if suspects:
        consistency.emit_fabrication_warnings(suspects)
    return 0


def status(root: Path | None = None) -> int:
    """Print whether a fix-round is active. Used by reviewer agents to
    decide narrow-vs-full review mode (v0.8.7).

    Output:
      - active fix-round: 'feature=<slug> round=<n> started=<iso>'  (rc 0)
      - no active fix-round: '(no active fix-round)'  (rc 0)

    rc is always 0 — informational; not blocking. The PRESENCE of the
    descriptive line + parseable feature/round fields is the contract.
    """
    state = current_state(root)
    if state is None:
        print("(no active fix-round)")
        return 0
    print(f"feature={state.get('feature', '?')} "
          f"round={state.get('round', '?')} "
          f"started={state.get('started_at', '?')}")
    return 0


def end(feature: str, root: Path | None = None) -> int:
    """End the active fix round. Removes the marker; logs duration.

    Exit codes:
      0 — ended
      1 — no active round / feature mismatch
    """
    root = root or ledger.project_root()
    state = current_state(root)
    if not state:
        print("[fix-round] No active fix round to end.")
        return 1
    if state.get("feature") != feature:
        print(f"[fix-round] Active round is for '{state.get('feature')}', "
              f"not '{feature}'. Refusing to end.")
        return 1
    round_n = state.get("round")
    duration_sec = None
    try:
        started = datetime.fromisoformat(state["started_at"])
        duration_sec = int((datetime.now(started.tzinfo) - started).total_seconds())
    except (KeyError, ValueError):
        pass
    _marker_path(root).unlink()
    ledger.append("fix_round_end", feature=feature, round=round_n,
                  duration_sec=duration_sec)
    # Re-stage partial-mirror worktrees → root so the reviewer's re-run tests the
    # FIXED code, not the stale pre-fix-round assembly (fb-db53b5d5d380, the
    # unbreakable reviewing loop). Stages every worktree file that DIFFERS from root —
    # so a clean deliverable whose root copy is stale/dirty (not changed DURING this
    # round) finally syncs (fb-ba9d617d55cb) — minus drop-at-integration stubs, which
    # never clobber a canonical root file (fb-bfc8ffdf0fd9). No-op for TS
    # full-worktrees (tested in place).
    from prusik import consistency
    staged = consistency.assemble_worktrees_to_root(root)
    if staged:
        ledger.append("worktrees_assembled", feature=feature, files=len(staged),
                      at="fix_round_end")
        print(f"[fix-round] re-assembled {len(staged)} worktree file(s) (differ from "
              f"root) → project root — the reviewer now re-tests the FIXED code, not "
              f"stale root.")
    print(f"[fix-round] Ended round {round_n} for feature '{feature}' "
          f"({duration_sec}s).")
    return 0
