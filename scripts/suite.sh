#!/usr/bin/env bash
# Run the full test suite. On a pass, record the hash of the exact tree it ran against
# (tracked + untracked, non-ignored files) so the pre-push hook can confirm a pushed commit
# is that tree. Any edit after the run changes the hash, so a stale pass never counts.
set -euo pipefail
REPO=$(git rev-parse --show-toplevel)
COMMON=$(git rev-parse --path-format=absolute --git-common-dir)
tree_hash() {
  local idx; idx=$(mktemp)
  rm -f "$idx"
  # Seed from HEAD: an empty index would drop tracked files that match .gitignore
  # (e.g. a committed reports/ file), so a clean checkout could never match its commit.
  (cd "$REPO" && GIT_INDEX_FILE="$idx" git read-tree HEAD \
     && GIT_INDEX_FILE="$idx" git add -A . && GIT_INDEX_FILE="$idx" git write-tree)
  rm -f "$idx"
}
before=$(tree_hash)
(cd "$REPO" && uv run python -m pytest -q "$@")
after=$(tree_hash)
if [ "$before" != "$after" ]; then
  echo "[suite] the tree changed during the run; not recording a pass." >&2
  exit 1
fi
echo "$after" >> "$COMMON/prusik-suite-pass"
echo "[suite] pass recorded for tree $after"
