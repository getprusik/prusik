# Doc: <what gets documented?>

## Type
doc

## Goal
<What's the gap in documentation, who reads it, and what should they be
able to do after reading? "Document X" is too vague — name the audience
(operator / new adopter / prusik author / external user) and the action.>

## Success criteria
- The document exists at <path>
- Read by <audience>, they can <specific action>
- No code/binary changes (doc-only sprint)
- <if applicable: word-count or section-count target>

## Priority
<P2 / P3 typically>

## Notes
<Reference the trigger (operator question / unclear support thread /
sprint retro item) so the doc actually addresses real friction, not
speculative completeness.>

<!-- Brief authoring guidance for doc:
  - TRIVIAL-LANE ELIGIBLE.
  - conventions-enforcer checks doc word-count budget if you declare one.
  - regression-sentinel will check no test-suite regression even on doc-only
    sprints — but with no code change, it should be a no-op PASS via F's
    carry-forward. Don't skip the gate. -->
