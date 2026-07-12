# Inline-client legal-name persistence

## Type
new_feature

## Goal
Wire the inline-client form's legal-name input to the server-side
handler so submitting the form persists the value through to the
client record.

## Success criteria
- Submitting the form with a legal-name value persists that value
- No silent default substitution for the legal-name field
- Existing client flows continue to work

## Priority
P1
