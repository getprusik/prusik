#!/usr/bin/env bash
# Install maintainer git hooks. Run once per clone (hooks live in .git/, which is not
# tracked, so a fresh clone starts without them — a real gap that leaked adopter names
# into public identifiers before this existed). Currently: the open-core boundary gate
# as a pre-commit hook.
set -euo pipefail
# Hooks live in the COMMON git dir, shared by the main tree and every linked worktree
# (in a worktree `.git` is a file, so `$REPO/.git/hooks` does not exist). The link
# targets the main checkout's script, which is the parent of the common dir.
COMMON=$(git rev-parse --path-format=absolute --git-common-dir)
MAIN=$(dirname "$COMMON")
chmod +x "$MAIN/scripts/pre-commit-boundary.sh"
mkdir -p "$COMMON/hooks"
ln -sf "$MAIN/scripts/pre-commit-boundary.sh" "$COMMON/hooks/pre-commit"
echo "installed: .git/hooks/pre-commit -> scripts/pre-commit-boundary.sh"
echo "the boundary gate now runs on every commit (blocks adopter-identity leaks)."
