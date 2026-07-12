# Inline-client search via JSX fetch (Express backend)

## Type
new_feature

## Goal
Add a search field to the inline-client form (JSX) that fetches
matching clients server-side as the user types. The Express route
exists on the clients router; the JSX component calls the route via
fetch.

## Success criteria
- JSX fetch URL resolves to the mounted route (no 404)
- Search returns results with matching clients
- No regressions in other inline-client flows

## Priority
P1
