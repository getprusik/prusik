#!/usr/bin/env bash
# Release the engine in one command: bump, full suite, commit, push, GitHub release, publish
# watch, PyPI wait, install, verify (fb-c713dc6530a9).
#
# In this repo the pin is prusik/__init__.py. Once the bump is committed, the installed global
# prusik is BEHIND the pin and every hook refuses, so a release done in separate tool calls
# locks the releasing session out until the operator hand-types the rest. Here the bump is the
# last local edit, and the only state a hook can observe is before it (pin == installed) or
# after the install (pin == installed again).
#
# Version and title come from the top `## [X.Y.Z] — title` heading of CHANGELOG.md, and the
# release notes are that section. Everything else in the release (CHANGELOG, closed findings,
# moat tags, closure rows from scripts/regen_closures.py, which stamps that same pending
# version, and the README findings count) must already be committed: the tree must be clean,
# except for an uncommitted hand-bump of the two bump files, which is redone from HEAD.
#
# Rerunning resumes. Each step is skipped when its result already exists (bump committed, HEAD
# on origin, release created, publish run green, wheel on PyPI, installed). If a step fails
# after the bump, the hooks refuse every tool call except this script: the pre-tool hook lets
# a Bash call that is exactly scripts/release.sh through (fb-b071cab60a5b), so the releasing
# session resumes the release itself.
set -euo pipefail
REPO=$(git rev-parse --show-toplevel)
cd "$REPO"
say() { echo "[release] $*" >&2; }
die() { say "FAILED: $*"; exit 1; }

[ "$(git rev-parse --git-dir)" = "$(git rev-parse --git-common-dir)" ] \
  || die "run from the main checkout, not a worktree."
[ "$(git branch --show-current)" = "main" ] || die "not on main."

heading=$(grep -m1 -E '^## \[[0-9]+\.[0-9]+\.[0-9]+\] — ' CHANGELOG.md) \
  || die "CHANGELOG.md has no '## [X.Y.Z] — title' heading."
VERSION=$(sed -E 's/^## \[([0-9.]+)\] — .*/\1/' <<<"$heading")
TITLE=$(sed -E 's/^## \[[0-9.]+\] — //' <<<"$heading")
TAG="v$VERSION"
# Whether the bump happened is a fact about HEAD, not the working tree: an operator's
# uncommitted hand-bump is not a bump (fb-65f7eb5ea11b).
current=$(git show HEAD:prusik/__init__.py | sed -nE 's/^__version__ = "([^"]+)"/\1/p')
[ -n "$current" ] || die "no __version__ in HEAD's prusik/__init__.py."

newer() {  # newer A B: A > B as X.Y.Z
  [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)" = "$1" ]
}

# The installed prusik is the one bare `prusik` runs: the interpreter on its shebang line,
# resolved here, never a guess.
bin=$(command -v prusik) || die "no prusik on PATH."
PY=$(head -1 "$bin" | sed -nE 's/^#!([^ ]+python[0-9.]*)$/\1/p')
[ -n "$PY" ] && [ -x "$PY" ] || die "cannot read the interpreter from $bin's shebang."

# The bump: the one definition of what the release changes in each of its two files, used both
# to make it and to recognise it.
BUMPED=(prusik/__init__.py README.md)
bump() {  # bump FILE: stdin is FILE's content, stdout is that content bumped to $VERSION
  case "$1" in
    prusik/__init__.py) sed -E "s/^__version__ = \"[^\"]+\"/__version__ = \"$VERSION\"/" ;;
    README.md) sed -E "s#getprusik/prusik@v[0-9]+\.[0-9]+\.[0-9]+#getprusik/prusik@$TAG#g" ;;
    *) return 1 ;;
  esac
}

# only_bump: every dirty path is a bump file whose index and working-tree content are each
# HEAD's content or HEAD's content bumped. Anything else is the operator's and is never touched.
only_bump() (  # a subshell, so its EXIT trap removes the scratch dir on every return
  t=$(mktemp -d)
  trap 'rm -rf "$t"' EXIT
  while IFS= read -r f; do
    case " ${BUMPED[*]} " in *" $f "*) ;; *) exit 1 ;; esac
  done < <(git status --porcelain --untracked-files=all | cut -c4-)
  for f in "${BUMPED[@]}"; do
    git show "HEAD:$f" >"$t/head"
    bump "$f" <"$t/head" >"$t/bumped"
    git show ":$f" >"$t/index"
    for c in "$t/index" "$f"; do
      cmp -s "$c" "$t/head" || cmp -s "$c" "$t/bumped" || exit 1
    done
  done
)

# 1. bump + suite + commit
if [ "$current" != "$VERSION" ]; then
  newer "$VERSION" "$current" || die "CHANGELOG's $VERSION is not newer than $current."
  if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    only_bump || die "the tree is not clean; commit the release content first."
    say "the tree holds an uncommitted bump to $VERSION; redoing it from HEAD"
    git checkout -q HEAD -- "${BUMPED[@]}"
  fi
  git fetch -q origin main
  git merge-base --is-ancestor origin/main HEAD || die "HEAD does not contain origin/main."
  say "bump $current -> $VERSION"
  for f in "${BUMPED[@]}"; do
    git show "HEAD:$f" | bump "$f" >"$f"
  done
  if ! scripts/suite.sh; then
    git checkout -q HEAD -- "${BUMPED[@]}"
    die "suite failed on the bumped tree; bump reverted, nothing committed."
  fi
  git add "${BUMPED[@]}"
  git commit -q -m "$VERSION: $TITLE"
fi
SHA=$(git log -1 --format=%H -- prusik/__init__.py)
[ "$(git show "$SHA:prusik/__init__.py" | sed -nE 's/^__version__ = "([^"]+)"/\1/p')" = "$VERSION" ] \
  || die "the last commit touching prusik/__init__.py is not the $VERSION bump."

# 2. push
git fetch -q origin main
if ! git merge-base --is-ancestor "$SHA" origin/main; then
  say "push"
  git push -q origin main
fi

# 3. GitHub release (its publication triggers publish.yml)
if ! gh release view "$TAG" >/dev/null 2>&1; then
  say "create release $TAG"
  notes=$(awk -v h="$heading" '$0==h{on=1;next} on&&/^## \[/{exit} on' CHANGELOG.md)
  gh release create "$TAG" --target "$SHA" --title "$TAG — $TITLE" --notes "$notes" >/dev/null
fi

# 4. the publish run for this tag
run=""
for _ in $(seq 60); do
  run=$(gh run list --workflow publish.yml --event release --limit 20 \
        --json databaseId,headBranch -q ".[] | select(.headBranch==\"$TAG\") | .databaseId" | head -1)
  [ -n "$run" ] && break
  sleep 5
done
[ -n "$run" ] || die "no publish run appeared for $TAG within 5 minutes."
# The run's own status and conclusion decide, never gh's exit code: `gh run watch
# --exit-status` also exits non-zero when gh cannot read the run (a jobs-API HTTP 500 in the
# 0.270.0 release), and an unreadable run is not a failed one (fb-78e2394070e5).
say "watch publish run $run"
unread=0
while :; do
  if state=$(gh run view "$run" --json status,conclusion -q '.status+" "+.conclusion' 2>/dev/null); then
    unread=0
    case "$state" in
      "completed success") break ;;
      completed*) die "publish run $run failed (conclusion ${state#completed }; gh run view $run --log-failed)." ;;
    esac
  else
    unread=$((unread + 1))
    [ "$unread" -lt 10 ] || die "publish run $run: state unknown (gh could not read it $unread times in a row); rerun scripts/release.sh to resume."
  fi
  sleep "${RELEASE_POLL_SECONDS:-15}"
done

# 5. PyPI has the version (the JSON API lags the upload by up to a few minutes)
for i in $(seq 90); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "https://pypi.org/pypi/prusik/$VERSION/json") || code=000
  [ "$code" = 200 ] && break
  [ "$i" = 90 ] && die "prusik $VERSION not on PyPI after 15 minutes (last HTTP $code)."
  sleep 10
done

# 6. install into the interpreter bare prusik runs, then verify
if [ "$(prusik --version 2>/dev/null)" != "prusik $VERSION" ]; then
  say "install prusik==$VERSION into $PY"
  for i in $(seq 10); do
    "$PY" -m pip install -q -U --no-cache-dir "prusik==$VERSION" && break
    [ "$i" = 10 ] && die "pip could not install prusik==$VERSION."
    sleep 30
  done
fi
got=$(prusik --version)
[ "$got" = "prusik $VERSION" ] || die "bare prusik reports '$got', expected prusik $VERSION."
say "released $TAG: pushed, published, installed ($bin)."
