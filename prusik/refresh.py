"""`prusik refresh` — sync new prusik templates into an existing project.

After `prusik init` has been run once, subsequent prusik releases may ship new or
updated commands, agents, schemas, etc. This command copies those template
updates into the existing project WITHOUT clobbering user-modified files.

Uses the manifest written by `prusik init` (.claude/.prusik-manifest.json): for
each tracked file, if its current hash matches the manifest (i.e., user
hasn't touched it), replace with the current template version. If it
doesn't match, skip and report. Also copies any NEW template files that
didn't exist when the project was first init'd.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from prusik import __version__

KIT_ROOT = Path(__file__).parent
TEMPLATE_ROOT = KIT_ROOT / "templates"

# v0.14.1: files with a non-destructive additive-merge path. These NEVER
# take the blanket "matches manifest → wholesale overwrite" branch (the
# manifest hash is not a reliable uncustomized-oracle); they always route
# to their additive merge, which preserves user customizations by
# construction. --force / --no-merge-additions still bypass (user intent).
_MERGE_ELIGIBLE = (".claude/settings.json", ".claude/sprint-config.yaml")


def template_skew(root: Path) -> tuple[str, str, bool]:
    """(engine_version, deployed_template_version, skewed). `skewed` is True when
    the installed engine is AHEAD of the templates deployed in this project — the
    silent-drift loophole: you upgraded the package but never `prusik refresh`ed,
    so the engine expects agents the project doesn't have. Returns skewed=False
    when there's no manifest (pre-manifest project) — don't touch those."""
    from prusik import manifest, version_check
    m = manifest.load(manifest.manifest_path(root / ".claude"))
    tmpl = manifest.surface_version(m)
    return __version__, tmpl, version_check.is_newer(__version__, installed=tmpl)


def auto_sync_if_skewed(root: Path, config: dict) -> bool:
    """Close the package↔templates loophole automatically: if the engine is ahead
    of the deployed templates, refresh them to match — so a sprint can NEVER start
    on stale templates and nobody has to remember `prusik refresh`. Honors an
    `auto_refresh_on_skew: false` opt-out. Returns True if a sync ran. The refresh
    itself fails closed and visibly on a local-edit conflict (never silent)."""
    if config.get("auto_refresh_on_skew") is False:
        return False
    eng, tmpl, skewed = template_skew(root)
    if not skewed:
        return False
    print(f"[prusik] engine {eng} is ahead of this project's templates ({tmpl}) "
          f"— auto-syncing so you don't run a sprint on stale agents:")
    run(force=False)
    return True


def skew_banner(root: Path) -> str | None:
    """A LOUD one-line warning when the engine is STILL ahead of the deployed
    templates — i.e. a sprint is about to run on stale agents because auto-sync
    was opted out (`auto_refresh_on_skew: false`) or hit a local-edit conflict.
    The fixes exist in the engine but are inert until `prusik refresh`. None when
    in sync. (field bridge #4: an 11-version skew ran a whole sprint silently —
    doctor reported it, but nothing surfaced at sprint-start.)"""
    eng, tmpl, skewed = template_skew(root)
    if not skewed:
        return None
    from prusik import version_check
    e, t = version_check._parse(eng), version_check._parse(tmpl)
    # each prusik release bumps the minor, so within one major the minor delta IS
    # the number of releases behind — an accurate, network-free magnitude.
    behind = (f" ({e[1] - t[1]} releases behind)"
              if e and t and e[0] == t[0] and e[1] > t[1] else "")
    return (f"⚠ TEMPLATE SKEW: engine {eng} vs this project's deployed templates "
            f"{tmpl}{behind}. This sprint will run on STALE agents — fixes shipped "
            f"since {tmpl} are INERT until you sync. Run `prusik refresh` then "
            f"restart Claude Code to apply them.")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _config_regressions(old: Any, new: Any, path: str = "") -> list[str]:
    """Destructive config changes a refresh must never make silently (field bridge):
    an `enabled: true` gate turned off/removed, or a populated list emptied. Walks
    the USER's tree (old); the template (new) only matters where it would flip or
    drop what the user has. A missing key in `new` counts as a regression for both
    cases — a commented-out gate or an absent list is the same silent loss."""
    out: list[str] = []
    if isinstance(old, dict):
        nd = new if isinstance(new, dict) else {}
        for k, ov in old.items():
            p = f"{path}.{k}" if path else k
            nv = nd.get(k)
            if k == "enabled" and ov is True and nv is not True:
                out.append(f"{path or k}.enabled true→{nv!r}")
            elif isinstance(ov, list) and ov and not nv:
                out.append(f"{p}: populated list ({len(ov)}) emptied")
            else:
                out.extend(_config_regressions(ov, nv, p))
    return out


def _user_content_loss(rel_path: str, old_text: str, new_text: str) -> str | None:
    """v0.14.2 backstop invariant. Refuse to write a merge-eligible config
    if the new content would DROP a top-level key (or, for sprint-config, a
    phase) the user currently has. Independent of root cause — it would
    have caught the v0.14.0 clobber regardless of WHICH bug caused it. The
    merge path is non-destructive by construction; this verifies that
    rather than trusting it (prusik's own thesis applied to its own
    refresh: nothing advances on 'should be correct', only on evidence).
    Unparseable old → cannot certify no-loss → treated as loss (fail
    closed; never write unverified). Returns a reason or None."""
    try:
        if rel_path == ".claude/settings.json":
            old = json.loads(old_text)
            new = json.loads(new_text)
            dropped = [k for k in old if k not in new]
            if dropped:
                return f"top-level key(s) {dropped} present before, absent after"
            o_h, n_h = (old.get("hooks") or {}), (new.get("hooks") or {})
            lost_ev = [ev for ev in o_h if ev not in n_h]
            if lost_ev:
                return f"hooks event(s) {lost_ev} present before, absent after"
            # permissions.allow entries dropped (An adopter lost Write(**)/Edit(**) →
            # more prompts, the opposite of intent).
            o_allow = ((old.get("permissions") or {}).get("allow") or [])
            n_allow = ((new.get("permissions") or {}).get("allow") or [])
            lost_allow = [p for p in o_allow if p not in n_allow]
            if lost_allow:
                return f"permissions.allow {lost_allow} present before, absent after"
        elif rel_path == ".claude/sprint-config.yaml":
            import yaml as _y
            old = _y.safe_load(old_text) or {}
            new = _y.safe_load(new_text) or {}
            dropped = [k for k in old if k not in new]
            if dropped:
                return f"top-level key(s) {dropped} present before, absent after"
            o_ph = {p.get("name") for p in (old.get("phases") or [])
                    if isinstance(p, dict)}
            n_ph = {p.get("name") for p in (new.get("phases") or [])
                    if isinstance(p, dict)}
            lost = sorted(n for n in (o_ph - n_ph) if n)
            if lost:
                return f"phase(s) {lost} present before, absent after"
            # an enabled gate turned off, or a populated additive list emptied
            # (An adopter: behavior_regression/project_policy reverted to false, and
            # brief_lint.extra_known_sources emptied — silent gate-disable).
            reg = _config_regressions(old, new)
            if reg:
                return "; ".join(reg)
    except Exception as e:
        return (f"could not verify no-content-loss ({e}) — refusing to "
                f"write (fail-closed; never write unverified)")
    return None


def run(force: bool = False, no_auto_adopt: bool = False,
        no_merge_additions: bool = False) -> int:
    """Sync project .claude/ to current prusik templates.

    Behavior matrix for each file under the template tree:

    - Missing on disk → create (new).
    - On disk, content matches new template → nothing to do (unchanged).
    - On disk, in manifest, hash matches manifest → overwrite with new
      template (safe; user hasn't edited since install). Updated.
    - On disk, in manifest, hash DIFFERS from manifest → user-authored
      edit. Skipped unless --force.
    - On disk, NOT in manifest (added to templates after project was
      init'd): v0.4.2 default is to AUTO-ADOPT as stale stock — overwrite
      with new template content. Pass --no-auto-adopt to preserve the
      pre-v0.4.2 behavior (skip, report as modified).

    Rationale for auto-adopt default: files at template paths that have
    no manifest entry are almost always stale copies from an older prusik
    version's init that didn't track them. Genuine user-authored files
    at those paths would be rare (requires manually authoring AFTER
    init, which would then go through manifest-aware channels).
    """
    target = Path.cwd()
    from prusik import manifest as _manifest
    claude_dir = target / ".claude"
    manifest_path = _manifest.find_manifest(claude_dir)
    if manifest_path is None:
        print(f"[prusik-refresh] no manifest in {claude_dir} — run `prusik init` "
              "first (or this project predates manifest support).")
        return 1

    manifest = _manifest.load(manifest_path)  # reads + migrates schema in memory (no write)
    if manifest is None:
        print(f"[prusik-refresh] unreadable {manifest_path}")
        return 1
    tracked = {entry["path"]: entry["hash"] for entry in manifest.get("files", [])}

    updated: list[str] = []
    new_files: list[str] = []
    skipped_modified: list[str] = []
    adopted_stale: list[str] = []
    unchanged: list[str] = []
    merged: list[tuple[str, dict]] = []  # v0.5.8: (path, summary) for surgical merges
    merge_failed: list[tuple[str, str]] = []  # v0.14.0: hard fail-closed (path, reason)

    # Walk every template file
    template_src_roots = [
        (TEMPLATE_ROOT / ".claude", target / ".claude"),
        (TEMPLATE_ROOT / "artifacts", target / ".claude" / "artifact-templates"),
    ]

    new_manifest_entries: list[dict] = []

    for src_root, dest_root in template_src_roots:
        if not src_root.exists():
            continue
        for src in src_root.rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(src_root)
            dest = dest_root / rel
            dest_rel_str = str(dest.relative_to(target))

            new_hash = _sha256(src)

            if not dest.exists():
                # New template file — didn't exist when project was init'd
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                new_files.append(dest_rel_str)
                new_manifest_entries.append({"path": dest_rel_str, "hash": new_hash})
                continue

            current_hash = _sha256(dest)
            manifest_hash = tracked.get(dest_rel_str)

            if current_hash == new_hash:
                # Target already matches template; nothing to do. Keep manifest entry.
                unchanged.append(dest_rel_str)
                new_manifest_entries.append({"path": dest_rel_str, "hash": new_hash})
                continue

            if manifest_hash is None:
                # File is at a prusik template path but wasn't tracked in the
                # manifest. Almost certainly a stale stock file from an
                # older prusik version's init that didn't include this path.
                # v0.4.2: auto-adopt by default (see docstring rationale).
                # --no-auto-adopt preserves pre-v0.4.2 "treat as modified"
                # behavior for users who want maximum conservatism.
                if no_auto_adopt and not force:
                    skipped_modified.append(dest_rel_str + " (not in original manifest)")
                else:
                    shutil.copy2(src, dest)
                    adopted_stale.append(dest_rel_str)
                    new_manifest_entries.append({"path": dest_rel_str, "hash": new_hash})
                continue

            # v0.14.1: branch-precedence fix. `current_hash == manifest_hash`
            # is NOT a reliable "uncustomized" oracle — a prior `prusik init`
            # or a prior additive *merge* (which records the merged hash)
            # makes the manifest equal a *customized* on-disk state. The
            # blanket overwrite below then wholesale-clobbers a customized
            # config instead of merging it — which defeated v0.14.0 and
            # destroyed a real project's sprint-config. So a merge-eligible
            # file never takes the blanket overwrite: it always routes to
            # its additive (non-destructive by construction) merge path.
            # `--force` (explicit user intent) and `--no-merge-additions`
            # (explicit opt-out) still bypass the merge — user-chosen, not
            # a silent system action.
            # v0.90.0 (field bridge, HIGH): `--force` must NOT bypass the additive
            # merge for structured CONFIG. --force re-adopts user-modified *agent
            # templates*; it is not a licence to wholesale-clobber settings.json /
            # sprint-config — which silently disabled a project's enabled gates,
            # bridge hook, and Write/Edit permissions (a no-silent-fallback
            # violation: the user upgrades and ships with their gates off). Config
            # always routes to its non-destructive merge; only the EXPLICIT
            # --no-merge-additions opt-out overwrites.
            is_merge_eligible = (
                dest_rel_str in _MERGE_ELIGIBLE and not no_merge_additions)
            if (current_hash == manifest_hash or force) and not is_merge_eligible:
                # User hasn't touched it since init (or --force) → overwrite.
                # v0.14.2 backstop: if a structured config reaches this
                # branch WITHOUT explicit --force (e.g. precedence logic
                # regresses, or --no-merge-additions), refuse to wholesale-
                # overwrite if it would drop user content. --force is the
                # one explicit clobber escape hatch (user-chosen).
                _loss = (None if force or dest_rel_str not in _MERGE_ELIGIBLE
                         else _user_content_loss(
                             dest_rel_str, dest.read_text(), src.read_text()))
                if _loss:
                    merge_failed.append(
                        (dest_rel_str, f"USER CONTENT LOSS — {_loss}"))
                    new_manifest_entries.append(
                        {"path": dest_rel_str, "hash": manifest_hash})
                else:
                    shutil.copy2(src, dest)
                    updated.append(dest_rel_str)
                    new_manifest_entries.append({"path": dest_rel_str, "hash": new_hash})
            elif (dest_rel_str == ".claude/settings.json"
                  and not no_merge_additions):
                # v0.5.8: surgical additive merge for settings.json.
                # User customized hooks or permissions; rather than skip
                # entirely (stranding the project on a stale baseline),
                # union template's permissions.allow|deny|ask into the
                # project's value. Hooks + scalar values stay user-owned.
                from prusik import refresh_merge
                template_text = src.read_text()
                project_text = dest.read_text()
                merged_text, summary = refresh_merge.merge_settings_json(
                    template_text, project_text
                )
                _loss = _user_content_loss(
                    dest_rel_str, project_text, merged_text)
                if _loss:
                    # Backstop: the merge is non-destructive by
                    # construction; if it ever isn't, fail closed.
                    merge_failed.append(
                        (dest_rel_str, f"USER CONTENT LOSS — {_loss}"))
                    new_manifest_entries.append(
                        {"path": dest_rel_str, "hash": manifest_hash})
                elif merged_text == project_text:
                    # Project already has every additive entry — no-op.
                    unchanged.append(dest_rel_str)
                    new_manifest_entries.append(
                        {"path": dest_rel_str, "hash": current_hash}
                    )
                else:
                    dest.write_text(merged_text)
                    merged_hash = hashlib.sha256(
                        merged_text.encode("utf-8")
                    ).hexdigest()
                    merged.append((dest_rel_str, summary))
                    new_manifest_entries.append(
                        {"path": dest_rel_str, "hash": merged_hash}
                    )
            elif (dest_rel_str == ".claude/sprint-config.yaml"
                  and not no_merge_additions):
                # v0.14.0: additive merge for the heavily-customized YAML
                # config with the twice-confirmed silent-non-deployment
                # defect (v0.11.0 #2 trivial lane; v0.12.0 F evidence
                # gate). Same philosophy as settings.json. If the merge
                # cannot be performed safely we FAIL CLOSED, VISIBLY: never
                # clobber, never a partial write — and never a silent
                # degrade-to-skip with rc 0. A skip the SYSTEM chose
                # (vs. the user's explicit --no-merge-additions) is an
                # unnoticed behavioral change: the operator believes they
                # are current while an enforcement delta silently did not
                # land. So it surfaces as a blocking failure (rc 1), the
                # v0.11.2 bridge precedent: "never silently lost, not
                # never fails". --no-merge-additions is the ONLY benign
                # skip (intended by the user, not a degradation).
                from prusik import refresh_merge
                try:
                    merged_text, summary = (
                        refresh_merge.merge_sprint_config_yaml(
                            src.read_text(), dest.read_text()))
                    ok_merge = True
                except Exception as _e:  # unparseable / unrecognized shape
                    ok_merge = False
                    _merge_err = _e
                if not ok_merge:
                    merge_failed.append((dest_rel_str, str(_merge_err)))
                    # No write (no clobber); keep manifest hash so a fixed
                    # re-run still works. NOT added to skipped_modified —
                    # this is a hard failure, not the benign skip bucket.
                    new_manifest_entries.append(
                        {"path": dest_rel_str, "hash": manifest_hash})
                elif (_cl := _user_content_loss(
                        dest_rel_str, dest.read_text(), merged_text)):
                    # Backstop: merge is additive-by-construction; if a
                    # regression ever drops user content, fail closed.
                    merge_failed.append(
                        (dest_rel_str, f"USER CONTENT LOSS — {_cl}"))
                    new_manifest_entries.append(
                        {"path": dest_rel_str, "hash": manifest_hash})
                elif merged_text == dest.read_text():
                    unchanged.append(dest_rel_str)
                    new_manifest_entries.append(
                        {"path": dest_rel_str, "hash": current_hash})
                else:
                    dest.write_text(merged_text)
                    merged_hash = hashlib.sha256(
                        merged_text.encode("utf-8")).hexdigest()
                    merged.append((dest_rel_str, summary))
                    new_manifest_entries.append(
                        {"path": dest_rel_str, "hash": merged_hash})
            else:
                skipped_modified.append(dest_rel_str)
                # Keep the old manifest hash so a future refresh with --force works
                new_manifest_entries.append({"path": dest_rel_str, "hash": manifest_hash})

    # v0.13.0: single choke point — the surface version stamp and the file
    # list move together, so doctor can never again report a stale version
    # after a refresh. If the manifest predated install-time detection,
    # establish the drift baseline now (refresh has the live project in
    # hand; doctor must not, being read-only) so the "predates detection"
    # nag self-heals instead of persisting forever.
    _manifest.record_surface_write(
        manifest, command="refresh", version=__version__,
        files=new_manifest_entries)
    if not manifest.get("detection"):
        try:
            from prusik import detect as _detect
            _manifest.record_detection_backfill(
                manifest, _detect.detect_project(target), __version__)
        except Exception:
            pass  # backfill is best-effort; never fail a refresh on it
    _manifest.save(manifest, manifest_path)

    print(f"[prusik-refresh] prusik {__version__} → this project")
    print(f"  updated:  {len(updated)}")
    for p in updated:
        print(f"    - {p}")
    print(f"  new:      {len(new_files)}")
    for p in new_files:
        print(f"    + {p}")
    if adopted_stale:
        print(f"  adopted:  {len(adopted_stale)} stale template file(s) "
              f"from older init not tracked in manifest")
        for p in adopted_stale:
            print(f"    ~ {p}")
        print("  (pass --no-auto-adopt to treat these as user-modified instead)")
    if merged:
        print(f"  merged:   {len(merged)} additive merge(s) (user customizations preserved)")
        for path, summary in merged:
            perm_adds = summary.get("permission_additions") or {}
            top_adds = summary.get("added_top_level_keys") or []
            bits = []
            for sub, count in perm_adds.items():
                bits.append(f"+{count} {sub}")
            # v0.14.0 sprint-config summary
            for ph in summary.get("phases_added") or []:
                bits.append(f"+phase {ph}")
            for pk in summary.get("phase_keys_added") or []:
                bits.append(f"+{pk}")
            for pk, n in (summary.get("list_additions") or {}).items():
                bits.append(f"+{n} {pk}")
            if top_adds:
                bits.append(f"+top-level: {top_adds}")
            print(f"    ⊕ {path}  ({', '.join(bits) if bits else 'no-op'})")
        print("  (pass --no-merge-additions to skip surgical merge instead)")
    print(f"  unchanged:{len(unchanged)}")
    if skipped_modified:
        print(f"  skipped:  {len(skipped_modified)} (user-modified; pass --force to overwrite)")
        for p in skipped_modified:
            print(f"    ! {p}")
    if not updated and not new_files and not adopted_stale and not merged:
        print("  (project is already current)")

    # v0.4.5: warn when agent-prompt files changed. Claude Code caches
    # `.claude/agents/*.md` at session start; a refresh that only updates
    # agent prompts will NOT affect a running CC session until it restarts.
    # Discovered live when a v0.4.1 brief-critic fix sat on disk but the running
    # session kept calling the pre-fix prompt. v0.78.0 (field finding #13):
    # RESTART is the reliable fix — `/agents` reloads the interactive picker but
    # NOT the Agent-tool dispatch an orchestrator uses mid-sprint, so a session
    # that DISPATCHES agents can't use a newly-added/changed role until restart.
    changed_agents = [p for p in (updated + new_files + adopted_stale)
                      if p.startswith(".claude/agents/")]
    if changed_agents:
        print()
        print(f"  NOTE: {len(changed_agents)} agent file(s) changed. Claude Code caches")
        print("        agent prompts at session start. RESTART the session to pick them")
        print("        up — `/agents` reloads the interactive picker but NOT the")
        print("        Agent-tool dispatch an orchestrator uses mid-sprint, so a")
        print("        sprint-driving session must restart to use a new/changed agent.")

    # v0.14.0: fail closed, visibly. A merge the SYSTEM could not safely
    # perform is an unnoticed behavioral change — the operator believes
    # they are current while an enforcement delta silently did not land.
    # Surface it as a blocking FAILURE (rc 1), never a clean refresh.
    if merge_failed:
        print()
        print("  ┌─ MERGE FAILED — refresh is reporting FAILURE (exit 1) ──────")
        print("  │ These files were NOT updated and NOT skipped-as-benign.")
        print("  │ An additive enforcement delta did NOT land; do not treat")
        print("  │ this as a clean refresh.")
        for p, reason in merge_failed:
            print(f"  │   ✗ {p}")
            print(f"  │       reason: {reason}")
            print("  │       fix: resolve the above (often: invalid YAML in")
            print("  │            your config), then re-run `prusik refresh`.")
            print("  │            To inspect the missing deltas:")
            print(f"  │            diff {p} <prusik>/prusik/templates/{p}")
        print("  └────────────────────────────────────────────────────────────")
        return 1
    return 0
