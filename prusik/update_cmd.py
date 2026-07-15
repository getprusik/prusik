"""`prusik update` (v0.84.0) — the one command that closes the multi-host update
loop. A prusik update is THREE parts and the third is easy to forget:

  1. the PACKAGE (engine)        — `pipx upgrade prusik` / `pip install -U …`
  2. the TEMPLATES in YOUR repo  — `prusik refresh` (agents/commands copied at
                                    init don't auto-update with the package)
  3. the running SESSION         — restart (Agent-tool dispatch caches the
                                    agent registry at start; finding #13)

This command checks the version, and — because a refresh against a stale package
would sync stale templates — only syncs templates once the package is current,
otherwise it prints the exact upgrade command and asks you to re-run. Pull-based:
it never upgrades the package itself (a self-upgrade mid-run is fragile and
can't reliably know how you installed it).
"""

from __future__ import annotations

import re
from pathlib import Path

_SECTION = re.compile(r"^##\s+\[(\d+\.\d+\.\d+)\][^\n]*\n(.*?)(?=^##\s+\[|\Z)",
                      re.M | re.S)
_HEADLINE = re.compile(r"\*\*(.+?)\*\*", re.S)
_FB_ID = re.compile(r"\bfb-[0-9a-f]{12}\b")


def _vkey(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def _whats_new(changelog: str, installed: str, latest: str | None,
               root: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Parse the CHANGELOG into (version, one-line headline) for releases newer
    than `installed` (up to `latest`), plus the adopter's OWN findings these
    releases close — cross-referenced against their local feedback (the loop
    closing in-product). Best-effort; never raises."""
    iv = _vkey(installed)
    lv = _vkey(latest) if latest else None
    mine: dict[str, str] = {}
    try:
        from prusik import feedback
        mine = {f["id"]: f.get("title", "") for f in feedback.load(root)}
    except Exception:  # noqa: BLE001 — the nicety must never break update
        pass
    new: list[tuple[str, str]] = []
    resolved: list[tuple[str, str]] = []
    for m in _SECTION.finditer(changelog):
        ver, body = m.group(1), m.group(2)
        try:
            vv = _vkey(ver)
        except ValueError:
            continue
        if vv <= iv or (lv and vv > lv):
            continue
        hl = _HEADLINE.search(body)
        headline = (hl.group(1) if hl else body.strip().split("\n", 1)[0])
        new.append((ver, " ".join(headline.split())[:96]))
        for fid in _FB_ID.findall(body):
            if fid in mine:
                resolved.append((mine[fid], ver))
    return new, resolved


def _install_kind() -> tuple[str, str]:
    """(kind, upgrade-command-hint). 'editable' when prusik runs from a source
    checkout (our dev loop); 'installed' otherwise."""
    import prusik
    path = str(Path(prusik.__file__).resolve())
    if "site-packages" not in path and "dist-packages" not in path:
        repo = str(Path(prusik.__file__).resolve().parent.parent)
        return "editable", f"git -C {repo} pull"
    return ("installed",
            "pipx upgrade prusik   # or: pip install -U prusik")


def run(timeout: float = 3.0) -> int:
    from prusik import version_check
    installed, latest, newer = version_check.check(timeout)
    kind, upgrade = _install_kind()
    print(f"[prusik update] installed {installed} ({kind} install)")

    if newer:
        print(f"  ↑ a newer release is available: {latest}")
        # B4 — what's new between installed and latest, from the repo CHANGELOG;
        # plus any of THIS project's filed findings these releases closed.
        try:
            cl = version_check.changelog_text(timeout)
            if cl:
                from prusik import ledger
                new, resolved = _whats_new(cl, installed, latest,
                                           ledger.project_root())
                if new:
                    print(f"\n  What's new since {installed}:")
                    for ver, hl in new[:8]:
                        print(f"    • v{ver} — {hl}")
                    if len(new) > 8:
                        print(f"    … +{len(new) - 8} more (see CHANGELOG.md)")
                for title, ver in resolved:
                    print(f"    ✓ your reported finding \"{title[:56]}\" "
                          f"shipped in v{ver}")
                print()
        except Exception:  # noqa: BLE001 — never let the nicety break update
            pass
        print(f"  1. upgrade the package:  {upgrade}")
        print("  2. then re-run `prusik update` to sync this project's templates.")
        print("  3. then restart your Claude Code session.")
        return 0

    if latest is None:
        print("  (couldn't reach GitHub to check for a newer release — offline or "
              "rate-limited; syncing local templates anyway.)")
    else:
        print(f"  ✓ package is current (latest release: {latest}).")

    # Package is current (or unknown) → bring THIS project's templates up to the
    # installed package, then the only part prusik can't do for you.
    print("  syncing project templates…")
    from prusik import refresh
    rc = refresh.run()
    print("  → restart your Claude Code session to pick up agent/command changes "
          "(`/agents` only reloads the interactive picker, not Agent-tool dispatch).")
    return rc if isinstance(rc, int) else 0
