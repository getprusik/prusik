#!/usr/bin/env bash
# Pre-commit enforcement of the open-core boundary: BLOCK a commit that would leak
# adopter identity into the public surface. Runs the registry-driven gate
# (scripts/boundary_check.py) against the PRIVATE adopter registry.
#
# The registry lives in the private HQ repo (prusik-hq), not here, so this hook
# discovers it. Where it's found → the gate runs and a leak is fail-CLOSED-blocked.
# Where it's absent (a public/adopter clone with no sibling HQ) → the hook warns and
# allows: enforcement is only meaningful where the private registry exists, and we
# must never block an adopter's commits on a file they don't have. Install once per
# clone with scripts/install-hooks.sh.
set -euo pipefail
REPO=$(git rev-parse --show-toplevel)
REG="${PRUSIK_HQ_REGISTRY:-}"
if [ -z "$REG" ]; then
  for c in "$REPO/../prusik-hq/hq/products.local.json" "$REPO/hq/products.local.json"; do
    [ -f "$c" ] && REG="$c" && break
  done
fi
if [ -z "$REG" ] || [ ! -f "$REG" ]; then
  echo "[pre-commit] boundary gate SKIPPED — no adopter registry found (set" \
       "PRUSIK_HQ_REGISTRY or clone prusik-hq alongside this repo)." >&2
  exit 0
fi
if ! python3 "$REPO/scripts/boundary_check.py" --registry "$REG"; then
  echo "[pre-commit] BLOCKED: adopter identity in the public surface (see above)." \
       "Scrub the name (cite the finding by its fb-<id>) and re-commit." >&2
  exit 1
fi
