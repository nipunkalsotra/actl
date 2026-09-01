#!/usr/bin/env bash
# One-command reviewer/dev launcher: real backend (Postgres + Redis via
# Docker Compose, migrated and seeded) plus the buyer and merchant Vite
# UI, all with safe local defaults (PAYMENT_PROVIDER=simulator,
# LLM_ENABLED=false, ANCHOR_PROVIDER=noop) -- no product/payment/schema/
# frontend behaviour changes, this only automates what the README's
# "Setup" section already lists as separate manual steps.
#
# Companion to scripts/clone_to_demo.sh, not a replacement for it: that
# script clones into a disposable temp directory and times a from-scratch
# verification run, then tears everything down. This script runs the
# real, persistent local stack you keep working against -- see README's
# "Reviewer path" for when to use which.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_launcher_lib.sh
source "${SCRIPT_DIR}/scripts/_launcher_lib.sh"

COMPOSE_READY_TIMEOUT_S="${COMPOSE_READY_TIMEOUT_S:-90}"
SERVICE_READY_TIMEOUT_S="${SERVICE_READY_TIMEOUT_S:-60}"

check_prereqs() {
  local missing=()
  command -v docker >/dev/null 2>&1 || missing+=("docker (https://docs.docker.com/get-docker/)")
  if command -v docker >/dev/null 2>&1 && ! docker compose version >/dev/null 2>&1; then
    missing+=("docker compose v2 plugin (bundled with recent Docker Desktop/Engine)")
  fi
  command -v uv >/dev/null 2>&1 || missing+=("uv (https://docs.astral.sh/uv/)")
  command -v node >/dev/null 2>&1 || missing+=("node")
  command -v npm >/dev/null 2>&1 || missing+=("npm")
  command -v curl >/dev/null 2>&1 || missing+=("curl")

  local required_files=(
    "docker-compose.yml"
    ".env.example"
    "pyproject.toml"
    "web/package.json"
    "web/.env.example"
  )
  local f
  for f in "${required_files[@]}"; do
    [ -e "${REPO_ROOT}/${f}" ] || missing+=("missing project file: ${f}")
  done

  if [ "${#missing[@]}" -gt 0 ]; then
    echo "FAIL: cannot start -- missing prerequisites:" >&2
    printf '  - %s\n' "${missing[@]}" >&2
    exit 1
  fi
  echo "== prerequisites ok (docker, docker compose, uv, node, npm, curl, project files) =="
}

# Real secrets never pass through this function: it only copies the
# committed, placeholder-only .env.example and forces three already-safe
# fields. If .env already exists (this reviewer's own, possibly with real
# test-mode credentials), it is left completely untouched -- never read,
# never printed, never overwritten.
generate_root_env() {
  local target="${REPO_ROOT}/.env"
  if [ -f "$target" ]; then
    echo "== .env already exists -- leaving it untouched =="
    return 0
  fi
  generate_safe_env "${REPO_ROOT}/.env.example" "$target"
  echo "== generated .env from .env.example (PAYMENT_PROVIDER=simulator, LLM_ENABLED=false, ANCHOR_PROVIDER=noop; mode 600) =="
  echo "== this generated file contains no real credentials -- every value is a safe local placeholder =="
}

generate_frontend_env() {
  local target="${REPO_ROOT}/web/.env.local"
  if [ -f "$target" ]; then
    echo "== web/.env.local already exists -- leaving it untouched =="
    return 0
  fi
  cp "${REPO_ROOT}/web/.env.example" "$target"
  echo "== generated web/.env.local from web/.env.example =="
}

start_compose_services() {
  echo "== starting postgres + redis (docker compose up is a no-op if already healthy) =="
  (cd "${REPO_ROOT}" && docker compose up -d postgres redis)
  wait_compose_ready "postgres" "${COMPOSE_READY_TIMEOUT_S}" check_postgres_ready
  wait_compose_ready "redis" "${COMPOSE_READY_TIMEOUT_S}" check_redis_ready
  echo "== postgres healthy, redis healthy =="
}

run_migrate_and_seed() {
  echo "== running migrations (alembic upgrade head) =="
  (cd "${REPO_ROOT}" && uv run alembic upgrade head)
  echo "== seeding catalog (idempotent) =="
  (cd "${REPO_ROOT}" && uv run python scripts/seed.py)
}

# Two safety checks before ever starting a service: (1) an ACTL PID file
# already pointing at a live, matching process -- reuse it, never start a
# duplicate; (2) the port already bound by something this launcher did
# not start -- refuse and exit non-zero rather than kill an unrelated
# process. Only after both pass does this function actually spawn one.
start_backend() {
  mkdir -p "${RUN_DIR}"
  local existing
  if existing="$(read_pidfile "${BACKEND_PIDFILE}")" && is_actl_backend_pid "${existing}"; then
    echo "== backend already running (PID ${existing}) -- reusing =="
    return 0
  fi
  rm -f "${BACKEND_PIDFILE}"

  local owner
  owner="$(port_owner_pid "${BACKEND_PORT}")"
  if [ -n "${owner}" ]; then
    echo "FAIL: port ${BACKEND_PORT} is already in use by PID ${owner}, which this launcher did not start." >&2
    echo "       cmd: $(process_args "${owner}")" >&2
    echo "       Not killing it. If it's safe to stop, do so yourself (e.g. kill ${owner}), then re-run ./start.sh." >&2
    exit 1
  fi

  echo "== starting backend (uvicorn on 127.0.0.1:${BACKEND_PORT}, PAYMENT_PROVIDER=simulator LLM_ENABLED=false ANCHOR_PROVIDER=noop) =="
  (
    cd "${REPO_ROOT}"
    exec env ANCHOR_PROVIDER=noop LLM_ENABLED=false PAYMENT_PROVIDER=simulator \
      uv run uvicorn actl.main:app --host 127.0.0.1 --port "${BACKEND_PORT}" \
      >>"${BACKEND_LOG}" 2>&1
  ) &
  echo $! >"${BACKEND_PIDFILE}"
}

start_frontend() {
  mkdir -p "${RUN_DIR}"
  local existing
  if existing="$(read_pidfile "${FRONTEND_PIDFILE}")" && is_actl_frontend_pid "${existing}"; then
    echo "== frontend already running (PID ${existing}) -- reusing =="
    return 0
  fi
  rm -f "${FRONTEND_PIDFILE}"

  local owner
  owner="$(port_owner_pid "${FRONTEND_PORT}")"
  if [ -n "${owner}" ]; then
    echo "FAIL: port ${FRONTEND_PORT} is already in use by PID ${owner}, which this launcher did not start." >&2
    echo "       cmd: $(process_args "${owner}")" >&2
    echo "       Not killing it. If it's safe to stop, do so yourself (e.g. kill ${owner}), then re-run ./start.sh." >&2
    exit 1
  fi

  if [ ! -d "${REPO_ROOT}/web/node_modules" ]; then
    echo "== installing frontend dependencies (npm ci) =="
    (cd "${REPO_ROOT}/web" && npm ci)
  fi

  # node_modules/.bin/vite directly, not `npm run dev`: npm would be a
  # wrapper process whose child is the real vite process, so `$!` would
  # capture the wrong PID for stop.sh to manage and `kill` on it might
  # not reliably reach the child. Runs with vite.config.ts's own
  # port/strictPort settings verbatim -- nothing overridden here.
  echo "== starting frontend (vite on :${FRONTEND_PORT}) =="
  (
    cd "${REPO_ROOT}/web"
    exec node_modules/.bin/vite >>"${FRONTEND_LOG}" 2>&1
  ) &
  echo $! >"${FRONTEND_PIDFILE}"
}

wait_for_services_ready() {
  echo "== waiting for backend readiness (${BACKEND_URL}/readyz) =="
  wait_http_ready "backend" "${SERVICE_READY_TIMEOUT_S}" "${BACKEND_URL}/readyz" "${BACKEND_LOG}"
  echo "== backend ready =="

  echo "== waiting for frontend readiness (${FRONTEND_URL}/) =="
  wait_http_ready "frontend" "${SERVICE_READY_TIMEOUT_S}" "${FRONTEND_URL}/" "${FRONTEND_LOG}"
  echo "== frontend ready =="
}

print_summary() {
  cat <<EOF

ACTL is ready
Buyer:    ${FRONTEND_URL}/
Merchant: ${FRONTEND_URL}/merchant
Backend:  ${BACKEND_URL}/docs
Logs:     ./logs.sh
Stop:     ./stop.sh
EOF
}

main() {
  check_prereqs
  generate_root_env
  generate_frontend_env
  start_compose_services
  run_migrate_and_seed
  start_backend
  start_frontend
  wait_for_services_ready
  print_summary
}

main "$@"
