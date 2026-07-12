"""`prusik uninstall` — remove only what prusik installed.

Reads `.claude/.prusik-manifest.json` and deletes those files, unwinds the
gitignore block, and removes empty working directories. User-added agents,
custom commands, and hand-edited configs are preserved.

Files whose content has drifted from the tracked hash are skipped unless
`--force` is passed, so an accidental `prusik uninstall` can't wipe out a
sprint-config the user spent time tuning.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from prusik.init import GITIGNORE_BEGIN, GITIGNORE_END


def run(keep_artifacts: bool = False, force: bool = False) -> int:
    target = Path.cwd()
    from prusik import manifest as _manifest
    claude_dir = target / ".claude"
    manifest_path = _manifest.find_manifest(claude_dir)

    if manifest_path is None:
        print(f"[prusik-uninstall] no manifest in {claude_dir}")
        print("  either prusik is not installed here, or it predates manifest support.")
        print("  fall back: manually remove .claude/ and .sprint/ if you're sure.")
        return 1

    manifest = _manifest.load(manifest_path)  # migration-safe; files[] preserved
    if manifest is None:
        print(f"[prusik-uninstall] unreadable manifest at {manifest_path}")
        return 1
    removed: list[str] = []
    skipped_modified: list[str] = []
    missing: list[str] = []

    for entry in manifest.get("files", []):
        rel = entry["path"]
        p = target / rel
        if not p.exists():
            missing.append(rel)
            continue
        current = _sha256(p)
        if current != entry["hash"] and not force:
            skipped_modified.append(rel)
            continue
        p.unlink()
        removed.append(rel)

    # v0.52.1: restore a pre-merge settings.json. When init merges prusik's
    # permissions into an EXISTING settings.json, the file isn't tracked in
    # files[] (it pre-existed), so the loop above never touches it — without this
    # it keeps prusik's additions (residue). Revert to the exact pre-merge
    # content, but ONLY if settings.json is unchanged since prusik merged; if the
    # user hand-edited it since, skip + warn rather than clobber (same safety as
    # the drifted-file rule above; --force overrides).
    settings_restored = False
    settings_skipped = False
    sr = manifest.get("settings_restore")
    if sr:
        sp = target / ".claude" / "settings.json"
        if sp.exists():
            if force or _sha256(sp) == sr.get("postmerge_sha"):
                sp.write_text(sr["premerge"])
                settings_restored = True
            else:
                settings_skipped = True

    if manifest.get("gitignore_block_added"):
        if _remove_gitignore_block(target / ".gitignore"):
            print("  gitignore block removed")

    emptied: list[str] = []
    for d in manifest.get("directories_created", []):
        p = target / d
        if p.exists():
            if not any(p.iterdir()):
                p.rmdir()
                emptied.append(d)

    _remove_empty_parents(target / ".claude")

    if not keep_artifacts:
        sprint = target / ".sprint"
        if sprint.exists():
            shutil.rmtree(sprint)
            emptied.append(".sprint/")

    # Keep the manifest if anything was left behind — the user may want to
    # retry with --force. Remove it only when the uninstall is fully clean.
    if not skipped_modified and not settings_skipped:
        manifest_path.unlink()
        if claude_dir.exists() and not any(claude_dir.iterdir()):
            claude_dir.rmdir()

    print(f"[prusik-uninstall] removed {len(removed)} file(s), {len(emptied)} dir(s)")
    if settings_restored:
        print("  .claude/settings.json restored to its pre-prusik content")
    if missing:
        print(f"  {len(missing)} tracked file(s) already gone")
    if skipped_modified:
        print(f"  {len(skipped_modified)} file(s) left in place (modified since install):")
        for f in skipped_modified:
            print(f"    - {f}")
        print("  manifest kept; rerun with --force to remove them too")
    if settings_skipped:
        print("  .claude/settings.json left as-is (hand-edited since prusik "
              "merged) — prusik's permission additions remain.")
        print("    manifest kept; rerun with --force to revert it anyway")

    if keep_artifacts:
        print("  .sprint/ kept (--keep-artifacts)")

    return 0


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _remove_gitignore_block(path: Path) -> bool:
    if not path.exists():
        return False
    lines = path.read_text().splitlines(keepends=False)
    try:
        start = lines.index(GITIGNORE_BEGIN)
        end = lines.index(GITIGNORE_END, start)
    except ValueError:
        return False
    # Also eat the blank line immediately preceding the block if present.
    pre_trim = start - 1 if start > 0 and lines[start - 1] == "" else start
    new_lines = lines[:pre_trim] + lines[end + 1:]
    # Collapse consecutive empty lines introduced by the removal.
    cleaned: list[str] = []
    prev_blank = False
    for line in new_lines:
        if line == "":
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        cleaned.append(line)
    text = "\n".join(cleaned)
    if cleaned:
        text += "\n"
    if text.strip():
        path.write_text(text)
    else:
        path.unlink()
    return True


def _remove_empty_parents(dir_path: Path) -> None:
    """Remove empty subdirectories of dir_path (bottom-up)."""
    if not dir_path.exists() or not dir_path.is_dir():
        return
    for sub in sorted(dir_path.rglob("*"), reverse=True):
        if sub.is_dir() and not any(sub.iterdir()):
            sub.rmdir()
