# Prusik eval suite

Empirical benchmark of prusik's catches against **observed defect
classes** from the trial. Each case is grounded in a real bug the
trial surfaced — not synthetic-by-imagination. The corpus grows when
new recurrence-trigger-eligible defect classes are observed in field
runs.

## What this is (and what it isn't)

This is prusik's **own check-mechanism benchmark**: "given a known
defect class, does prusik's gating + flagging actually catch it?"
Reproducible, deterministic, runs in CI in seconds, no LLM cost. The
honest claim it substantiates: *prusik's checks fire on the defect
classes the trial observed*.

This is NOT a full agent-vs-control benchmark (yet). The "kit-gated
vs vibe-coding control" comparison requires real LLM agent runs or
recorded traces; that's queued for a later pass (v0.22+). What ships
in v0.21.0 establishes the corpus + the harness + the reproducible
metrics, so the agent-control layer can land later without re-doing
the foundation.

## Running

```bash
prusik eval list                       # list corpus cases
prusik eval run                        # run all cases; report per-case + aggregate
prusik eval run --case case-001 ...    # run a specific case
prusik eval run --json                 # machine-readable output for CI
```

Each case produces a hit (prusik caught the defect) or miss (prusik didn't).
Aggregate metrics: hit rate per defect class, false-positive rate on
clean-code controls (each case ships a `clean/` variant proving the
check doesn't false-fire), regressions per prusik version.

## Adding a case

A new corpus case is justified when:
1. A real defect class is observed in a field run (not invented).
2. The class crosses prusik's recurrence-trigger threshold OR is
   strategically important to substantiate (e.g. the first instance
   of a §4 boundary prusik just mechanized).
3. The case is reproducible deterministically — no real-LLM
   dependency.

Structure:
```
cases/case-NNN-short-name/
  README.md                         # what defect class, what trial sprint surfaced it
  brief.md                          # operator-style brief for the synthetic sprint
  initial-repo/                     # minimal repo with the bug PRESENT
    ...
  clean/                            # same repo but with the bug FIXED (FP control)
    ...
  expected-outcomes.yaml            # which checks should fire on initial; should NOT on clean
```

## Honest bounds (stated, not hidden)

- The corpus is small (3 cases on first pass). Expanding to ~20-50 is
  the next increment as more defect classes surface.
- The agent-control branch is queued. Without it, the eval shows
  "prusik catches what prusik claims to" but doesn't yet show "prusik catches
  what vibe-coding misses." Both matter; first ship the former.
- Reproducibility depends on prusik's check determinism. The checks
  ARE deterministic (regex + grep + file inspection); the LLM-agent
  layer is what introduces non-determinism, which is precisely why
  the agent layer is deferred.
