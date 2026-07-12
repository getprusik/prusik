# case-003 — cross-touch-set test reach

**Surfaced in**: adopter m4-suspect-skip-audit (3+ instances observed,
recurrence-trigger 2 crossed → mechanized in v0.20.0 as
`prusik gate check-test-reach`).

**Defect class**: a route/handler/contract inside the touched set is
referenced by a test OUTSIDE the touched set. Cross-touch-set partial
mirror (v0.7.0/B17) intentionally limits reviewer scope to touched
files — so a regression-risking test in an unmodified test file is
invisible to reviewer-phase checks until the post-integration
full-suite gate fires. The new check flags this at reviewer-phase as
a heads-up (flag-only, not block).

**Prusik check that catches it**: `prusik gate check-test-reach` (v0.20.0).
Scans tests outside `--touched-set` for symbol references to
contracts inside touched-set; emits `reviewer_test_set_reach` ledger
event.

**Trial reference**: m4-suspect-skip-audit had touched route
`/audit/skips` referenced by `tests/test_legacy_audit.py` which sat
outside touched-set — partial-mirror review missed it; full-suite
post-integration gate caught it (slow path).
