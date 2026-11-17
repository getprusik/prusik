#!/usr/bin/env python3
"""Regenerate prusik/_closures.json from GROUND TRUTH — run at release after adding a
moat test. Membership = the `moat-finding:` test markers (regression coverage);
versions are preserved, and a newly-marked finding is stamped the version under
development (`changelog.development_version`: the top CHANGELOG heading when it is newer
than __version__, else __version__)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prusik import changelog  # noqa: E402

root = Path(__file__).resolve().parent.parent

# Ordering guard (2026-08-04): stamping an already-released version puts new findings one
# release low (a downgrade would then false-verify a fix that isn't present). Fail closed.
# Releases are cut remotely (`gh release create`), so LOCAL tags lag; the remote is the
# instrument. Offline we can't verify: say so loudly and proceed (the release flow re-runs
# it connected).
version, state = changelog.release_state(root)
if version is None:
    sys.exit("regen refused: no __version__ in prusik/__init__.py.")
if state == "unknown":
    print(f"[regen] WARNING: cannot reach origin to verify v{version} "
          f"is unreleased — if it IS released, new findings get a stale "
          f"floor; re-run connected before shipping.", file=sys.stderr)
elif state == "released":
    sys.exit(f"regen refused: v{version} is already released — add the NEXT version's "
             f"`## [X.Y.Z]` heading to CHANGELOG.md first, then regen, so new findings "
             f"get the version their fix actually ships in.")

manifest = changelog.reconcile_closures(
    changelog.installed_closures(), changelog.scan_test_moat_markers(root), version)
(root / "prusik" / "_closures.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(f"prusik/_closures.json: {len(manifest)} moat-tested findings")
