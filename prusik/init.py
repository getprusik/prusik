"""`prusik init` — scaffold prusik into a target project.

Copies templates (settings.json, sprint-config.yaml, agents/, commands/, schemas/,
artifact templates), creates the working directories (briefs/, design/, etc.),
and optionally ingests a conventions pack (symlink + config reference).

Writes `.claude/.prusik-manifest.json` tracking every file created so `prusik uninstall`
can later remove only what prusik installed, preserving any user customizations.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from prusik import __version__

KIT_ROOT = Path(__file__).parent
TEMPLATE_ROOT = KIT_ROOT / "templates"

GITIGNORE_BEGIN = "# --- begin prusik ---"
GITIGNORE_END = "# --- end prusik ---"

WORKING_DIRS = ["briefs", "design", "reports", "decisions", "worktrees"]

GITIGNORE_ENTRIES = [
    ".sprint/ledger.jsonl",
    ".sprint/inventory.json",
    ".sprint/dep-graph.json",
    ".sprint/state.json",
    ".sprint/incidents/",
    ".sprint/status/",
    ".sprint/issues.db.jsonl",
    "worktrees/",
    ".claude/settings.local.json",
]


def run(conventions: str | None = None, force: bool = False,
        merge_settings: bool = True, stack: str | None = None,
        allow_dirty: bool = False, merge_hooks: bool = False,
        minimal_perms: bool = False) -> int:
    """Scaffold prusik into the current project.

    Three behaviors when `.claude/` already exists:

    - Default (no flags): **additive merge.** Files that don't exist yet
      are copied in; files that DO exist are left untouched. For
      `.claude/settings.json` specifically, prusik's permission entries
      are union-merged into the user's value (same surgical merge as
      `prusik refresh`); user hooks and other keys are preserved untouched.
      This is the v0.8.0 behavior — the previous "refuse unless --force"
      behavior destroyed user-authored CC config (settings, custom agents,
      session history) for any project that had touched .claude/ before.

    - `--force`: nuke `.claude/` and copy fresh. User-authored subdirs
      `conventions/` and `projects/` (Claude Code session history) are
      stashed and restored — everything else is destroyed. Use this only
      when you genuinely want a clean slate.

    - `merge_settings=False`: skip the settings.json surgical merge in the
      default path. Files still skip-on-conflict; settings.json simply
      stays exactly as the user has it, prusik permission entries not added.
      Equivalent to passing `--no-merge-additions` to `prusik refresh`.
    """
    target = Path.cwd()
    claude_dest = target / ".claude"

    # v0.52.0: pre-flight git due diligence — FAIL CLOSED on a dirty tree before
    # touching anything, so the operator never has to remember the safety gate
    # manually. (Installing over uncommitted changes makes a clean uninstall
    # unverifiable.) Warns (does not block) on a missing git repo.
    from prusik import preflight
    ok, msg = preflight.init_guard(target, allow_dirty)
    if msg:
        print(msg, file=sys.stderr if not ok else sys.stdout)
    if not ok:
        return 2

    # v0.8.4: detect project shape BEFORE scaffolding so we capture the
    # operator's pre-init state (what tools, what existing CC config,
    # etc.). Detection results go into the manifest for future drift
    # checks (prusik doctor) and inform copy-paste snippets we print at the
    # end of init.
    from prusik import detect
    detection = detect.detect_project(target)

    skipped_paths: list[str] = []
    settings_merge_summary: dict | None = None
    settings_restore: dict | None = None

    if force and claude_dest.exists():
        # User-authored subdirs to preserve across the rmtree. `conventions/`
        # is prusik's own opt-in dir for project conventions packs; `projects/`
        # is Claude Code's per-project session history (would otherwise be
        # destroyed and is unrecoverable). Add new subdirs here as the
        # need arises — better to over-preserve than to surprise.
        preserved_subdirs = ["conventions", "projects"]
        stashes: dict[str, Path] = {}
        for sub in preserved_subdirs:
            sub_path = claude_dest / sub
            if sub_path.exists():
                stash = target / f".claude.{sub}.stash"
                if stash.exists():
                    shutil.rmtree(stash)
                shutil.move(str(sub_path), str(stash))
                stashes[sub] = stash
        shutil.rmtree(claude_dest)
        copied, _ = _copy_tree_tracked(TEMPLATE_ROOT / ".claude", claude_dest, target)
        for sub, stash in stashes.items():
            shutil.move(str(stash), str(claude_dest / sub))
    else:
        copied, skipped_paths = _copy_tree_tracked(
            TEMPLATE_ROOT / ".claude", claude_dest, target
        )
        if merge_settings:
            # v0.52.1: snapshot a pre-existing settings.json BEFORE merging so
            # uninstall can revert it cleanly. Only relevant when the merge
            # actually changes a file that pre-existed (a fresh repo's
            # settings.json is template==project → no merge → nothing to record,
            # and it's tracked in files[] for normal removal instead).
            proj_settings = claude_dest / "settings.json"
            pre_text = proj_settings.read_text() if proj_settings.exists() else None
            settings_merge_summary = _maybe_merge_settings_json(
                target, merge_hooks=merge_hooks, minimal_perms=minimal_perms)
            if settings_merge_summary is not None and pre_text is not None:
                settings_restore = {
                    "premerge": pre_text,
                    "postmerge_sha": _sha256(proj_settings),
                }

    dirs_created: list[str] = []
    for d in WORKING_DIRS:
        p = target / d
        pre_existed = p.exists()
        p.mkdir(exist_ok=True)
        if not pre_existed:
            dirs_created.append(d)

    sprint_dir = target / ".sprint"
    if not sprint_dir.exists():
        sprint_dir.mkdir()
        dirs_created.append(".sprint")
    (sprint_dir / ".gitkeep").touch(exist_ok=True)

    artifact_src = TEMPLATE_ROOT / "artifacts"
    if artifact_src.exists():
        artifact_copied, artifact_skipped = _copy_tree_tracked(
            artifact_src, claude_dest / "artifact-templates", target
        )
        copied.extend(artifact_copied)
        skipped_paths.extend(artifact_skipped)

    if conventions:
        _ingest_pack(conventions, target)

    # v0.17.0 — per-stack sprint-config presets (Item 7). After the
    # generic template has been laid down, if --stack was specified AND
    # the preset exists, overlay the preset's sprint-config.yaml. Only
    # overwrites the sprint-config (not other files); engine-baked
    # invariants in phases.py apply regardless of which config is used.
    if stack:
        # v0.17.0: presets ship under .claude/sprint-config-presets/ per the
        # v0.8.10 opt-in / non-coupling invariant (no new top-level template
        # dirs — only .claude/, .sprint/, artifacts/).
        preset_path = TEMPLATE_ROOT / ".claude" / "sprint-config-presets" / f"{stack}.yaml"
        if not preset_path.exists():
            avail = sorted(
                p.stem for p in (TEMPLATE_ROOT / ".claude" / "sprint-config-presets").glob("*.yaml")
            )
            print(f"[prusik-init] unknown stack {stack!r}; available: {avail}",
                  file=sys.stderr)
            return 2
        target_sc = claude_dest / "sprint-config.yaml"
        shutil.copy2(preset_path, target_sc)
        print(f"[prusik-init] applied stack preset: {stack}")

    gitignore_added = _append_gitignore_block(target)

    # v0.13.0: single-writer manifest. created_with + surface version are
    # both this version (init genuinely deploys the current template
    # surface). detection recorded for future drift checks (v0.8.4).
    from prusik import manifest as _manifest
    manifest = _manifest.new_manifest(
        version=__version__,
        files=copied,
        directories_created=dirs_created,
        gitignore_block_added=gitignore_added,
        detection=detection,
        installed_at=datetime.now(timezone.utc).isoformat(),
        settings_restore=settings_restore,
    )
    manifest_path = _manifest.manifest_path(claude_dest)
    _manifest.save(manifest, manifest_path)

    # v0.8.4: detection summary first — operators see WHAT we found
    # before WHAT we scaffolded.
    print("[prusik-init] Detected project shape:")
    print(detect.format_summary(detection))
    print()
    print(f"[prusik-init] Initialized at {target}")
    print(f"  copied:   {len(copied)} new file(s)")
    if skipped_paths:
        print(f"  skipped:  {len(skipped_paths)} file(s) already existed (preserved)")
        for sp in skipped_paths[:10]:
            print(f"    ! {sp}")
        if len(skipped_paths) > 10:
            print(f"    ... and {len(skipped_paths) - 10} more")
        print("  (run `prusik refresh` to selectively update preserved files)")
    if settings_merge_summary:
        perm_adds = settings_merge_summary.get("permission_additions") or {}
        top_adds = settings_merge_summary.get("added_top_level_keys") or []
        bits: list[str] = []
        for sub, count in perm_adds.items():
            bits.append(f"+{count} {sub}")
        if top_adds:
            bits.append(f"+top-level: {top_adds}")
        if bits:
            print(f"  merged:   .claude/settings.json  ({', '.join(bits)})")
    # v0.53.3 (finding #5): only claim "hooks wired" when they ACTUALLY are.
    # A repo with its own `hooks` block keeps it, and without --merge-hooks
    # prusik's gate hooks are NOT wired — the scaffold lands but the FSM is
    # INERT. Saying "hooks wired" there is a false-clean; warn loudly instead.
    hooks_wired = _prusik_hooks_wired(claude_dest / "settings.json")
    print()
    if hooks_wired:
        print("  .claude/settings.json          (hooks wired to prusik gate)")
    else:
        print("  .claude/settings.json          (permissions merged — HOOKS NOT WIRED, see below)")
    print("  .claude/sprint-config.yaml     (phase FSM + permissions + triage)")
    print("  .claude/agents/*.md            (role library)")
    print("  .claude/commands/*.md          (slash commands)")
    print("  .claude/schemas/               (brief + scope schemas)")
    print("  .claude/.prusik-manifest.json  (tracks what prusik installed)")
    print(f"  {' '.join(d + '/' for d in WORKING_DIRS)} .sprint/")
    if conventions:
        print(f"  .claude/conventions/{Path(conventions).name}  (pack symlinked)")
    # v0.8.4: copy-paste snippets for things we detected but didn't
    # auto-apply. Operator can paste straight into sprint-config.yaml.
    snippets = detect.format_snippets(detection)
    if snippets:
        print()
        print(f"[prusik-init] Detected configuration suggestions "
              f"({len(snippets)}):")
        for s in snippets:
            print()
            for line in s.splitlines():
                print(f"  {line}")
        print()
        print("  These are not auto-applied — paste into "
              ".claude/sprint-config.yaml if you want them.")

    if not hooks_wired:
        print()
        print("  ⚠ HOOKS NOT WIRED — the FSM is scaffolded but INERT.")
        print("    Your .claude/settings.json already has a `hooks` block, and prusik")
        print("    does not overwrite it — so prusik's gate hooks (PreToolUse")
        print("    phase/writable gate, PostToolUse, Stop artifact gate, SessionStart")
        print("    context) are NOT active. The harness reads as installed but enforces")
        print("    nothing until they're wired.")
        print("    → Re-run `prusik init --merge-hooks` to APPEND prusik's gate hooks")
        print("      alongside your existing hooks (non-destructive, and uninstall")
        print("      reverts them cleanly). Verify with `prusik doctor`.")

    rec = preflight.branch_recommendation(target)
    if rec:
        print()
        print(f"  {rec}")
    print()
    print("Next: /brief-new <feature>   (or write briefs/<feature>.md by hand)")
    print("To pause:    prusik disable")
    print("To remove:   prusik uninstall")
    return 0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_tree_tracked(src: Path, dest: Path,
                       project_root: Path) -> tuple[list[dict], list[str]]:
    """Copy src→dest with skip-on-conflict. Returns (copied, skipped).

    - `copied`: list of {path, hash} for files newly written by this call.
    - `skipped`: list of project-relative paths where a file already
      existed and was left untouched.
    """
    tracked: list[dict] = []
    skipped: list[str] = []
    if not src.exists():
        return tracked, skipped
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        tgt = dest / rel
        if item.is_dir():
            tgt.mkdir(parents=True, exist_ok=True)
            continue
        if tgt.exists():
            skipped.append(str(tgt.relative_to(project_root)))
            continue
        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, tgt)
        tracked.append({
            "path": str(tgt.relative_to(project_root)),
            "hash": _sha256(tgt),
        })
    return tracked, skipped


def _maybe_merge_settings_json(target: Path,
                               merge_hooks: bool = False,
                               minimal_perms: bool = False) -> dict | None:
    """Surgical additive merge of `.claude/settings.json` if user has one.

    Reuses the same merge logic `prusik refresh` uses (prusik/refresh_merge.py):
    union template's `permissions.allow|deny|ask` into user's; copy in
    new top-level keys. Hooks: by default left untouched (user wins); with
    `merge_hooks`, prusik's gate hooks are surgically APPENDED alongside the
    user's so the FSM enforces (finding #5). Returns the merge summary if
    anything was added, else None.
    """
    project_settings = target / ".claude" / "settings.json"
    template_settings = TEMPLATE_ROOT / ".claude" / "settings.json"
    if not project_settings.exists() or not template_settings.exists():
        return None
    template_text = template_settings.read_text()
    project_text = project_settings.read_text()
    if template_text == project_text:
        return None  # nothing to merge

    from prusik import refresh_merge
    merged_text, summary = refresh_merge.merge_settings_json(
        template_text, project_text, merge_hooks=merge_hooks,
        minimal_perms=minimal_perms
    )
    if merged_text == project_text:
        return None  # user already has every additive entry
    project_settings.write_text(merged_text)
    return summary


def _prusik_hooks_wired(settings_path: Path) -> bool:
    """True if the final settings.json actually has prusik's gate hooks active
    (any hook command starting with 'prusik gate'). The honest signal for the
    'hooks wired' success line — a hooks-present repo without --merge-hooks
    keeps its own hooks and prusik's are NOT wired (the inert-harness case)."""
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, ValueError):
        return False
    for groups in (data.get("hooks") or {}).values():
        if not isinstance(groups, list):
            continue
        for g in groups:
            for h in (g.get("hooks") or []) if isinstance(g, dict) else []:
                if str(h.get("command", "")).startswith("prusik gate"):
                    return True
    return False


def _ingest_pack(pack: str, target: Path) -> None:
    src = Path(pack).expanduser().resolve()
    if not src.exists():
        print(f"Warning: conventions pack not found: {pack}")
        return
    dest_parent = target / ".claude" / "conventions"
    dest_parent.mkdir(parents=True, exist_ok=True)
    dest = dest_parent / src.name
    if not dest.exists():
        try:
            dest.symlink_to(src)
        except OSError:
            shutil.copytree(src, dest)

    config_path = target / ".claude" / "sprint-config.yaml"
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text()) or {}
        conv = data.setdefault("conventions", {})
        packs = conv.setdefault("packs", [])
        if src.name not in packs:
            packs.append(src.name)
        config_path.write_text(yaml.safe_dump(data, sort_keys=False))


def _append_gitignore_block(target: Path) -> bool:
    """Append a marked block of gitignore entries. Returns True if appended."""
    gi = target / ".gitignore"
    existing = gi.read_text() if gi.exists() else ""
    if GITIGNORE_BEGIN in existing:
        return False
    block = "\n".join([GITIGNORE_BEGIN] + GITIGNORE_ENTRIES + [GITIGNORE_END])
    sep = "" if existing.endswith("\n") or not existing else "\n"
    with open(gi, "a") as f:
        if existing:
            f.write(sep)
            f.write("\n")
        f.write(block + "\n")
    return True
