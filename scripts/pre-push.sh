#!/usr/bin/env bash
# Pre-push enforcement: every commit pushed must have a tree that scripts/suite.sh passed
# locally. Twice in one session (0.244.0) a commit edited after its last suite run was pushed,
# and CI found the failure after the push. The check is by tree hash, so it is exact: any
# edit after the run blocks the push until the suite runs again.
# A tree with no stamp is still accepted when its diff from one of the last 20 passed trees is
# non-empty and touches only the prefixes declared in scripts/non-test-inputs.txt (today
# findings/), paths no test reads. Any failure to establish that is "no carry" and blocks.
set -euo pipefail
COMMON=$(git rev-parse --path-format=absolute --git-common-dir)
STAMP="$COMMON/prusik-suite-pass"
DECLARED="$(git rev-parse --show-toplevel 2>/dev/null || true)/scripts/non-test-inputs.txt"
CARRIER=""

# carried TREE: succeeds (and sets CARRIER) only when TREE differs from a recently passed tree
# solely under declared prefixes. Every error path returns 1.
carried() {
  local pushed=$1 prefixes=() line passed diff p ok pre
  [ -f "$STAMP" ] && [ -r "$STAMP" ] && [ -r "$DECLARED" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%%#*}; line=${line//[[:space:]]/}
    [ -z "$line" ] && continue
    case "$line" in /*|*//*|*/../*|../*|.) return 1 ;; */) prefixes+=("$line") ;; *) return 1 ;; esac
  done < "$DECLARED"
  [ "${#prefixes[@]}" -gt 0 ] || return 1
  diff=$(mktemp) || return 1
  while IFS= read -r passed; do
    git cat-file -e "$passed^{tree}" 2>/dev/null || continue
    git diff --no-renames --name-only -z "$passed" "$pushed" > "$diff" 2>/dev/null || continue
    [ -s "$diff" ] || continue
    ok=1
    while IFS= read -r -d '' p; do
      pre=0
      for line in "${prefixes[@]}"; do
        [ "${p#"$line"}" != "$p" ] && { pre=1; break; }
      done
      [ "$pre" = 1 ] || { ok=0; break; }
    done < "$diff"
    if [ "$ok" = 1 ]; then CARRIER=$passed; rm -f "$diff"; return 0; fi
  done < <(awk '{a[NR]=$0} END{for(i=NR;i>=1;i--) if(!s[a[i]]++){print a[i]; if(++n==20) exit}}' "$STAMP")
  rm -f "$diff"
  return 1
}

zero=0000000000000000000000000000000000000000
while read -r _lref lsha _rref _rsha; do
  [ "$lsha" = "$zero" ] && continue          # a branch delete pushes no tree
  tree=$(git rev-parse "$lsha^{tree}")
  if { [ -f "$STAMP" ] && grep -qx "$tree" "$STAMP"; }; then continue; fi
  if carried "$tree"; then
    echo "[pre-push] ${lsha:0:7} (tree ${tree:0:12}) carries the suite pass of tree ${CARRIER:0:12}:" \
         "only declared non-test-input paths changed." >&2
    continue
  fi
  echo "[pre-push] BLOCKED: ${lsha:0:7} (tree ${tree:0:12}) has no local full-suite pass." \
       "Run scripts/suite.sh on exactly this tree, then push." >&2
  exit 1
done
