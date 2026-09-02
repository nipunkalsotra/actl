# shellcheck shell=bash
# Shared by start.sh/stop.sh/status.sh/logs.sh -- one definition of "what
# counts as an ACTL-owned backend/frontend process" so all four scripts
# agree, and one place tests/unit/test_launcher_scripts.py exercises
# directly by sourcing this file (it defines functions/constants only --
# no top-level action other than computing REPO_ROOT -- so sourcing it
# never starts anything, mirroring how scripts/clone_to_demo.sh guards
# its own `main` behind a `BASH_SOURCE == $0` check for the same reason).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${REPO_ROOT}/.run"
BACKEND_PIDFILE="${RUN_DIR}/backend.pid"
FRONTEND_PIDFILE="${RUN_DIR}/frontend.pid"
BACKEND_LOG="${RUN_DIR}/backend.log"
FRONTEND_LOG="${RUN_DIR}/frontend.log"
BACKEND_PORT=8000
FRONTEND_PORT=5173
BACKEND_URL="http://127.0.0.1:${BACKEND_PORT}"
FRONTEND_URL="http://localhost:${FRONTEND_PORT}"

pid_alive() {
  [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null
}

process_args() {
  ps -p "$1" -o args= 2>/dev/null || true
}

# lsof's cwd file-descriptor entry, not /proc/$PID/cwd -- the latter is
# Linux-only, lsof -d cwd works the same way on macOS too. `|| true`: an
# empty result (PID gone, or lsof simply finding nothing) is a normal
# outcome here, not an error -- without it, `set -o pipefail` turns
# lsof's "nothing found" exit status into this pipeline's own failure,
# which then trips `errexit` in any bare `x="$(process_cwd ...)"` caller.
process_cwd() {
  lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n1 || true
}

is_actl_backend_pid() {
  local pid="$1"
  pid_alive "$pid" || return 1
  process_args "$pid" | grep -q "actl\.main:app"
}

is_actl_frontend_pid() {
  local pid="$1"
  pid_alive "$pid" || return 1
  process_args "$pid" | grep -q "vite" || return 1
  [ "$(process_cwd "$pid")" = "${REPO_ROOT}/web" ]
}

# Empty/non-numeric content is treated as "no PID recorded" (covers a
# stale, truncated, or hand-edited PID file) rather than passed on to
# `kill`/`ps`, which would either error or -- far worse -- silently match
# an unrelated PID if the file ever contained something like "-1".
read_pidfile() {
  local pidfile="$1" pid
  [ -f "$pidfile" ] || return 1
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$pid"
}

# First PID currently listening on the given local TCP port, or empty.
# `|| true`: a free port is the common, expected case, not an error --
# see process_cwd's comment above for why this matters under `set -e`.
port_owner_pid() {
  lsof -tiTCP:"$1" -sTCP:LISTEN 2>/dev/null | head -n1 || true
}

# Value of KEY as last set in `file` (later lines win), or empty if the
# key/file is absent. `|| true`: "not set" is a normal outcome here, not
# an error -- see process_cwd's comment above for why that matters under
# `set -e`. Callers only ever test this for emptiness/equality (e.g. "is
# GROQ_API_KEY non-empty?") -- the value itself is never echoed or logged.
env_file_value() {
  local file="$1" key="$2"
  [ -f "$file" ] || return 0
  grep -E "^${key}=" "$file" 2>/dev/null | tail -n1 | cut -d= -f2- || true
}

# Sets KEY=VALUE in `file`: replaces an existing `^KEY=...` line in place,
# or appends the line if the key isn't present at all (e.g. a future
# .env.example that drops one of these lines) -- either way the safe
# default ends up set, never silently skipped.
ensure_env_default() {
  local file="$1" key="$2" value="$3"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    sed -i "s#^${key}=.*#${key}=${value}#" "$file"
  else
    printf '%s=%s\n' "$key" "$value" >>"$file"
  fi
}

# Copies `example` to `target`, forces the three safe-local-dev fields,
# and locks permissions down -- the only place a root .env or web/.env.local
# gets written by this launcher. Real secrets never pass through here:
# `example` is always a committed, placeholder-only *.env.example, and
# every other line is copied through verbatim, never invented.
generate_safe_env() {
  local example="$1" target="$2"
  cp "${example}" "${target}"
  ensure_env_default "${target}" PAYMENT_PROVIDER simulator
  ensure_env_default "${target}" LLM_ENABLED false
  ensure_env_default "${target}" ANCHOR_PROVIDER noop
  chmod 600 "${target}"
}

# True (exit 0) iff `env_file` explicitly opts in to live Groq: both
# LLM_ENABLED=true and a non-empty GROQ_API_KEY are already set there --
# in which case the caller should NOT force LLM_ENABLED=false. False
# (exit 1, the safe default for a fresh clone) otherwise. Never echoes the
# key itself, only tests it for emptiness.
llm_opt_in_present() {
  local env_file="$1"
  [ "$(env_file_value "${env_file}" LLM_ENABLED)" = "true" ] &&
    [ -n "$(env_file_value "${env_file}" GROQ_API_KEY)" ]
}

check_postgres_ready() {
  (cd "${REPO_ROOT}" && docker compose exec -T postgres pg_isready -U actl -d actl) >/dev/null 2>&1
}

check_redis_ready() {
  (cd "${REPO_ROOT}" && docker compose exec -T redis redis-cli ping) >/dev/null 2>&1
}

# Bounded wait for a docker-compose-backed check (postgres/redis
# healthchecks): never an unbounded `until ... done` loop that can hang
# forever against a container that never becomes healthy. On timeout,
# prints `docker compose ps` plus recent logs for both services and
# returns non-zero -- the same diagnostics-on-timeout shape as
# scripts/clone_to_demo.sh's own wait_ready.
wait_compose_ready() {
  local desc="$1" timeout_s="$2"
  shift 2
  local deadline=$((SECONDS + timeout_s))
  until "$@" >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      echo "FAIL: ${desc} did not become ready within ${timeout_s}s" >&2
      echo "--- docker compose ps ---" >&2
      (cd "${REPO_ROOT}" && docker compose ps) >&2 || true
      echo "--- docker compose logs (tail) ---" >&2
      (cd "${REPO_ROOT}" && docker compose logs --tail=50 postgres redis) >&2 || true
      return 1
    fi
    sleep 1
  done
}

# Bounded wait for an HTTP service to answer successfully. On timeout,
# tails the service's own log (if any) instead of hanging forever.
wait_http_ready() {
  local desc="$1" timeout_s="$2" url="$3" logfile="$4"
  local deadline=$((SECONDS + timeout_s))
  until curl -fsS "$url" >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      echo "FAIL: ${desc} did not become ready within ${timeout_s}s (${url})" >&2
      if [ -f "$logfile" ]; then
        echo "--- ${logfile} (tail) ---" >&2
        tail -n 50 "$logfile" >&2
      fi
      return 1
    fi
    sleep 1
  done
}
