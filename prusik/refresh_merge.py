"""Surgical additive merge for tracked JSON config files (v0.5.8).

When a user has customized `.claude/settings.json` (e.g. added project-
specific hooks or allow entries), `prusik refresh` previously detected the
divergence and SKIPPED the file entirely. Result: a project that adopted
prusik pre-v0.5.3 stayed permanently stranded on a stale baseline — the 59
permission entries shipped in v0.5.3 never reached the project's
settings.json, and any subagent in that project hit silent Bash denials
indefinitely.

Surgical merge solves this for known additive keys: UNION template
entries into the project's value, preserving every customization the
user made. Hooks and scalar values are left untouched (user wins).

Surfaced by live-cc's [post-sprint] OBSERVATION on 2026-04-25.
"""

from __future__ import annotations

import json
from typing import Any

# Keys under `permissions` whose values are arrays of strings that grow
# additively across prusik releases — safe to union-merge.
_ADDITIVE_PERMISSION_KEYS = ("allow", "deny", "ask")

# The ONLY allow entry the FSM itself needs (so `prusik gate` / `sprint-*` don't
# prompt). `--minimal-perms` adds just this — the rest of the template allowlist
# (Write(**), Edit(**), Bash(docker *), …) is dev convenience an adopter should
# opt into, not a silent broadening of a repo's committed auto-approve posture.
_HARNESS_REQUIRED_ALLOW = frozenset({"Bash(prusik *)"})


def _merge_hooks(template_hooks: dict, project_hooks: dict
                 ) -> tuple[dict, list[str]]:
    """Append template hook groups ALONGSIDE the project's, per event, never
    overwriting — so a project's own PreToolUse + prusik's gate both fire.
    Idempotent: a group whose command already appears is skipped. Returns
    (merged_hooks, events_appended)."""
    merged = {k: (list(v) if isinstance(v, list) else v)
              for k, v in project_hooks.items()}
    added: list[str] = []
    for event, groups in template_hooks.items():
        if not isinstance(groups, list):
            continue
        existing = merged.setdefault(event, [])
        if not isinstance(existing, list):
            continue
        have = {h.get("command")
                for g in existing if isinstance(g, dict)
                for h in (g.get("hooks") or []) if isinstance(h, dict)}
        for g in groups:
            cmds = {h.get("command") for h in (g.get("hooks") or [])
                    if isinstance(h, dict)}
            if cmds & have:
                continue  # already wired (idempotent re-run)
            existing.append(g)
            added.append(event)
    return merged, added


def merge_settings_json(template_text: str, project_text: str,
                        merge_hooks: bool = False,
                        minimal_perms: bool = False) -> tuple[str, dict]:
    """Surgical additive merge of `.claude/settings.json`.

    Rules:
      - Project's existing values are NEVER overwritten.
      - For `permissions.allow|deny|ask`: append template entries that the
        project doesn't already have (deduped by string equality).
        Project-specific entries are preserved at the front of the list.
      - For top-level keys present in template but missing from project
        (e.g., template adds a new section): copied in wholesale.
      - For `hooks` and other complex/non-additive keys: project wins
        entirely (no merge — user customizations are not safe to interleave).

    Returns:
      (merged_text, summary) where summary describes the additions made.
    """
    template = json.loads(template_text)
    project = json.loads(project_text)

    # v0.53.4: --minimal-perms reduces the template allowlist to ONLY the
    # harness-required entry before any merge — so a repo with no permissions
    # block (its tools already session-approved) doesn't get the full ~70-entry
    # auto-approve baked into its committed posture. Applied before both the
    # wholesale top-level copy and the array union below.
    if minimal_perms:
        tp = template.get("permissions")
        if isinstance(tp, dict):
            template = dict(template)
            template["permissions"] = {
                **{k: v for k, v in tp.items() if k != "allow"},
                "allow": [e for e in (tp.get("allow") or [])
                          if e in _HARNESS_REQUIRED_ALLOW],
            }

    summary: dict[str, Any] = {
        "permission_additions": {},   # {"allow": int, "deny": int, "ask": int}
        "added_top_level_keys": [],   # list[str]
    }

    merged = dict(project)  # start from project; only ADD from template

    # Top-level keys missing from project: copy from template wholesale.
    # (This wires prusik's hooks ONLY when the project has no `hooks` key.)
    for key, value in template.items():
        if key not in merged:
            merged[key] = value
            summary["added_top_level_keys"].append(key)

    # v0.53.3: optional surgical hook union. When the project has its OWN hooks
    # (its governance, e.g. a scope-guard PreToolUse), the wholesale copy above
    # is skipped and prusik's gate hooks are NOT wired — leaving the FSM inert
    # (finding #5). With merge_hooks, APPEND prusik's gate hooks alongside the
    # user's (per event, idempotent) so both fire. Snapshotted by init for a
    # clean uninstall.
    if merge_hooks:
        t_hooks, p_hooks = template.get("hooks"), merged.get("hooks")
        if isinstance(t_hooks, dict) and isinstance(p_hooks, dict):
            unioned, added = _merge_hooks(t_hooks, p_hooks)
            if added:
                merged["hooks"] = unioned
                summary["hooks_merged"] = added

    # Surgical union-merge of permissions.allow|deny|ask.
    template_perms = template.get("permissions") or {}
    if isinstance(template_perms, dict) and isinstance(
        merged.get("permissions"), dict
    ):
        for sub_key in _ADDITIVE_PERMISSION_KEYS:
            template_list = template_perms.get(sub_key, [])
            if not isinstance(template_list, list):
                continue
            project_list = merged["permissions"].get(sub_key, [])
            if not isinstance(project_list, list):
                continue
            project_set = set(project_list)
            additions = [x for x in template_list if x not in project_set]
            if additions:
                merged["permissions"][sub_key] = list(project_list) + additions
                summary["permission_additions"][sub_key] = len(additions)

        # v0.15.1: also add nested keys inside `permissions` the project
        # lacks — e.g., `defaultMode`. Without this, a template change that
        # adds a NEW nested key (not in _ADDITIVE_PERMISSION_KEYS) silently
        # doesn't propagate, while the union-merge reports `merged: 1`
        # truthfully because the array union ran — a real false-clean shape
        # surfaced by v0.15.0 shipping `defaultMode: "acceptEdits"` and not
        # landing on an adopter. Project-wins for keys already set (additive, never
        # overwrite). Hooks deliberately stay project-wins-only at the
        # top-level branch above ("complex/non-additive; not safe to
        # interleave"); this carve-out is scoped to `permissions` because
        # prusik owns the permissions schema.
        for sub_key, sub_value in template_perms.items():
            if sub_key in _ADDITIVE_PERMISSION_KEYS:
                continue  # handled above by the array-union path
            if sub_key not in merged["permissions"]:
                merged["permissions"][sub_key] = sub_value
                summary.setdefault("added_permission_keys", []).append(sub_key)

    return json.dumps(merged, indent=2) + "\n", summary


# v0.14.0 — additive merge-coverage for `.claude/sprint-config.yaml`.
#
# Same philosophy as merge_settings_json (union additive structure, NEVER
# overwrite a user value), extended to the one heavily-customized YAML file
# with a twice-confirmed silent-non-deployment defect (v0.11.0 #2 trivial
# lane; v0.12.0 F evidence gate). ruamel round-trip preserves the user's
# comments/formatting — a naive PyYAML round-trip would destroy them, a
# silent-destruction prusik must not commit.

# exit-artifact-shaped lists are keyed by `path`; scalar lists by value.
_PATH_KEYED_LIST_KEYS = ("exit_artifacts", "trivial_exit_artifacts")


def _yaml_rt():
    from ruamel.yaml import YAML
    y = YAML()  # round-trip mode: preserves comments + formatting
    y.preserve_quotes = True
    y.width = 4096  # don't line-wrap long template strings
    return y


def _item_identity(item):
    """Stable identity for list-union. Mapping with a `path` → that path
    (exit_artifacts); otherwise the value itself (writable/deny_commands)."""
    if isinstance(item, dict):
        return ("path", item.get("path"))
    return ("val", item)


def _truncate_at_blank(text: str) -> str:
    """Keep a value's OWN trailing comment, drop a following key's leading block.
    A blank line conventionally separates an inline/own comment from the next
    section's leading comments, so cut at the first blank line."""
    kept: list[str] = []
    for ln in text.split("\n"):
        if ln.strip() == "":
            break
        kept.append(ln)
    return ("\n".join(kept) + "\n") if kept else ""


def _untangle_dragged_comment(parent_map: Any, key: Any) -> None:
    """When the additive merge copies a missing top-level key, ruamel parks the
    NEXT template key's leading comment block on the copied value's trailing
    comment token (confirmed: a block-list key absorbs the following key's
    comments). Left alone it lands orphaned at the end of the adopter's config on
    every refresh — cosmetic, but it compounds. Truncate the trailing comment at
    the first blank line so only the key's own comment survives."""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    def _fix_slot(slot: Any) -> None:
        if slot is None:
            return
        for tok in (slot if isinstance(slot, list) else [slot]):
            for t in (tok if isinstance(tok, list) else [tok]):
                if t is not None and hasattr(t, "value") \
                        and isinstance(t.value, str) and "\n\n" in t.value:
                    t.value = _truncate_at_blank(t.value)

    # comment parked on the parent mapping for this key (scalar values)
    _fix_slot(getattr(getattr(parent_map, "ca", None), "items", {}).get(key))
    val = parent_map[key]
    # comment parked on the last element of a copied block list / mapping
    if isinstance(val, CommentedSeq) and len(val):
        _fix_slot(val.ca.items.get(len(val) - 1))
    elif isinstance(val, CommentedMap) and len(val):
        _fix_slot(val.ca.items.get(list(val.keys())[-1]))


def _iter_comment_tokens(ca):
    """Yield every ruamel CommentToken reachable from a comment-attribute."""
    if ca is None:
        return

    def expand(x):
        if x is None:
            return
        if isinstance(x, list):
            for i in x:
                yield from expand(i)
        elif hasattr(x, "value"):
            yield x

    for x in (ca.comment or []):
        yield from expand(x)
    for v in (getattr(ca, "items", None) or {}).values():
        yield from expand(v)


def _sanitize_empty_comments(node, _seen=None) -> None:
    """ruamel's emitter.write_comment does `value[-1]` with no length guard, so a
    CommentToken whose value is the empty string crashes serialize with IndexError
    (fb-80eb508aa7fd: the additive merge's node-copy of a top-level key left an
    empty post-comment, and `prusik refresh/update` then died with MERGE FAILED on
    a config that is VALID YAML — silently blocking the adopter from every future
    enforcement delta). Normalize any empty comment value to a benign newline across
    the whole merged tree before dump."""
    if _seen is None:
        _seen = set()
    if id(node) in _seen:
        return
    _seen.add(id(node))
    for ct in _iter_comment_tokens(getattr(node, "ca", None)):
        if ct.value == "":
            ct.value = "\n"
    if isinstance(node, dict):
        for v in node.values():
            _sanitize_empty_comments(v, _seen)
    elif isinstance(node, list):
        for v in node:
            _sanitize_empty_comments(v, _seen)


def _strip_all_comments(node, _seen=None) -> None:
    """Last-resort: drop all comment attributes so serialize can't touch a comment
    token at all. Used only if the sanitized round-trip still fails — a
    comment-stripped merge with CORRECT data beats a crash that blocks updates."""
    if _seen is None:
        _seen = set()
    if id(node) in _seen:
        return
    _seen.add(id(node))
    ca = getattr(node, "ca", None)
    if ca is not None:
        try:
            ca.comment = None
        except Exception:  # noqa: BLE001
            pass
        try:
            (getattr(ca, "items", None) or {}).clear()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(node, dict):
        for v in node.values():
            _strip_all_comments(v, _seen)
    elif isinstance(node, list):
        for v in node:
            _strip_all_comments(v, _seen)


def merge_sprint_config_yaml(template_text: str, project_text: str
                             ) -> tuple[str, dict]:
    """Surgical additive merge of `.claude/sprint-config.yaml`.

    Rules (identical philosophy to settings.json — purely additive, the
    user never loses/changes/reorders anything):
      - phases keyed by `name`. Template phase the project lacks → append
        whole. Template phase the project has → merge phase-level:
          * key absent in project phase → add it
          * list value → union append-only (exit_artifacts/
            trivial_exit_artifacts by `path`; writable/deny_commands by
            string); existing project items untouched
          * scalar/map value → PROJECT WINS, untouched
      - top-level key absent in project → add; present → project wins.

    Returns (merged_text, summary). Raises on unrecognized structure so
    the caller can fall back to a loud, precise skip (never clobber).
    """
    y = _yaml_rt()
    import io as _io
    proj = y.load(project_text)
    tmpl = y.load(template_text)
    if not isinstance(proj, dict) or not isinstance(tmpl, dict):
        raise ValueError("sprint-config.yaml top-level is not a mapping")

    summary: dict[str, Any] = {
        "phases_added": [],          # list[str]
        "phase_keys_added": [],      # list["phase.key"]
        "list_additions": {},        # {"phase.key": int}
        "added_top_level_keys": [],  # list[str]
    }
    changed = False

    tphases = tmpl.get("phases") or []
    pphases = proj.get("phases")
    if tphases and not isinstance(pphases, list):
        raise ValueError("project sprint-config has no phases list to merge")
    # After the guard: whenever there are template phases to merge, pphases is
    # a list. Bind a definitely-list alias for the append below (mypy can't
    # narrow through the `tphases and …` guard, and the loop body only runs
    # when tphases is truthy anyway).
    plist: list = pphases if isinstance(pphases, list) else []
    pby = {p.get("name"): p for p in plist if isinstance(p, dict)}

    for tph in tphases:
        if not isinstance(tph, dict):
            continue
        name = tph.get("name")
        pph = pby.get(name)
        if pph is None:
            plist.append(tph)                 # whole new phase, comments intact
            summary["phases_added"].append(name)
            changed = True
            continue
        for k, tv in tph.items():
            if k not in pph:
                pph[k] = tv
                summary["phase_keys_added"].append(f"{name}.{k}")
                changed = True
            elif isinstance(pph[k], list) and isinstance(tv, list):
                have = {_item_identity(i) for i in pph[k]}
                adds = [i for i in tv if _item_identity(i) not in have]
                if adds:
                    pph[k].extend(adds)        # append-only; existing untouched
                    summary["list_additions"][f"{name}.{k}"] = len(adds)
                    changed = True
            # scalar/map present → project wins; do nothing.

    for k, tv in tmpl.items():
        if k == "phases":
            continue
        if k not in proj:
            proj[k] = tv
            _untangle_dragged_comment(proj, k)   # drop next-key comments ruamel dragged
            summary["added_top_level_keys"].append(k)
            changed = True

    if not changed:
        return project_text, summary            # byte-identical no-op

    buf = _io.StringIO()
    try:
        _sanitize_empty_comments(proj)
        y.dump(proj, buf)
    except Exception as e:  # noqa: BLE001 — a serializer edge case must NEVER block
        import sys as _sys
        print(f"[prusik-refresh] warning: comment-preserving merge hit a "
              f"serializer edge case ({type(e).__name__}); falling back to a "
              f"comment-stripped merge so the update still lands.", file=_sys.stderr)
        _strip_all_comments(proj)
        buf = _io.StringIO()
        y.dump(proj, buf)
    return buf.getvalue(), summary
