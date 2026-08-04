#!/usr/bin/env python3
"""Regenerate prusik/_closures.json from GROUND TRUTH — run at release after adding a
moat test. Membership = the `moat-finding:` test markers (regression coverage);
versions are preserved, and a newly-marked finding is stamped the current __version__.
CHANGELOG-independent, so it works in the public-canonical engine repo."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from prusik import __version__, changelog  # noqa: E402

root = Path(__file__).resolve().parent.parent

# Ordering guard (2026-08-04): a newly-marked finding is stamped __version__,
# so regen MUST run AFTER the release bump — regenerating while __version__ is
# already tagged/released stamps new findings one release low (a downgrade
# would then false-verify a fix that isn't present). Fail closed.
import subprocess
tagged = subprocess.run(["git", "-C", str(root), "tag", "-l", f"v{__version__}"],
                        capture_output=True, text=True).stdout.strip()
if tagged:
    sys.exit(f"regen refused: v{__version__} is already released — bump "
             f"prusik/__init__.py to the NEXT version first, then regen, so "
             f"new findings get the version their fix actually ships in.")

manifest = changelog.reconcile_closures(
    changelog.installed_closures(), changelog.scan_test_moat_markers(root), __version__)
(root / "prusik" / "_closures.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(f"prusik/_closures.json: {len(manifest)} moat-tested findings")
