#!/usr/bin/env bash
# Stops only the backend/frontend processes this launcher started and
# recorded in .run/ -- never an unrelated process that merely happens to
# occupy the same PID number after a reboot/crash (a stale or
# non-matching PID file is removed, not acted on). Postgres/Redis are
# left running by default (they may be shared with other local work);
# pass --down to also stop this project's Compose services.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_launcher_lib.sh
source "${SCRIPT_DIR}/scripts/_launcher_lib.sh"

stop_service() {
  local name="$1" pidfile="$2" match_fn="$3" pid
  if ! pid="$(read_pidfile "${pidfile}")"; then
    echo "== ${name}: no PID file, nothing to stop =="
    return 0
  fi
  if ! "${match_fn}" "${pid}"; then
    echo "== ${name}: PID file (${pid}) is stale or no longer an ACTL ${name} process -- removing, not killing =="
    rm -f "${pidfile}"
    return 0
  fi

  echo "== stopping ${name} (PID ${pid}) =="
  kill "${pid}" 2>/dev/null || true
  local waited=0
  while pid_alive "${pid}" && [ "${waited}" -lt 10 ]; do
    sleep 1
    waited=$((waited + 1))
  done
  if pid_alive "${pid}"; then
    echo "== ${name} did not stop within 10s, sending SIGKILL =="
    kill -9 "${pid}" 2>/dev/null || true
  fi
  rm -f "${pidfile}"
  echo "== ${name} stopped =="
}

compose_down() {
  echo "== stopping ACTL's postgres/redis compose services (--down requested) =="
  (cd "${REPO_ROOT}" && docker compose down)
}

main() {
  stop_service "backend" "${BACKEND_PIDFILE}" is_actl_backend_pid
  stop_service "frontend" "${FRONTEND_PIDFILE}" is_actl_frontend_pid

  if [ "${1:-}" = "--down" ]; then
    compose_down
  else
    echo "== postgres/redis left running (pass --down to also stop them) =="
  fi
}

main "$@"
