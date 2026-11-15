#!/usr/bin/env bash
# Pre-push enforcement: every commit pushed must have a tree that scripts/suite.sh passed
# locally. Twice in one session (0.244.0) a commit edited after its last suite run was pushed,
# and CI found the failure after the push. The check is by tree hash, so it is exact: any
# edit after the run blocks the push until the suite runs again.
set -euo pipefail
COMMON=$(git rev-parse --path-format=absolute --git-common-dir)
STAMP="$COMMON/prusik-suite-pass"
zero=0000000000000000000000000000000000000000
while read -r _lref lsha _rref _rsha; do
  [ "$lsha" = "$zero" ] && continue          # a branch delete pushes no tree
  tree=$(git rev-parse "$lsha^{tree}")
  if ! { [ -f "$STAMP" ] && grep -qx "$tree" "$STAMP"; }; then
    echo "[pre-push] BLOCKED: ${lsha:0:7} (tree ${tree:0:12}) has no local full-suite pass." \
         "Run scripts/suite.sh on exactly this tree, then push." >&2
    exit 1
  fi
done
