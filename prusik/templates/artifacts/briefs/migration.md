# Migration: <from-X to-Y>

## Type
migration

## Goal
<What's being migrated (data shape / API version / dependency / database
schema)? Why now? What's the cutover plan (atomic, multi-step,
backward-compatible-window)?>

Backward compatibility window: <none / 1 release / until X is removed>

## Success criteria
- New shape is reachable + functions correctly
- Old shape continues working through the deprecation window (if any)
- Migration is idempotent (can re-run without harm)
- A rollback path exists and is documented in design/<feature>/scope.md
- All call sites updated (or explicitly deprecated)

## Priority
<P0 / P1 — migrations are coordination-heavy>

## Notes
<Affected systems / downstream callers / coordination needed. Reference
the schema diff or API change spec. Note any data backfill required.>

<!-- Brief authoring guidance for migration:
  - FULL LANE — trivial lane REJECTS migrations (real blast radius).
  - Database/schema migrations: name the migration tool (alembic, prisma, etc.)
    and reference the migration file the sprint will produce.
  - Coordination: migrations often need deploy-order discipline (deploy old
    code + new code that handles both shapes, run migration, deploy new code
    that requires new shape). State the order in Notes if relevant.
  - Idempotency is a real requirement, not aspirational — write the regression
    test that re-runs the migration twice and asserts the same final state. -->
