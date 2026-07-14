# Product charter

The WHAT-layer of your product — the source of truth every feature is reconciled
against so a stream of feature builds cohere into a world-class *product*, not
just well-built features. You own this file; prusik never rewrites it. Once it
exists, the product-fit gate is IMPERATIVE: every new feature's brief must ship a
`design/<feature>/product-fit.md` that reconciles against the pillars and
glossary below, and prusik verifies those references resolve.

## North-star

<!-- One or two sentences: the single outcome this product exists to deliver.
     e.g. "The fastest, most trustworthy way for <our users> to <the core job>,
     with zero fabricated results." -->

## Pillars

<!-- The coherence axes. Every feature must advance at least one. Give each an id
     (P1, P2, …) and a short name. Keep it to 3–6 — pillars are load-bearing, not
     a wishlist. -->
- P1 <pillar name>
- P2 <pillar name>
- P3 <pillar name>

## Glossary

<!-- The canonical domain terms — one definition each. This is what stops the
     "42 definitions of customer": a feature that touches a term either reuses it
     [canonical] or explicitly registers a new one. Add terms as the product
     grows; keep each one singular and unambiguous.

     Declare near-synonyms with `(aka: …)` — the omission linter then flags any
     brief that writes the alias instead of the canonical term. This is the
     highest-leverage drift catch: it stops "client"/"persona" creeping in when
     the product says "customer". -->
- <term>: <one-line definition> (aka: <alias>, <alias>)
- <term>: <one-line definition>
