"""`prusik brief-lint` — catch near-miss references before a sprint starts.

Motivated by the backlog-gap-fill trial where the brief used "Invoice
lifecycle" / "Onboarding" as epic names that almost-but-not-quite matched
the actual "Invoice lifecycle API" / "Workspace configuration" strings in
the code. Scope-critic and the builder caught it downstream, but every
downstream catch costs more than catching it at brief-author time.

Strategy: extract "candidate identifiers" from the brief (quoted phrases,
ID patterns like BL-###, multi-word Capitalized Phrases), cross-reference
against a `known strings` set assembled from the project's dep graph,
existing scope docs, previously-authored briefs, and synced issues. If
a candidate doesn't match verbatim but has a near-match (difflib ratio
≥ 0.80), report it as a near-miss with the closest matches suggested.

Structural validation (schema) still runs first — `prusik gate brief` logic
is reused; near-miss is an additional pass.
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
from pathlib import Path

import yaml

from prusik import artifact_variants, schema
from prusik.ledger import project_root


def _defer_markers(root: Path) -> tuple[str, ...]:
    """The project's `reviewing_defer_markers` (sprint-config), default browser_smoke —
    so the criterion-scope check is driven by each customer's own config, not a
    hardcoded marker."""
    from prusik import phases
    cfg = phases.load_sprint_config(root) or {}
    markers = cfg.get("reviewing_defer_markers")
    if isinstance(markers, list) and markers:
        out = tuple(str(m).strip() for m in markers if str(m).strip())
        if out:
            return out
    return schema.DEFAULT_DEFER_MARKERS


# Patterns for things worth fuzzy-matching against project content.
_QUOTED = re.compile(r'["“](.*?)["”]|\'([^\']+?)\'')
_ID_PATTERN = re.compile(r"\b[A-Z]{2,6}-\d+\b")                    # BL-123, ADR-42
_CAPITALIZED_PHRASE = re.compile(
    # Multi-word Capitalized Phrase. `[ ]` (single space only) instead of
    # `\s+` so we don't match across newlines (section headers etc.).
    r"\b([A-Z][a-z]+(?:[ ](?:[A-Z][a-z]+|API|UI|DB|SDK|CLI)){1,5})\b"
)


def _extract_candidates(text: str) -> set[str]:
    cands: set[str] = set()
    for m in _QUOTED.finditer(text):
        val = m.group(1) or m.group(2)
        if val and len(val) >= 4:
            cands.add(val.strip())
    for m in _ID_PATTERN.finditer(text):
        cands.add(m.group(0))
    for m in _CAPITALIZED_PHRASE.finditer(text):
        phrase = m.group(1).strip()
        if len(phrase) >= 8:  # ignore two-word noise
            cands.add(phrase)
    return cands


def _project_known_strings(root: Path) -> set[str]:
    """Gather a set of 'known strings' from project content that a brief's
    references should fuzzy-match against."""
    known: set[str] = set()

    # Dep graph → module/file names and every path segment
    graph_path = root / ".sprint" / "dep-graph.json"
    if graph_path.exists():
        try:
            g = json.loads(graph_path.read_text())
            for fpath in g.get("forward", {}).keys():
                known.add(fpath)
                for part in fpath.split("/"):
                    if part:
                        known.add(part)
                        # Add filename without extension too
                        stem = part.rsplit(".", 1)[0]
                        if stem and stem != part:
                            known.add(stem)
        except (json.JSONDecodeError, OSError):
            pass

    # Inventory → top-level directories
    inv_path = root / ".sprint" / "inventory.json"
    if inv_path.exists():
        try:
            inv = json.loads(inv_path.read_text())
            for d in inv.get("directories", []):
                p = d.get("path")
                if p:
                    known.add(p)
        except (json.JSONDecodeError, OSError):
            pass

    # Existing scope.md content (bullets, modules, epic mentions)
    # v0.6.8: route through schema.extract_list_items so the known-string
    # scan inherits HR skipping (v0.6.1), `+ `/`* ` alt markers (v0.6.1),
    # and nested-bullet skipping (v0.6.7). Pre-v0.6.8 used inline
    # `if s.startswith("- ")` only — third twin of the bullet-extractor
    # split that v0.6.7 caught (consistency._bullet_items, triage._bullets).
    # Top-level only here: sub-bullets are descriptive ("25 illegal-cell
    # tests") and rarely contribute new reference vocabulary beyond what
    # the parent bullet already named.
    design = root / "design"
    if design.exists():
        for scope in design.glob("*/scope.md"):
            try:
                text = scope.read_text()
            except OSError:
                continue
            for item in schema.extract_list_items(text):
                # Strip markdown wrappers + trailing description
                tok = schema.extract_path_token(item)
                if tok and len(tok) >= 3:
                    known.add(tok)
                # Also keep the head of the bullet as a phrase
                head = item.split(" — ")[0].split("  ")[0].strip("`*_")
                if head and len(head) >= 4:
                    known.add(head)

    # Issues db → titles
    issues_db = root / ".sprint" / "issues.db.jsonl"
    if issues_db.exists():
        for line in issues_db.read_text().splitlines():
            try:
                issue = json.loads(line)
            except json.JSONDecodeError:
                continue
            title = (issue.get("title") or "").strip()
            if title and len(title) >= 5:
                known.add(title)
            for label in issue.get("labels", []) or []:
                if label:
                    known.add(label)

    # Previously authored briefs → feature slugs
    briefs_dir = root / "briefs"
    if briefs_dir.exists():
        for b in briefs_dir.glob("*.md"):
            known.add(b.stem)

    # v0.4.3: user-declared extra sources. For projects whose IDs live in
    # non-standard places (Python BACKLOG lists, ADR markdown tables, etc.)
    # — extend the known set with regex-extracted tokens from the declared
    # path(s). Declared under `brief_lint.extra_known_sources` in
    # .claude/sprint-config.yaml:
    #   brief_lint:
    #     extra_known_sources:
    #       - path: scripts/build_backlog.py
    #         grep: "BL-\\d+"
    config_path = root / ".claude" / "sprint-config.yaml"
    if config_path.exists():
        try:
            cfg = yaml.safe_load(config_path.read_text()) or {}
        except yaml.YAMLError:
            cfg = {}
        sources = ((cfg.get("brief_lint") or {}).get("extra_known_sources") or [])
        for src in sources:
            known.update(_extract_from_source(root, src))

    return {k for k in known if k}


def _extract_from_source(root: Path, source: dict) -> set[str]:
    """Extract tokens from a user-declared source.

    Supported shapes (distinguished by `type` field; defaults to "path_grep"):

    - path_grep (default):
        path: scripts/build_backlog.py
        grep: "BL-\\d+"
      Reads the file (or every file under the dir), runs the regex, and
      returns all matches.

    - command:
        type: command
        command: "python -c 'from scripts.build_backlog import BACKLOG; ...'"
        grep: "BL-\\d+"   # optional; applied to stdout
      Runs a shell command, collects tokens from stdout. For projects
      where IDs are position-derived at runtime (not literal in any file).
    """
    src_type = source.get("type", "path_grep")
    if src_type == "command":
        return _extract_from_command(root, source)
    return _extract_from_path_grep(root, source)


def _extract_from_path_grep(root: Path, source: dict) -> set[str]:
    raw_path = source.get("path")
    raw_grep = source.get("grep")
    if not raw_path or not raw_grep:
        return set()
    try:
        pattern = re.compile(raw_grep)
    except re.error:
        return set()
    base = (root / raw_path)
    if not base.exists():
        return set()
    found: set[str] = set()
    targets: list[Path] = []
    if base.is_file():
        targets = [base]
    elif base.is_dir():
        targets = [f for f in base.rglob("*") if f.is_file()]
    for f in targets:
        try:
            for m in pattern.finditer(f.read_text()):
                found.add(m.group(0))
        except (OSError, UnicodeDecodeError):
            continue
    return found


def _extract_from_command(root: Path, source: dict) -> set[str]:
    cmd = source.get("command")
    if not cmd:
        return set()
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=str(root),
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    out = result.stdout or ""
    raw_grep = source.get("grep")
    if raw_grep:
        try:
            pattern = re.compile(raw_grep)
        except re.error:
            return set()
        return {m.group(0) for m in pattern.finditer(out)}
    # No grep → one token per non-empty line
    return {line.strip() for line in out.splitlines() if line.strip()}


# Section headers that declare "IDs this brief intends to CREATE". Matched
# case-insensitively; both "Proposed new IDs" and "New IDs" work. Tokens
# in this section bypass the near-miss check FOR THIS BRIEF ONLY — they're
# explicitly marked as "not-yet-existing, but legitimate".
_PROPOSED_NEW_IDS_HEADING = re.compile(r"(?i)\b(proposed|new)\b.*\bids?\b")


def _proposed_new_ids(brief_text: str) -> set[str]:
    """Parse a `## Proposed new IDs` (or similar) section from a brief.

    Accepts bullets, comma-separated, or whitespace-separated tokens.
    """
    sections = schema.parse_sections(brief_text)
    for heading, body in sections.items():
        head = heading.lstrip("#").strip()
        if _PROPOSED_NEW_IDS_HEADING.search(head):
            tokens: set[str] = set()
            for line in body.splitlines():
                s = line.strip().lstrip("-").strip()
                if not s:
                    continue
                for part in re.split(r"[,\s]+", s):
                    part = part.strip().rstrip(",.;:")
                    if part and not part.startswith("("):
                        tokens.add(part)
            return tokens
    return set()


def _near_misses(candidates: set[str], known: set[str],
                 cutoff: float = 0.80,
                 suppressed: list | None = None) -> list[tuple[str, list[str]]]:
    results: list[tuple[str, list[str]]] = []
    known_list = list(known)
    # A candidate that is only a BENIGN AUTHORING VARIANT of a known sentinel —
    # case/separator (the Title-Case prose name of a kebab subsection), a markdown
    # wrapper, trailing punctuation — is a legitimate reference, not a typo, so it is
    # suppressed (fb-a1753e4a729d). The registry is the SAME one the scope/plan
    # path comparison uses, so a variant learned anywhere is known here too (the
    # cluster's anti-drift fix). A real typo canonicalizes differently → still flagged.
    for cand in candidates:
        if cand in known:
            continue
        bv = artifact_variants.variant_of(cand, known, artifact_variants.IDENTIFIER)
        if bv is not None:
            if suppressed is not None:
                suppressed.append((cand, bv))
            continue
        close = difflib.get_close_matches(cand, known_list, n=3, cutoff=cutoff)
        if close:
            results.append((cand, close))
    return results


def lint(brief_path: str | Path | None = None,
         root: Path | None = None,
         cutoff: float = 0.80) -> int:
    """Lint one brief (path given) or all briefs under briefs/ (no path)."""
    root = root or project_root()

    if brief_path:
        bp = Path(brief_path)
        if not bp.is_absolute():
            bp = root / bp
        if not bp.exists():
            print(f"[brief-lint] not found: {bp}")
            return 1
        briefs = [bp]
    else:
        briefs_dir = root / "briefs"
        if not briefs_dir.exists():
            print(f"[brief-lint] no briefs/ directory at {root}")
            return 0
        briefs = sorted(briefs_dir.glob("*.md"))
        if not briefs:
            print(f"[brief-lint] no briefs found under {briefs_dir}")
            return 0

    known = _project_known_strings(root)
    total_issues = 0

    for brief in briefs:
        print(f"[brief-lint] {brief.relative_to(root) if brief.is_relative_to(root) else brief}")

        # Structural: fields present, types correct, etc.
        ok, errors = schema.validate_brief(brief)
        if not ok:
            for e in errors:
                print(f"  [structural] {e}")
            total_issues += len(errors)

        # v0.9.0 — success_criteria sibling-file (briefs/<feature>.criteria.yaml).
        # During v0.9.0→v0.10.0 deprecation window: warn-only when absent.
        # When present: full schema validation (shape + verify_command path).
        criteria_path = schema.criteria_path_for_brief(brief)
        if criteria_path.exists():
            c_ok, c_errors = schema.validate_criteria_file(
                criteria_path, root, defer_markers=_defer_markers(root))
            if not c_ok:
                for e in c_errors:
                    print(f"  [criteria] {e}")
                total_issues += len(c_errors)
        else:
            print(f"  [criteria-warn] no sibling {criteria_path.name} found "
                  f"(v0.10.0 will require it; opt out via sprint-config "
                  f"`success_criteria: {{ required: false }}`)")

        # Near-miss
        brief_text = brief.read_text()
        candidates = _extract_candidates(brief_text)
        # v0.4.4: per-brief allow-list for proposed-new-IDs. Tokens declared
        # in an optional `## Proposed new IDs` (or similar) section are
        # admitted to the known set FOR THIS BRIEF ONLY.
        proposed = _proposed_new_ids(brief_text)
        brief_known = known | proposed
        if proposed:
            print(f"  [info] proposed-new-IDs admitted: {sorted(proposed)}")

        misses: list[tuple[str, list[str]]] = []
        if not brief_known:
            if candidates:
                print("  [info] no project known-strings available to fuzzy-match against")
                print("         (run `prusik discovery all` to populate, then re-lint)")
        else:
            suppressed: list = []
            misses = _near_misses(candidates, brief_known, cutoff=cutoff,
                                  suppressed=suppressed)
            # Record each benign-variant suppression so recurrence is MEASURABLE per
            # project (fuel for the calibration loop) — the analog of capture's
            # `capture_non_evidence`. A suppression means the gate did NOT false-flag
            # a legitimate reference; counting them shows which variant forms a project
            # actually authors. Best-effort: a logging failure never breaks lint.
            for cand, bv in suppressed:
                try:
                    from prusik import ledger
                    ledger.append("artifact_benign_variant", artifact="brief",
                                  token=cand, canonical=bv.canonical,
                                  variants=list(bv.variants))
                except Exception:  # noqa: BLE001
                    pass
            if misses:
                print(f"  [near-miss] {len(misses)} candidate(s):")
                for cand, matches in misses:
                    print(f"    '{cand}'  did you mean: {matches}")
                total_issues += len(misses)

        if ok and not misses:
            print("  [ok]")

    return 1 if total_issues else 0
