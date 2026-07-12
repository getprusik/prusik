# Fix: <one-line symptom — what's broken from the user's POV?>

## Type
bug_fix

## Goal
<One paragraph. What's the observed defect, and what's the correct
behavior? Cite a trace, a failing test, an issue ID, or a reproducible
sequence. If you can't name the trigger, the bug isn't characterized
yet — investigate before authoring.>

Reproduction:
1. <step that produces the bug>
2. <step>
3. Observed: <wrong behavior>
4. Expected: <right behavior>

## Success criteria
- A regression test exists that FAILS on current code and PASSES after
  the fix (prusik's executed-not-collected primitive will gate this)
- <user-visible behavior assertion after fix>
- No regressions in <touched module>

## Priority
<P0 / P1 / P2>

## Notes
<Trace ID / failing test path / issue link. If the bug surfaced from a
production trace and you have it on disk, reference the trace path so
v0.21's trace→test loop (when it ships) can pick it up. Note known-good
prior version if relevant.>

<!-- Brief authoring guidance for bug_fix:
  - This is TRIVIAL-LANE ELIGIBLE. Use `prusik gate sprint-start --trivial <f>`
    if the fix is bounded to one module + has a clear regression test target.
    If the fix is cross-module or unclear, drop to full lane.
  - Success criterion #1 (regression test fails first, passes after) is what
    F's `prusik gate capture --kind tests` will gate against. Don't write the
    fix and the test in the same commit without the failing-first proof.
  - If the bug was caught by the post-integration gate (not reviewing), v0.12.0
    §4's test-set-reach boundary applies — the existing test set didn't reach
    this contract. Worth noting in Notes. -->
