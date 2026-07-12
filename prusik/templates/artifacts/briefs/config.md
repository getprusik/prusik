# Config: <what configuration change?>

## Type
config

## Goal
<What configuration is changing, why, and what's the resulting behavior?
Configuration changes can have outsized blast radius — be explicit about
what triggers the change to take effect (immediate / on restart / on
next request).>

## Success criteria
- Config file at <path> reflects the new values
- Behavior under the new config: <observable change>
- Behavior under the old config (if rollback): unchanged
- No regression in non-config-touching paths

## Priority
<P1 / P2>

## Notes
<Reference related infrastructure (CI vars / secrets / environment
overrides) without leaking secret values. Note rollout coordination if
the config is shared across services.>

<!-- Brief authoring guidance for config:
  - TRIVIAL-LANE ELIGIBLE.
  - Config changes that REPLACE a value vs ADD a key have different blast
    radius — favor ADD over REPLACE where possible (additive, reversible).
  - regression-sentinel runs the test suite; if your test suite reads the
    config, it'll exercise the new config naturally. If not, add at least
    one test that asserts the config has the expected value. -->
