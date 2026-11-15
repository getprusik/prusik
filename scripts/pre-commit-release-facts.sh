#!/usr/bin/env bash
# Pre-commit enforcement of the README's release-derived facts: the Action example pins
# v<prusik.__version__>, and the pinned-findings count equals len(prusik/_closures.json).
# tests/test_readme_claims.py is the one check of both. It used to run only in CI, after the
# push, so a version bump that missed the README pin reached main and failed there (0.244.0).
# Here it runs whenever a commit stages a file those facts derive from, and blocks on failure.
set -euo pipefail
REPO=$(git rev-parse --show-toplevel)
staged=$(git diff --cached --name-only)
if ! grep -qxE 'README\.md|prusik/__init__\.py|prusik/_closures\.json' <<<"$staged"; then
  exit 0
fi
# The test reads the working tree, so the committed content must equal it for these files.
if ! git diff --quiet -- README.md prusik/__init__.py prusik/_closures.json; then
  echo "[pre-commit] BLOCKED: README.md, prusik/__init__.py or prusik/_closures.json has" \
       "unstaged changes; stage them so the release-facts check sees what is committed." >&2
  exit 1
fi
if ! (cd "$REPO" && uv run --quiet python -m pytest -q -p no:cacheprovider \
      tests/test_readme_claims.py >/dev/null 2>&1); then
  (cd "$REPO" && uv run --quiet python -m pytest -q -p no:cacheprovider \
      tests/test_readme_claims.py 2>&1 | grep -E "AssertionError|FAILED" | head -4) >&2
  echo "[pre-commit] BLOCKED: README release facts disagree with their source (above)." >&2
  exit 1
fi
