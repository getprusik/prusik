# Case: blast-radius predicted but not verified (adopter retro #1)

**Source:** adopter TS/turbo Stripe-billing sprint, end-of-sprint retrospective.

**The friction.** The sprint's `scope.md` Blast-radius section literally predicted
the regression: *"any route that gains a `requireFeature` guard will reject
free-tier users; regression tests for those routes must be updated."* prusik
**computed** that prediction (`plan_test_reach` → `at_risk_tests`) and emitted it
as a plan-time advisory — then nothing consumed it. The builder added the guard,
did **not** update the route's tests, and the break surfaced as 28 red tests at
reviewing. *The harness made the prediction and ignored it.*

**The principle.** A prediction prusik computes should become a **gate**, not
ignored prose: at reviewing, verify the predicted-regressing tests were actually
touched. (the adopter's framing: "the map already knows the edges — make them
actionable.")

## Reproduction (replayed by `tests/test_blast_verify.py`)

```
src/billing.py          router @router.get("/checkout")          ← route contract
tests/test_checkout.py  references "/billing/checkout"           ← OUTSIDE module set → at-risk
design/<feat>/plan.md   ## Modules touched: src/billing.py
worktrees/solo/src/billing.py   builder added a guard            ← src touched
                                (tests/test_checkout.py NOT touched)
```

- `plan-reach` / `record_prediction` → `at_risk_tests = ["tests/test_checkout.py"]`,
  persisted to `.sprint/blast-prediction.<feat>.json`.
- `blast-verify` at reviewing → `unverified = ["tests/test_checkout.py"]` — the
  predicted-regressing test was never updated. Surfaced by name (advisory;
  `--strict` / `require_blast_radius_verified` makes it a hard gate).
- Positive control: touch `tests/test_checkout.py` in the worktree → `unverified = []`.

## Why it's in the moat-loop

This case is the first adopter friction run through the formalized loop: the
report became a reproducing case **and** a permanent prusik regression test, so
the harness cannot re-break what adopter taught it. "Unable to be wrong twice."

**Shipped:** v0.96.0 — `blast_plan.record_prediction` / `verify_prediction` /
`verification_advisory`, `prusik blast-verify`, `consistency.sprint_changed_files`.
