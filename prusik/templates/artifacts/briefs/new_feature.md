# <Brief title — what's the user-visible change?>

## Type
new_feature

## Goal
<One paragraph. What does the user see / experience / can do after this
ships that they could not before? Be specific about the user-visible
behavior, not the implementation. If you cannot describe this without
naming a specific file, the scope is too narrow — refine first.>

## Success criteria
<Each criterion testable against built code. Format prusik expects:>
- <visible-behavior assertion 1 — phrasing the success-criteria verify
  script should be able to check>
- <visible-behavior assertion 2>
- <visible-behavior assertion 3 — typically 3–7 criteria for a feature>

## Priority
<P0 / P1 / P2>

## Notes
<Stack-specific reminders, integration points, gotchas. Reference any
related briefs (`see also: briefs/<other>.md`), prior decisions
(`per decisions/<file>.md`), or design docs. Note any out-of-scope items
explicitly — "NOT in this sprint: X" — so scope-critic can confirm.>

<!-- Brief authoring guidance for new_feature:
  - This is FULL LANE (scope → triage → planning → build → review → integrate).
  - Trivial lane is REJECTED for new_feature by the brief-Type guard — design
    blast radius requires scope + plan critic review.
  - The success criteria are what F (Candidate F) evidence will gate against.
    Write them as testable assertions, not aspirations.
  - If a sibling briefs/<feature>.criteria.yaml exists with `verify_command`
    entries, those are the mechanical version of these criteria. Prusik's
    sprint-complete gate runs them.
  - Notes is the place for context that doesn't fit Goal/Criteria. Keep brief
    short overall; supporting design belongs in design/<feature>/scope.md
    which scope-critic produces. -->
