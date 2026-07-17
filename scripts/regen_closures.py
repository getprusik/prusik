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
manifest = changelog.reconcile_closures(
    changelog.installed_closures(), changelog.scan_test_moat_markers(root), __version__)
(root / "prusik" / "_closures.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(f"prusik/_closures.json: {len(manifest)} moat-tested findings")
