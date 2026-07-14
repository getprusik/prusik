---
description: Interactive wizard that authors a new brief from five prompts.
argument-hint: <feature-slug>
---

You're going to help the user author `briefs/$ARGUMENTS.md`. The brief has five fields (three required, two optional). Ask them one at a time, validate as you go, and then write the file.

**Steps:**

1. Ask for **Goal** (one sentence, 5–80 words). Make sure it's an outcome, not a solution. If they write a solution, gently push for the underlying goal.

2. Ask for **Success criteria**. This MUST be measurable — a threshold ("within Xs", "at least X%", "<X"), an exit code ("exits 0"), or a count ("0 new failures", "100/100 runs pass"). If their answer isn't measurable, explain and ask again.

3. Ask for **Type** — show them: `bug_fix | new_feature | refactor | migration | doc | config`. Accept only one of these.

4. Ask for **Priority** (optional; default P2) — `P0 | P1 | P2`.

5. Ask for **Notes** (optional) — anything prusik can't know: constraints, prior context, hint at links.

**Then:**
- Write `briefs/$ARGUMENTS.md` using the artifact template at `.claude/artifact-templates/brief.md` (copy that structure; don't invent).
- Run `prusik gate brief briefs/$ARGUMENTS.md` via Bash. If validation fails, show the errors and ask the user to fix.

**Product-fit (holistic context) — if `design/product.md` exists:**
This project has declared a product, so a feature isn't done being briefed until it's reconciled with the *whole* product. Together with the user:
- Read `design/product.md` (north-star, pillars, canonical glossary).
- Co-author `design/$ARGUMENTS/product-fit.md` from `.claude/artifact-templates/product-fit.md`:
  - `## Advances` — which real pillar(s) this feature serves.
  - `## Related` — which existing `briefs/*.md` features it reconciles with (extends/depends/overlaps), or `none`.
  - `## Concepts` — domain terms touched, `[canonical]` (already in the glossary) or `[new: <definition>]` (a genuinely new term — never a second name for an existing concept).
- Run `prusik gate product-fit $ARGUMENTS`. Fix anything that doesn't resolve — a claim that doesn't check out (a pillar/feature/term that doesn't exist) blocks the sprint by design. This is prusik ensuring the brief was built with holistic context at hand, with evidence.
- If instead you see "gate is DORMANT", the project hasn't declared a product yet. Offer to seed one: `prusik gate product-fit $ARGUMENTS --bootstrap`, then help the user ratify `design/product.md`.

- Once the brief (and product-fit, if applicable) validate, tell them: "Run `/sprint-start $ARGUMENTS` when ready."

**Do not:**
- Ask them to list modules, domains, size, or links. The scoping role will derive those. If they volunteer such info, store it in Notes.
- Proceed to start the sprint. This command only authors the brief.
