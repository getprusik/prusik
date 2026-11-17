#!/usr/bin/env bash
# Print the hash of the working tree as a commit of it would record: tracked + untracked,
# non-ignored files. Seeded from HEAD, because an empty index plus `git add -A` drops
# tracked files that match .gitignore (e.g. a committed reports/ file), and a clean
# checkout of such a commit could then never match its recorded suite pass.
set -euo pipefail
REPO=$(git rev-parse --show-toplevel)
idx=$(mktemp)
rm -f "$idx"
trap 'rm -f "$idx"' EXIT
cd "$REPO"
if git rev-parse -q --verify HEAD >/dev/null; then
  GIT_INDEX_FILE="$idx" git read-tree HEAD
fi
GIT_INDEX_FILE="$idx" git add -A .
GIT_INDEX_FILE="$idx" git write-tree
