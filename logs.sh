#!/usr/bin/env bash
# Tails ACTL's own backend/frontend logs under .run/. Default: both,
# clearly labelled (GNU tail's native `==> file <==` headers when given
# multiple files -- no hand-rolled multiplexing). `./logs.sh backend` or
# `./logs.sh frontend` tails just one.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/_launcher_lib.sh
source "${SCRIPT_DIR}/scripts/_launcher_lib.sh"

usage() {
  echo "Usage: $0 [backend|frontend]" >&2
}

tail_one() {
  local name="$1" file="$2"
  if [ ! -f "${file}" ]; then
    echo "No ${name} log yet at ${file} -- it hasn't been started, or the log was cleaned up. Run ./start.sh first." >&2
    exit 1
  fi
  exec tail -n 50 -f "${file}"
}

main() {
  case "${1:-}" in
  "")
    local files=()
    [ -f "${BACKEND_LOG}" ] || echo "note: no backend log yet at ${BACKEND_LOG}" >&2
    [ -f "${BACKEND_LOG}" ] && files+=("${BACKEND_LOG}")
    [ -f "${FRONTEND_LOG}" ] || echo "note: no frontend log yet at ${FRONTEND_LOG}" >&2
    [ -f "${FRONTEND_LOG}" ] && files+=("${FRONTEND_LOG}")
    if [ "${#files[@]}" -eq 0 ]; then
      echo "No logs yet -- run ./start.sh first." >&2
      exit 1
    fi
    exec tail -n 20 -f "${files[@]}"
    ;;
  backend) tail_one "backend" "${BACKEND_LOG}" ;;
  frontend) tail_one "frontend" "${FRONTEND_LOG}" ;;
  *)
    usage
    exit 1
    ;;
  esac
}

main "$@"
