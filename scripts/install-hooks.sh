#!/usr/bin/env bash
# Install maintainer git hooks. Run once per clone (hooks live in .git/, which is not
# tracked, so a fresh clone starts without them — a real gap that leaked adopter names
# into public identifiers before this existed). The pre-commit hook (scripts/pre-commit.sh)
# runs the open-core boundary gate and the README release-facts check.
set -euo pipefail
# Hooks live in the COMMON git dir, shared by the main tree and every linked worktree
# (in a worktree `.git` is a file, so `$REPO/.git/hooks` does not exist). The link
# targets the main checkout's script, which is the parent of the common dir.
COMMON=$(git rev-parse --path-format=absolute --git-common-dir)
MAIN=$(dirname "$COMMON")
chmod +x "$MAIN/scripts/pre-commit.sh" "$MAIN/scripts/pre-commit-boundary.sh" \
  "$MAIN/scripts/pre-commit-release-facts.sh"
mkdir -p "$COMMON/hooks"
ln -sf "$MAIN/scripts/pre-commit.sh" "$COMMON/hooks/pre-commit"
echo "installed: .git/hooks/pre-commit -> scripts/pre-commit.sh"
echo "every commit now runs the boundary gate and the README release-facts check."
