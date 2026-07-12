"""Authoring-variant classifier — the ONE extensible surface answering "do these
two authored tokens denote the SAME thing, differing only by a benign authoring
variant?" for the planning artifacts (brief / scope / plan / criteria).

WHY THIS EXISTS. The second-largest recurring adopter-finding cluster was all one
shape: a brief/scope/plan validator FALSE-FLAGGED legitimately-authored content
because the agent wrote a token in a natural variant the validator didn't treat as
equivalent to its canonical form — a diff-style `+ new/file` read as a missing
file (scope #8), a markdown-wrapped `` `path` `` / `**path**` captured with its
backticks, a Title-Case prose reference `Submission profile` flagged as a near-miss
typo for the kebab sentinel `submission-profile` (fb-a1753e4a729d), trailing
punctuation on a path. Each was patched as a NEW inline branch in a DIFFERENT place
— `schema.extract_path_token`, `schema.extract_module_token`, `brief_lint._ident_norm`
— so the same knowledge ("strip this benign variant before comparing") lived in
several copies that DRIFTED: a variant taught to the path parser wasn't known to the
identifier near-miss, and vice-versa. That recurrence — and that drift — is what this
module is built to stop.

It makes the benign authoring variants ONE inspectable, registered list. Every
comparison surface (scope/plan path existence + subset, brief near-miss) routes
through it, so a variant learned once is known everywhere. `variant_of()` returns
WHICH variant(s) bridged the gap (for a remedy + measurability) or None when the
tokens genuinely differ — so a real typo / a real missing file still flags. A
suppressed false-flag is recorded to the ledger (`artifact_benign_variant`, with
the stable variant name) so recurrence is MEASURABLE per project — fuel for the
cross-run calibration loop rather than another silent special-case.

THE MAINTENANCE CONTRACT — registering a new benign variant is now bounded:
  1. write a normalizer `_my_variant(token: str) -> str` (idempotent; strips only
     its own benign form)
  2. wrap it in a `Variant(name, normalizer, kinds)` and append to `_VARIANTS`
  3. add its name to `KNOWN_VARIANTS` (the completeness test pins name↔variant parity)
  4. add a unit test (the variant is benign; a genuine difference still differs)
No new scattered branch in a parser; no second divergent copy of the knowledge.

TOKEN KINDS scope a variant to where it is SAFE. `PATH` tokens keep their internal
structure (a path comparison must stay case- and separator-sensitive: `api/Billing`
≠ `api/billing` may be a real case bug), so they get only marker/wrapper/punctuation
variants. `IDENTIFIER` tokens (sentinel / subsection references) additionally fold
case and separators, the prose-vs-kebab equivalence near-miss needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# Token kinds (a variant declares which it is safe for).
PATH = "path"              # a file/module path — structure-preserving comparison
IDENTIFIER = "identifier"  # a sentinel / subsection name — case+separator-insensitive

# Markdown wrappers authors put around inline tokens, and trailing punctuation a
# token picks up in prose. Single home for these char sets (was duplicated in
# schema.extract_path_token).
_MARKDOWN_WRAPPERS = "`*_~"
_TRAILING_PUNCT = ",.):;"


@dataclass(frozen=True)
class Variant:
    """One registered benign authoring-variant: a normalizer that strips its own
    form, plus the token kinds it is SAFE to apply to."""
    name: str
    normalize: Callable[[str], str]
    kinds: frozenset[str]


@dataclass(frozen=True)
class BenignVariant:
    """The verdict that an authored token is a benign variant of a canonical one."""
    variants: tuple[str, ...]   # which registered variants bridged the gap (≥1)
    canonical: str              # the canonical token it actually denotes
    reason: str                 # human one-liner (diagnosis + why it's benign)


# ── the registered variants (normalizers), in application order ───────────────

def strip_new_file_marker(token: str) -> str:
    # A diff-style `+ path` (or `+path`) declares a NEW file this sprint; for IDENTITY
    # it denotes the same path as `path` (scope #8). Strip a single leading `+`.
    t = token.lstrip()
    if t.startswith("+"):
        return t[1:].lstrip()
    return token


def strip_markdown_wrappers(token: str) -> str:
    # `` `path` `` / `*path*` / `**path**` / `_path_` / `~path~` — strip wrapper
    # chars from both ends (the token is the same underneath).
    return token.strip(_MARKDOWN_WRAPPERS)


def strip_trailing_punct(token: str) -> str:
    # `path,` / `path).` in prose — trailing punctuation is not part of the token.
    return token.rstrip(_TRAILING_PUNCT)


def fold_case_separators(token: str) -> str:
    # Collapse the differences between a PROSE reference and its kebab/snake
    # SENTINEL form — case, and space-vs-hyphen-vs-underscore — so
    # 'Submission profile' and 'submission-profile' denote the same identifier
    # (fb-a1753e4a729d). IDENTIFIER-only: never applied to paths.
    return re.sub(r"[-_\s]+", "", token).lower()


_VARIANTS: tuple[Variant, ...] = (
    Variant("diff_new_file_marker", strip_new_file_marker,
            frozenset({PATH, IDENTIFIER})),
    Variant("markdown_wrapper", strip_markdown_wrappers,
            frozenset({PATH, IDENTIFIER})),
    Variant("trailing_punct", strip_trailing_punct,
            frozenset({PATH, IDENTIFIER})),
    Variant("case_separator", fold_case_separators,
            frozenset({IDENTIFIER})),
)

# Stable names of every registered variant — the completeness test pins this to
# _VARIANTS so a variant can't be added without a name (observability + contract),
# nor a name orphaned. Mirrors capture_diagnose.KNOWN_MODES.
KNOWN_VARIANTS: tuple[str, ...] = (
    "diff_new_file_marker",
    "markdown_wrapper",
    "trailing_punct",
    "case_separator",
)

_REASON = {
    "diff_new_file_marker": "a diff-style `+ ` marks a new-this-sprint file; it "
    "denotes the same path (write it as `- + path` to also declare intent)",
    "markdown_wrapper": "markdown wrappers (`` ` ``/`*`/`_`/`~`) around the token "
    "are formatting, not part of the name",
    "trailing_punct": "trailing prose punctuation is not part of the token",
    "case_separator": "differs only by case / space-vs-hyphen-vs-underscore — a "
    "prose reference to a kebab/snake sentinel, not a typo",
}


def canonicalize(token: str, kind: str) -> str:
    """Apply every variant normalizer SAFE for `kind`, in registration order, to
    reduce an authored token to its canonical form. The single normalization
    authority — `schema`/`brief_lint` route their stripping through this so the
    knowledge can't drift between the path and identifier comparison surfaces."""
    for v in _VARIANTS:
        if kind in v.kinds:
            token = v.normalize(token)
    return token


def _bridging_variants(token: str, kind: str) -> tuple[str, ...]:
    """Which registered variants actually CHANGED the token on the way to its
    canonical form (the ones that 'mattered'), for an honest remedy + ledger label."""
    applied: list[str] = []
    cur = token
    for v in _VARIANTS:
        if kind not in v.kinds:
            continue
        nxt = v.normalize(cur)
        if nxt != cur:
            applied.append(v.name)
            cur = nxt
    return tuple(applied)


def variant_of(token: str, canonical_set, kind: str = IDENTIFIER
               ) -> BenignVariant | None:
    """If `token` is not literally in `canonical_set` but canonicalizes (under the
    variants safe for `kind`) to the same form as a member, return the BenignVariant
    that bridges them — else None (a genuine difference: a real typo / missing path).

    This is the false-flag suppressor: a comparison surface that is about to flag a
    token as unknown/missing first asks here whether it's only a benign authoring
    variant of something legitimate. Fail-safe by construction: no canonical match →
    None → the flag stands (a defect is never silently waved through)."""
    if token in canonical_set:
        return None   # literal match — no variant needed
    canon_index: dict[str, str] = {}
    for c in canonical_set:
        canon_index.setdefault(canonicalize(c, kind), c)
    ct = canonicalize(token, kind)
    matched = canon_index.get(ct)
    if matched is None or matched == token:
        return None
    bridged = _bridging_variants(token, kind) or ("exact_after_canonicalize",)
    reason = "; ".join(_REASON.get(v, v) for v in bridged
                       if v in _REASON) or "benign authoring variant"
    return BenignVariant(bridged, matched, reason)
