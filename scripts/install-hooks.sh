#!/usr/bin/env bash
# Install maintainer git hooks. Run once per clone (hooks live in .git/, which is not
# tracked, so a fresh clone starts without them — a real gap that leaked adopter names
# into public identifiers before this existed). Currently: the open-core boundary gate
# as a pre-commit hook.
set -euo pipefail
REPO=$(git rev-parse --show-toplevel)
chmod +x "$REPO/scripts/pre-commit-boundary.sh"
ln -sf ../../scripts/pre-commit-boundary.sh "$REPO/.git/hooks/pre-commit"
echo "installed: .git/hooks/pre-commit -> scripts/pre-commit-boundary.sh"
echo "the boundary gate now runs on every commit (blocks adopter-identity leaks)."
