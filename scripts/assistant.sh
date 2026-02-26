#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
PID_FILE="$LOG_DIR/assistant.pid"
STDIN_PIPE="$LOG_DIR/assistant.stdin"
APP_LOG_FILE="$LOG_DIR/assistant.stdout.log"
AUTO_PULL_FLAG="${ASSISTANT_AUTO_PULL:-true}"
AUTO_PULL_REMOTE="${ASSISTANT_AUTO_PULL_REMOTE:-origin}"
AUTO_PULL_BRANCH="${ASSISTANT_AUTO_PULL_BRANCH:-}"

resolve_python_bin() {
  local venv_python="$ROOT_DIR/.venv/bin/python"
  if [[ -x "$venv_python" ]]; then
    echo "$venv_python"
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi

  echo "python3 not found. Please install Python 3 or create .venv first." >&2
  return 1
}

is_truthy() {
  local value
  value="$(printf "%s" "${1:-}" | tr '[:upper:]' '[:lower:]')"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" || "$value" == "on" ]]
}

ensure_latest_code() {
  if ! is_truthy "$AUTO_PULL_FLAG"; then
    return 0
  fi

  (
    cd "$ROOT_DIR"
    if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      echo "Skip auto update: $ROOT_DIR is not a git repository."
      return 0
    fi

    local branch
    if [[ -n "$AUTO_PULL_BRANCH" ]]; then
      branch="$AUTO_PULL_BRANCH"
    else
      branch="$(git rev-parse --abbrev-ref HEAD)"
    fi

    if [[ "$branch" == "HEAD" || -z "$branch" ]]; then
      echo "Auto update failed: cannot detect target branch in detached HEAD." >&2
      return 1
    fi

    echo "Syncing latest code from $AUTO_PULL_REMOTE/$branch ..."
    git fetch "$AUTO_PULL_REMOTE"

    local remote_ahead local_ahead
    remote_ahead="$(git rev-list --left-right --count "$AUTO_PULL_REMOTE/$branch...HEAD" | awk '{print $1}')"
    local_ahead="$(git rev-list --left-right --count "$AUTO_PULL_REMOTE/$branch...HEAD" | awk '{print $2}')"

    if [[ "$remote_ahead" -gt 0 && "$local_ahead" -gt 0 ]]; then
      echo "Auto update failed: local branch diverged from $AUTO_PULL_REMOTE/$branch." >&2
      echo "Please resolve with manual git pull/rebase first." >&2
      return 1
    fi

    if [[ "$remote_ahead" -gt 0 ]]; then
      git merge --ff-only "$AUTO_PULL_REMOTE/$branch"
      return 0
    fi

    if [[ "$local_ahead" -gt 0 ]]; then
      echo "Local branch is ahead of $AUTO_PULL_REMOTE/$branch; skip fast-forward merge."
      return 0
    fi

    echo "Already up to date."
  )
}

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

start_background() {
  local skip_update="${1:-false}"
  if ! is_truthy "$skip_update"; then
    ensure_latest_code
  fi

  if is_running; then
    echo "Assistant is already running (pid $(cat "$PID_FILE"))."
    return 0
  fi

  mkdir -p "$LOG_DIR"

  if [[ ! -p "$STDIN_PIPE" ]]; then
    rm -f "$STDIN_PIPE"
    mkfifo "$STDIN_PIPE"
  fi

  local python_bin
  python_bin="$(resolve_python_bin)"

  (
    cd "$ROOT_DIR"
    # Keep one read-write handle open so input() does not get EOF in background mode.
    exec 3<>"$STDIN_PIPE"
    exec "$python_bin" main.py <"$STDIN_PIPE" >>"$APP_LOG_FILE" 2>&1
  ) &

  local pid="$!"
  echo "$pid" >"$PID_FILE"
  sleep 0.3
  if kill -0 "$pid" >/dev/null 2>&1; then
    echo "Assistant started in background (pid $pid)."
    echo "Log file: $APP_LOG_FILE"
  else
    echo "Assistant failed to start. Check logs: $APP_LOG_FILE" >&2
    rm -f "$PID_FILE"
    return 1
  fi
}

stop_background() {
  if ! is_running; then
    echo "Assistant is not running."
    rm -f "$PID_FILE"
    return 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" >/dev/null 2>&1 || true

  for _ in {1..20}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      break
    fi
    sleep 0.2
  done

  if kill -0 "$pid" >/dev/null 2>&1; then
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi

  rm -f "$PID_FILE"
  echo "Assistant stopped."
}

status_background() {
  if is_running; then
    echo "Assistant is running (pid $(cat "$PID_FILE"))."
  else
    echo "Assistant is not running."
  fi
}

run_foreground() {
  local python_bin
  python_bin="$(resolve_python_bin)"
  cd "$ROOT_DIR"
  exec "$python_bin" main.py
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [start|stop|restart|status|run]

  start    Auto-update code then start assistant in background mode.
  stop     Stop background assistant.
  restart  Auto-update code, then restart background assistant.
  status   Show background assistant status.
  run      Run assistant in current terminal (foreground).

Environment:
  ASSISTANT_AUTO_PULL=true|false  Enable auto update before start/restart (default: true).
  ASSISTANT_AUTO_PULL_REMOTE=...  Git remote used for update (default: origin).
  ASSISTANT_AUTO_PULL_BRANCH=...  Override target branch (default: current branch).
EOF
}

cmd="${1:-start}"
case "$cmd" in
  start)
    start_background
    ;;
  stop)
    stop_background
    ;;
  restart)
    ensure_latest_code
    stop_background
    start_background true
    ;;
  status)
    status_background
    ;;
  run)
    run_foreground
    ;;
  *)
    usage
    exit 1
    ;;
esac
