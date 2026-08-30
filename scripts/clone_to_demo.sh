#!/usr/bin/env bash
# §28 P10 instruction 9 / release-readiness correction: the exact,
# reproducible clone-to-demo path a reviewer runs, timed. Fresh temporary
# directory, no existing virtual environment, no local database state, no
# inherited .env, no real credentials -- simulator payments and LLM
# disabled throughout, isolated from any ACTL stack the caller already
# has running locally (own Compose project, own dynamic host ports via
# POSTGRES_HOST_PORT/REDIS_HOST_PORT=0 -- see docker-compose.yml -- own
# generated .env, never the caller's own .env).
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
#
# Functions below are extracted so tests/unit/test_clone_to_demo_script.py
# can source this file (guarded by the BASH_SOURCE check at the bottom)
# and exercise the .env-generation, project-naming, and bounded-timeout
# logic directly, without cloning a repo or touching real Docker.
set -euo pipefail

READY_TIMEOUT_S="${CLONE_TO_DEMO_READY_TIMEOUT_S:-90}"

# WORKDIR/RUN_PROJECT are deliberately *not* `local` to any function: the
# EXIT trap must still see them after an `errexit`-triggered abort deep
# inside main() unwinds that call frame -- a `local` in main() plus a
# `cleanup` closure defined inside it was tried first and failed with
# "run_project: unbound variable" the moment a mid-main command failed,
# because bash discards main()'s locals before running the EXIT trap.
WORKDIR=""
RUN_PROJECT=""

cleanup() {
  echo "=== cleaning up: docker compose (project ${RUN_PROJECT:-unknown}) down -v, rm -rf ${WORKDIR:-<none>} ==="
  if [ -n "${WORKDIR}" ]; then
    (cd "${WORKDIR}/actl" 2>/dev/null && docker compose down -v) 2>/dev/null || true
    rm -rf "${WORKDIR}"
  fi
}
# Armed inside main(), not here at source/parse time -- sourcing this
# file (as tests/unit/test_clone_to_demo_script.py does, to call the
# functions above directly) must not register a cleanup trap that fires
# on the *test's* shell exit and prints noise WORKDIR never set up.

# Never the caller's own .env (a fresh clone has none, by construction --
# see the assertion below) -- always regenerated from the committed
# .env.example, with reviewer-safe values forced regardless of what
# .env.example itself currently defaults to.
generate_reviewer_env() {
  local example="$1" target="$2"
  cp "${example}" "${target}"
  sed -i \
    -e 's/^PAYMENT_PROVIDER=.*/PAYMENT_PROVIDER=simulator/' \
    -e 's/^LLM_ENABLED=.*/LLM_ENABLED=false/' \
    "${target}"
  chmod 600 "${target}"
}

# A fixed "actl" directory/project name risks colliding with an already-
# running local ACTL stack (same container names, same named volume). The
# mktemp workdir's own random suffix is already a unique run id -- reuse
# it verbatim as the Compose project name instead of minting a second one.
derive_compose_project_name() {
  basename "$1" | tr '[:upper:].' '[:lower:]-'
}

# Bounded wait: print `docker compose ps` and recent logs and exit
# non-zero on timeout, instead of the previous unbounded `until ... done`
# loop that could hang forever against a container that never becomes
# healthy.
wait_ready() {
  local desc="$1" timeout_s="$2"
  shift 2
  local deadline=$((SECONDS + timeout_s))
  until "$@" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      echo "FAIL: ${desc} did not become ready within ${timeout_s}s" >&2
      echo "--- docker compose ps ---" >&2
      docker compose ps >&2 || true
      echo "--- docker compose logs (tail) ---" >&2
      docker compose logs --tail=50 >&2 || true
      return 1
    fi
    sleep 1
  done
}

main() {
  local source branch
  source="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && git remote get-url origin 2>/dev/null || pwd)}"
  branch="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)}"

  WORKDIR="$(mktemp -d -t actl-clone-to-demo.XXXXXX)"
  RUN_PROJECT="$(derive_compose_project_name "${WORKDIR}")"
  export COMPOSE_PROJECT_NAME="${RUN_PROJECT}"
  trap cleanup EXIT

  echo "=== clone-to-demo: source=${source} branch=${branch} workdir=${WORKDIR} project=${RUN_PROJECT} ==="
  local start
  start=$(date +%s)

  git clone --branch "${branch}" --single-branch "${source}" "${WORKDIR}/actl"
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

  generate_reviewer_env .env.example .env
  echo "=== generated reviewer .env (PAYMENT_PROVIDER=simulator, LLM_ENABLED=false, mode 600, no real credentials) ==="

  # Dynamic host ports -- avoids colliding with an already-running local
  # ACTL stack's fixed 5432/6379 bindings. "0" asks the OS to assign a
  # free ephemeral port (docker-compose.yml's POSTGRES_HOST_PORT/
  # REDIS_HOST_PORT interpolation, defaulting to the fixed ports for the
  # normal, single-instance `make up` dev workflow). A generated Compose
  # override file was tried first and rejected: Compose merges `ports:`
  # lists by concatenation across files, not replacement, so the override
  # added a second port mapping instead of replacing the fixed one, and
  # the original fixed-port bind was still attempted and still collided.
  export POSTGRES_HOST_PORT=0
  export REDIS_HOST_PORT=0

  echo "=== compose project: ${RUN_PROJECT} (isolated from any existing ACTL stack) ==="
  docker compose up -d postgres redis

  wait_ready "postgres" "${READY_TIMEOUT_S}" docker compose exec -T postgres pg_isready -U actl -d actl
  wait_ready "redis" "${READY_TIMEOUT_S}" docker compose exec -T redis redis-cli ping
  echo "postgres healthy, redis healthy"

  # This clone's own generated .env must point at the dynamically-assigned
  # host ports above, not the fixed 5432/6379 docker-compose.yml defaults
  # to -- migrate/demo below connect from the host, not from inside a
  # container, so DATABASE_URL/REDIS_URL are what they actually use.
  local pg_port redis_port
  pg_port="$(docker compose port postgres 5432 | head -n1 | awk -F: '{print $NF}')"
  redis_port="$(docker compose port redis 6379 | head -n1 | awk -F: '{print $NF}')"
  sed -i \
    -e "s#^DATABASE_URL=.*#DATABASE_URL=postgresql+asyncpg://actl:actl@localhost:${pg_port}/actl#" \
    -e "s#^REDIS_URL=.*#REDIS_URL=redis://localhost:${redis_port}/0#" \
    .env
  echo "=== isolated ports: postgres=${pg_port} redis=${redis_port} (independent of any existing ACTL stack) ==="

  make migrate
  make demo

  local end elapsed
  end=$(date +%s)
  elapsed=$((end - start))
  echo
  echo "=== clone-to-demo complete: ${elapsed}s (target: under 120s) ==="
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
