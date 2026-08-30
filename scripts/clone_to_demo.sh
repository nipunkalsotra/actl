#!/usr/bin/env bash
# §28 P10 instruction 9: the exact, reproducible clone-to-demo path a
# reviewer runs, timed. Fresh temporary directory, no existing virtual
# environment, no local database state, no .env, no credentials --
# simulator payments and LLM disabled throughout (`make demo` forces
# both regardless of anything inherited from the caller's shell).
#
# Usage:
#   scripts/clone_to_demo.sh [git-url-or-local-path] [branch]
#
# Defaults to this repository's own `origin` remote and its current
# branch, so a reviewer running this after a real `git push` gets a
# genuine clean-clone timing. Pass a local path (e.g. the repo's own
# working directory) only to dry-run the script's mechanics against
# already-committed history -- see the README's own "Reviewer path"
# section for why an *uncommitted* working tree can't be timed this way
# without misrepresenting the result as a true clean clone.
set -euo pipefail

SOURCE="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && git remote get-url origin 2>/dev/null || pwd)}"
BRANCH="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)}"

WORKDIR="$(mktemp -d -t actl-clone-to-demo.XXXXXX)"
cleanup() {
  echo "=== cleaning up: docker compose down, rm -rf ${WORKDIR} ==="
  (cd "${WORKDIR}/actl" && docker compose down -v) 2>/dev/null || true
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

echo "=== clone-to-demo: source=${SOURCE} branch=${BRANCH} workdir=${WORKDIR} ==="
START=$(date +%s)

git clone --branch "${BRANCH}" --single-branch "${SOURCE}" "${WORKDIR}/actl"
cd "${WORKDIR}/actl"

# No .env, no credentials, no inherited virtual environment -- a fresh
# clone has none of these by construction; asserting it here makes that
# guarantee visible rather than merely assumed.
if [ -f .env ]; then
  echo "FAIL: a fresh clone should never contain .env" >&2
  exit 1
fi
if [ -d .venv ]; then
  echo "FAIL: a fresh clone should never contain .venv" >&2
  exit 1
fi

make up
make migrate
make demo

END=$(date +%s)
ELAPSED=$((END - START))
echo
echo "=== clone-to-demo complete: ${ELAPSED}s (target: under 120s) ==="
