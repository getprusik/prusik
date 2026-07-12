# Inline-client search via template fetch

## Type
new_feature

## Goal
Add a search field to the inline-client form that fetches matching
clients server-side as the user types. The route exists on the
invoices router and renders matching clients into the template's
results panel; the template calls the route via fetch.

## Success criteria
- Template fetch URL resolves to the registered route (no 404)
- Search returns results panel with matching clients
- No regressions in other invoice flows

## Priority
P1

## Notes
The route lives on the invoices router; the template lives under
templates/inline_client/.
