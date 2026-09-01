#!/usr/bin/env bash
# Compact status table for the local ACTL stack -- Postgres/Redis compose
# health, backend/frontend PID + running state + URL health, and any
# port occupied by a process this launcher doesn't own.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_launcher_lib.sh
source "${SCRIPT_DIR}/scripts/_launcher_lib.sh"

row() {
  printf '%-10s %-9s %-8s %s\n' "$1" "$2" "$3" "$4"
}

report_compose_service() {
  local name="$1" check_fn="$2"
  if "${check_fn}"; then
    row "${name}" "healthy" "-" "docker compose"
  else
    row "${name}" "down" "-" "docker compose"
  fi
}

report_app_service() {
  local name="$1" pidfile="$2" match_fn="$3" url="$4" port="$5"
  local pid state detail

  if pid="$(read_pidfile "${pidfile}")" && "${match_fn}" "${pid}"; then
    state="running"
  else
    pid="-"
    state="stopped"
    local owner
    owner="$(port_owner_pid "${port}")"
    if [ -n "${owner}" ]; then
      row "${name}" "${state}" "${pid}" "port ${port} in use by untracked PID ${owner}"
      return 0
    fi
  fi

  if curl -fsS "${url}" >/dev/null 2>&1; then
    detail="${url} ok"
  else
    detail="${url} unreachable"
  fi
  row "${name}" "${state}" "${pid}" "${detail}"
}

main() {
  echo "ACTL status"
  echo
  row "SERVICE" "STATE" "PID" "DETAIL"
  report_compose_service "postgres" check_postgres_ready
  report_compose_service "redis" check_redis_ready
  report_app_service "backend" "${BACKEND_PIDFILE}" is_actl_backend_pid "${BACKEND_URL}/readyz" "${BACKEND_PORT}"
  report_app_service "frontend" "${FRONTEND_PIDFILE}" is_actl_frontend_pid "${FRONTEND_URL}/" "${FRONTEND_PORT}"
}

main "$@"
