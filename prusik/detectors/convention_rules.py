"""convention-rules — repo-wide sweep of declared mechanical conventions.

fb-7f0bc1640914: the conventions-enforcer gates sprint DIFFS, so 8
pre-existing pagination-tiebreak violations persisted invisibly — untouched
files are never re-examined. Diff-gating stops new debt; this sweep retires
old debt.

Layering: the ENGINE ships the rule classes; the ADOPTER declares their
conventions in `.claude/conventions/rules.yaml` (dormant when absent):

    rules:
      - id: no-console-in-services
        description: services use the logger
        files: "src/services/**"        # glob, repo-relative
        forbid: "console\\.(log|warn)"  # line regex → finding per line
      - id: paginated-order-tiebreak
        files: "src/repo/**"
        pair:                            # file-level heuristic
          if_match: "\\.limit\\("        #   trigger present…
          must_match: "orderBy"          #   …counterpart absent ⇒ finding

Complex rules belong in the `.claude/detectors/*.py` local-detector escape
hatch. An invalid rule is itself a LOUD finding — a broken convention that
silently stops checking is worse than none.
"""

from __future__ import annotations

import fnmatch
import re

import yaml

from prusik.detectors.base import Finding, ScanContext

NAME = "convention-rules"
DESCRIPTION = ("repo-wide sweep of adopter-declared mechanical conventions "
               "(.claude/conventions/rules.yaml: forbid/pair rule classes); "
               "diff-gating stops new debt, the sweep retires old debt "
               "(fb-7f0bc1640914)")

_MAX_BYTES = 1_000_000            # skip huge/binary-ish files, never crash


def _load_rules(ctx: ScanContext) -> tuple[list[dict], list[Finding]]:
    path = ctx.root / ".claude" / "conventions" / "rules.yaml"
    if not path.is_file():
        return [], []
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        return [], [Finding(detector=NAME, cls="invalid-rule",
                            severity="warn",
                            message=f"rules.yaml is not valid YAML: {e}",
                            file=".claude/conventions/rules.yaml")]
    rules, problems = [], []
    for r in data.get("rules") or []:
        rid = str((r or {}).get("id") or "<missing-id>")
        files = (r or {}).get("files")
        forbid = (r or {}).get("forbid")
        pair = (r or {}).get("pair") or {}
        try:
            if not files or not (forbid or
                                 (pair.get("if_match")
                                  and pair.get("must_match"))):
                raise ValueError(
                    "needs `files` plus `forbid` or `pair.if_match` + "
                    "`pair.must_match`")
            compiled = {
                "id": rid, "files": str(files),
                "description": str(r.get("description", "")),
                "forbid": re.compile(forbid) if forbid else None,
                "if_match": (re.compile(pair["if_match"])
                             if pair.get("if_match") else None),
                "must_match": (re.compile(pair["must_match"])
                               if pair.get("must_match") else None),
            }
            rules.append(compiled)
        except (re.error, ValueError, TypeError) as e:
            problems.append(Finding(
                detector=NAME, cls="invalid-rule", severity="warn",
                message=(f"rule {rid!r} is invalid and is NOT being "
                         f"checked: {e} — a silently-dead convention is "
                         f"worse than none; fix the rule."),
                file=".claude/conventions/rules.yaml"))
    return rules, problems


def detect(ctx: ScanContext) -> list[Finding]:
    rules, out = _load_rules(ctx)
    if not rules:
        return out
    rel_files = []
    for p in ctx.files:
        if not p.is_file():
            continue
        try:
            rel = str(p.relative_to(ctx.root))
        except ValueError:
            continue
        rel_files.append((rel, p))
    for rule in rules:
        for rel, p in rel_files:
            if not fnmatch.fnmatch(rel, rule["files"]):
                continue
            try:
                if p.stat().st_size > _MAX_BYTES:
                    continue
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            if rule["forbid"] is not None:
                for n, line in enumerate(text.splitlines(), 1):
                    if rule["forbid"].search(line):
                        out.append(Finding(
                            detector=NAME, cls=rule["id"], severity="warn",
                            message=(f"{rel}:{n}: violates convention "
                                     f"{rule['id']!r}"
                                     + (f" — {rule['description']}"
                                        if rule['description'] else "")),
                            file=rel, line=n))
            elif rule["if_match"].search(text) and \
                    not rule["must_match"].search(text):
                out.append(Finding(
                    detector=NAME, cls=rule["id"], severity="warn",
                    message=(f"{rel}: matches {rule['id']!r} trigger but "
                             f"lacks its required counterpart"
                             + (f" — {rule['description']}"
                                if rule['description'] else "")),
                    file=rel))
    return out
