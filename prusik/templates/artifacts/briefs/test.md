# Test: <what coverage gap is being closed?>

## Type
test

## Goal
<What's the coverage gap, and what failure mode would this test catch?
Tests authored without a named failure mode become assertion-noise.
Name the bug class (regression in module X / boundary case Y / integration
seam Z) the test guards against.>

## Success criteria
- Test file at <path> exists
- Test reliably FAILS on a contrived defect that matches the named
  failure mode (proves the test would catch it)
- Test PASSES on current code
- Test executes in <bound> seconds (no flaky-by-slow tests)
- Test is independent (no shared mutable state with siblings)

## Priority
<P1 / P2>

## Notes
<Reference the bug class motivation. If this test is being added because
a recent incident slipped through reviewing (post-integration-only catch),
note the trace/incident — this is prusik's recurrence-trigger working
forward.>

<!-- Brief authoring guidance for test:
  - TRIVIAL-LANE ELIGIBLE.
  - The "test reliably fails on contrived defect" criterion is the F-thesis
    applied: don't ship a test that's never been seen to fail. The capture-
    wrapper's executed primitive counts this test as +1 only when it ran.
  - If the test exists to catch a class of defects (not just one specific
    bug), name the class. "Catches all uses of `==` for password comparison"
    is sharper than "tests login security." -->
