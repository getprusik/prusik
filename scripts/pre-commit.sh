#!/usr/bin/env bash
# The maintainer pre-commit hook: runs each check in turn; any failure blocks the commit.
#   1. open-core boundary (adopter identity never reaches the public surface)
#   2. README release facts (Action pin and pinned-findings count match their source)
set -euo pipefail
DIR=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)
"$DIR/pre-commit-boundary.sh"
"$DIR/pre-commit-release-facts.sh"
