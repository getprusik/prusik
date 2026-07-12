# Refactor: <what's the structural change?>

## Type
refactor

## Goal
<What's the structural improvement, and what does it enable? Refactors
must preserve observable behavior — be explicit that this is the
contract. Name the smell being removed (deep nesting / duplicated logic /
hidden coupling / etc.) and what the post-refactor shape looks like.>

Observable-behavior preservation contract: <yes, nothing changes from
user POV / yes, with intentional documented exceptions: ...>

## Success criteria
- All existing tests in <module> still pass with no modification
- <structural assertion: e.g., "function X is <50 lines">
- <coupling assertion: e.g., "module Y no longer imports Z">
- No new public-API surface

## Priority
<P2 typically — refactors are debt-paydown>

## Notes
<Refactor scope is famously easy to expand. List EXPLICITLY what's NOT
in this sprint. "Refactor X, NOT also Y and Z." scope-critic enforces.>

<!-- Brief authoring guidance for refactor:
  - FULL LANE — trivial lane REJECTS refactors (design blast radius).
  - Behavior preservation is the load-bearing claim. F evidence will gate
    against existing test results: if any existing test changes behavior,
    that's a refactor escaping its contract.
  - Refactors that touch >5 files or cross modules should split into multiple
    sprints, not bundle. scope-critic flags this. -->
