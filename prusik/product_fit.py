"""Holistic product-fit gate — the WHAT-layer that keeps a stream of feature
builds cohering into a world-class PRODUCT, not just well-built features.

Prusik enforces feature-level rigor everywhere; this closes the gap above it.
A brief is a live-cc + operator collaboration; when one is authored, prusik
CALLS FOR an evidence-backed acknowledgement that the feature fits the whole
product — and accepts it only when its references RESOLVE against real repo
state. A bare "yes, we considered the product" cannot pass: there is nothing
gameable to write. That is what makes the gate *truly* enable rather than
rubber-stamp.

Layered anchor (all three, progressive — not either/or):
  1. Derived floor  — prior briefs + map + decisions (always available; used
     inside the checks below).
  2. Product charter (design/product.md) — human-owned north-star + pillars +
     canonical glossary. The vision layer: the only thing encoding what the
     product SHOULD be. OPTIONAL — if absent the gate is DORMANT so un-adopted
     projects aren't blocked; once present it is IMPERATIVE (every feature must
     acknowledge fit or sprint-start fails closed).
  3. Bootstrap (`--bootstrap`) — drafts a charter scaffold seeded with the
     existing feature list so the operator never faces a blank page; they
     ratify it (human-owned truth, not agent drift).

The acknowledgement design/<feature>/product-fit.md has three evidence-backed
sections, each verified to resolve:
  ## Advances — pillar(s) served; each must name a real charter pillar.
  ## Related  — prior features reconciled with; each cited brief must exist.
  ## Concepts — domain terms touched; [canonical] must be in the glossary,
                [new: <definition>] registers a genuinely-new term (blocks
                silent concept duplication — the "42 definitions of customer").
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from prusik import ledger, schema


def charter_path(root: Path) -> Path:
    return root / "design" / "product.md"


def fit_path(root: Path, feature: str) -> Path:
    return root / "design" / feature / "product-fit.md"


def _bullets(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("-", "*")):
            body = s[1:].strip()
            if body:
                out.append(body)
    return out


def _parse_pillars(text: str) -> list[dict]:
    """Each bullet → {"id": <e.g. P1 or "">, "name": <rest>}. Both an id token
    (P1/G2/…) and a free-text name are accepted; either can be cited in
    ## Advances."""
    pillars = []
    for b in _bullets(text):
        m = re.match(r"([A-Za-z]{1,4}\d+)[:.)]?\s+(.*)", b)
        if m:
            pillars.append({"id": m.group(1), "name": m.group(2).strip()})
        else:
            pillars.append({"id": "", "name": b})
    return pillars


def _parse_glossary(text: str) -> tuple[dict[str, str], dict[str, str]]:
    """Parse the glossary. Each bullet is `term: definition`, and the definition
    may declare near-synonyms with `(aka: a, b, c)` — the alias words that should
    be written as the canonical term instead.

    Returns (glossary, aliases):
      glossary = {term_lower: definition}
      aliases  = {alias_lower: canonical_term_lower}   — for the omission linter
    """
    glossary: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for b in _bullets(text):
        if ":" not in b:
            glossary[b.strip().lower()] = ""
            continue
        term, defn = b.split(":", 1)
        term_l = term.strip().lower()
        defn = defn.strip()
        m = re.search(r"\(aka:\s*(.*?)\)", defn, re.IGNORECASE)
        if m:
            for a in m.group(1).split(","):
                a = a.strip().lower()
                if a:
                    aliases[a] = term_l
            defn = (defn[:m.start()] + defn[m.end():]).strip()
        glossary[term_l] = defn
    return glossary, aliases


def load_charter(root: Path | None = None) -> dict | None:
    """Parse design/product.md → {north_star, pillars, glossary}, or None if the
    project has not declared a product (gate stays dormant)."""
    root = root or ledger.project_root()
    p = charter_path(root)
    if not p.exists():
        return None
    sections = schema.parse_sections(p.read_text())
    glossary, aliases = _parse_glossary(sections.get("## Glossary", ""))
    return {
        "north_star": sections.get("## North-star", "").strip(),
        "pillars": _parse_pillars(sections.get("## Pillars", "")),
        "glossary": glossary,
        "aliases": aliases,
    }


def lint_glossary(feature: str, root: Path, charter: dict) -> list[tuple[str, str]]:
    """The omission linter — the sin `[canonical]` tags can't catch. Scans the
    brief TEXT for whole-word occurrences of any charter alias (a near-synonym the
    operator declared with `(aka: …)`) that should have been the canonical term.
    Returns [(alias, canonical), …]. Deterministic and operator-owned: only the
    aliases the operator chose to enforce are flagged, so false positives stay low.
    """
    aliases = charter.get("aliases") or {}
    if not aliases:
        return []
    brief = root / "briefs" / f"{feature}.md"
    if not brief.exists():
        return []
    text = brief.read_text()
    found: list[tuple[str, str]] = []
    for alias, canonical in sorted(aliases.items()):
        if re.search(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE):
            found.append((alias, canonical))
    return found


def charter_staleness(root: Path | None = None) -> int | None:
    """How many sprints have COMPLETED since the charter was last edited — a
    freshness smell (a WHAT-layer authored once ossifies; its pillars drift from
    what's actually shipping). None if there's no charter. Compares the charter
    file's mtime against sprint_complete ledger timestamps: needs no new state and
    errs toward 'fresh' (a checkout that resets mtimes won't nag spuriously)."""
    root = root or ledger.project_root()
    p = charter_path(root)
    if not p.exists():
        return None
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    lf = root / ".sprint" / "ledger.jsonl"
    if not lf.exists():
        return 0
    from datetime import datetime
    n = 0
    for line in lf.read_text().splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") != "sprint_complete":
            continue
        ts = e.get("ts")
        try:
            if ts and datetime.fromisoformat(ts).timestamp() > mtime:
                n += 1
        except (ValueError, TypeError):
            continue
    return n


def freshness_warning(root: Path | None = None,
                      max_sprints_stale: int = 8) -> str | None:
    """The advisory message when the charter has ossified, else None. Advisory by
    design — a stale vision is a smell, not a defect, so this never blocks."""
    stale = charter_staleness(root)
    if stale is None or stale < max_sprints_stale:
        return None
    return (f"charter freshness: design/product.md has not changed in {stale} "
            f"completed sprint(s) (≥ {max_sprints_stale}). Revisit the "
            f"north-star / pillars / glossary so product-fit checks a living "
            f"vision, not a fiction.")


def _parse_concept(entry: str) -> tuple[str, str, str]:
    """`customer [canonical]` → (customer, canonical, "");
    `workspace [new: a tenant boundary]` → (workspace, new, "a tenant boundary").
    Untagged → (entry, "", "")."""
    m = re.search(r"\[(canonical|new)(?::\s*(.*?))?\]\s*$", entry, re.IGNORECASE)
    if not m:
        return entry.strip(), "", ""
    term = entry[: m.start()].strip()
    return term, m.group(1).lower(), (m.group(2) or "").strip()


def check(feature: str, root: Path | None = None) -> tuple[bool, list[str]]:
    """Evidence-resolution gate. (True, []) when the acknowledgement's references
    all resolve — OR when the gate is dormant (no charter). (False, errors)
    when a charter exists but the acknowledgement is missing or a reference
    fails to resolve."""
    root = root or ledger.project_root()
    charter = load_charter(root)
    if charter is None:
        return True, []  # dormant: project hasn't declared a product

    fit = fit_path(root, feature)
    if not fit.exists():
        return False, [
            f"missing product-fit acknowledgement {fit.relative_to(root)} — the "
            f"brief must reconcile against design/product.md before the sprint "
            f"can start (sections: ## Advances / ## Related / ## Concepts)"
        ]

    sections = schema.parse_sections(fit.read_text())
    errors: list[str] = []

    # ## Advances — every cited pillar must exist in the charter.
    advances = _bullets(sections.get("## Advances", ""))
    if not advances:
        errors.append("## Advances is empty — name at least one product pillar "
                      "this feature advances")
    else:
        ids = {p["id"].lower() for p in charter["pillars"] if p["id"]}
        names = [p["name"].lower() for p in charter["pillars"] if p["name"]]
        for a in advances:
            al = a.lower()
            hit = any(re.search(rf"\b{re.escape(i)}\b", al) for i in ids) or \
                any(n and n in al for n in names)
            if not hit:
                pill = [p["id"] or p["name"] for p in charter["pillars"]]
                errors.append(f"## Advances cites {a!r}, not a pillar in "
                              f"design/product.md (pillars: {pill})")

    # ## Related — every cited prior feature must exist as a brief.
    related = _bullets(sections.get("## Related", ""))
    if not related:
        errors.append("## Related is empty — cite the prior features this "
                      "reconciles with, or state 'none'")
    else:
        for r in related:
            feat = r.split(":", 1)[0].strip()
            if feat.lower() == "none" or feat == feature:
                continue
            if not (root / "briefs" / f"{feat}.md").exists():
                errors.append(f"## Related cites feature {feat!r} but "
                              f"briefs/{feat}.md does not exist")

    # ## Concepts — reconcile domain terms with the canonical glossary.
    glossary = set(charter["glossary"])
    for c in _bullets(sections.get("## Concepts", "")):
        term, tag, defn = _parse_concept(c)
        tl = term.lower()
        if tag == "canonical":
            if tl not in glossary:
                errors.append(f"## Concepts marks {term!r} [canonical] but it is "
                              f"not in the charter glossary — add it to "
                              f"design/product.md ## Glossary, or tag [new: …]")
        elif tag == "new":
            if not defn:
                errors.append(f"## Concepts registers new term {term!r} without a "
                              f"definition — use [new: <definition>]")
            elif tl in glossary:
                errors.append(f"## Concepts registers {term!r} as [new] but it is "
                              f"already canonical — reuse it as [canonical], don't "
                              f"redefine (concept duplication)")
        else:
            errors.append(f"## Concepts entry {c!r} must tag each term "
                          f"[canonical] or [new: <definition>]")

    # Omission linter — the brief must speak the canonical vocabulary, not a
    # declared near-synonym (the definition-drift that's invisible until costly).
    for alias, canonical in lint_glossary(feature, root, charter):
        errors.append(f"brief uses {alias!r} but the charter canonicalizes it as "
                      f"{canonical!r} — use the canonical term, or add {alias!r} to "
                      f"the glossary as its own term if it is genuinely distinct")

    return (not errors), errors


def bootstrap(root: Path | None = None) -> int:
    """Draft design/product.md from the charter template, seeded with the
    existing feature list so the operator ratifies rather than starts blank."""
    root = root or ledger.project_root()
    p = charter_path(root)
    if p.exists():
        print(f"[prusik-product-fit] {p.relative_to(root)} already exists — "
              f"edit it directly (bootstrap won't overwrite).", file=sys.stderr)
        return 0
    tpl = (Path(__file__).parent / "templates" / ".claude"
           / "artifact-templates" / "product.md")
    body = tpl.read_text() if tpl.exists() else "# Product charter\n"
    briefs = sorted(b.stem for b in (root / "briefs").glob("*.md")) \
        if (root / "briefs").exists() else []
    seed = ("\n<!-- bootstrap: existing features to draw pillars/glossary from:\n"
            + "\n".join(f"  - {b}" for b in briefs) + "\n-->\n") if briefs else ""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body + seed)
    print(f"[prusik-product-fit] drafted {p.relative_to(root)} "
          f"({len(briefs)} existing feature(s) listed). Ratify it — fill the "
          f"north-star, pillars, and glossary — then the product-fit gate is live.")
    return 0


def run(feature: str, root: Path | None = None, json_output: bool = False,
        do_bootstrap: bool = False) -> int:
    root = root or ledger.project_root()
    if do_bootstrap:
        return bootstrap(root)

    if load_charter(root) is None:
        msg = ("no design/product.md yet — the product-fit gate is DORMANT. "
               "Seed a charter with `prusik gate product-fit "
               f"{feature} --bootstrap` to make feature→product fit imperative.")
        if json_output:
            print(json.dumps({"feature": feature, "dormant": True, "ok": True,
                              "message": msg}))
        else:
            print(f"[prusik-product-fit] {msg}")
        return 0

    ok, errors = check(feature, root)
    if json_output:
        print(json.dumps({"feature": feature, "dormant": False, "ok": ok,
                          "layer": "reference-resolution (form)", "errors": errors},
                         indent=2))
    elif ok:
        # Honesty: this checks FORM (references resolve), not SUBSTANCE (does the
        # feature truly fit). A form-pass is a floor, not a quality signal — the
        # product-fit-critic judges soundness. Never let this masquerade as "it fits."
        print(f"[prusik-product-fit] ✓ {feature}: references RESOLVE (form only — "
              f"cited pillar/feature/terms exist). Soundness (does it truly fit?) "
              f"is judged by the product-fit-critic, not asserted here.")
    else:
        print(f"[prusik-product-fit] ✗ {feature}: product-fit unresolved:",
              file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
    return 0 if ok else 2
