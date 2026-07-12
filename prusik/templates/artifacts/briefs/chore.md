# Chore: <what housekeeping?>

## Type
chore

## Goal
<What maintenance task — dependency bump / file reorganization / log
cleanup / unused-import removal / etc.? Chores are bounded
behavior-preserving changes that don't fit other Types. If observable
behavior changes, this is actually a refactor (or worse, a defect).>

Behavior-preservation: yes, no user-visible change

## Success criteria
- <housekeeping outcome: e.g., "dependency X bumped to Y">
- All existing tests pass unchanged
- No new code paths
- <if applicable: lint/typecheck still clean>

## Priority
<P3 typically>

## Notes
<Why now? Chores done without a trigger are easy to over-scope. Trigger
examples: security advisory on dep version / unused-import cleanup before
deeper refactor / aligning files with newly-established convention.>

<!-- Brief authoring guidance for chore:
  - TRIVIAL-LANE ELIGIBLE.
  - If the chore is "bump dependency X," prusik's behavior_regression suite
    is your friend — it'll catch any subtle behavior change from the dep
    update that unit tests might miss.
  - Chores that "while I was in there..." touch unrelated files are scope
    creep. scope-critic flags. Keep narrow. -->
