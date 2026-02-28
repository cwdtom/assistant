#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
DEFAULT_INSTANCE_ALIAS="default"
INSTANCE_ALIAS="${ASSISTANT_ALIAS:-$DEFAULT_INSTANCE_ALIAS}"
PID_FILE=""
STDIN_PIPE=""
APP_LOG_FILE=""
AUTO_PULL_FLAG="${ASSISTANT_AUTO_PULL:-true}"
AUTO_PULL_REMOTE="${ASSISTANT_AUTO_PULL_REMOTE:-origin}"
AUTO_PULL_BRANCH="${ASSISTANT_AUTO_PULL_BRANCH:-}"

resolve_runtime_paths() {
  local suffix=""
  if [[ "$INSTANCE_ALIAS" != "$DEFAULT_INSTANCE_ALIAS" ]]; then
    suffix=".$INSTANCE_ALIAS"
  fi

  PID_FILE="$LOG_DIR/assistant${suffix}.pid"
  STDIN_PIPE="$LOG_DIR/assistant${suffix}.stdin"
  APP_LOG_FILE="$LOG_DIR/assistant${suffix}.stdout.log"
}

validate_alias() {
  if [[ -z "$INSTANCE_ALIAS" ]]; then
    echo "Assistant alias cannot be empty." >&2
    return 1
  fi

  if [[ "$INSTANCE_ALIAS" =~ [^a-zA-Z0-9._-] ]]; then
    echo "Invalid assistant alias: $INSTANCE_ALIAS" >&2
    echo "Allowed chars: letters, numbers, dot, underscore, hyphen." >&2
    return 1
  fi

  resolve_runtime_paths
}

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

read_branch_divergence() {
  local remote_ref="$1"
  local counts
  counts="$(git rev-list --left-right --count "$remote_ref...HEAD")"

  local remote_ahead local_ahead
  read -r remote_ahead local_ahead <<<"$counts"
  echo "$remote_ahead" "$local_ahead"
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

    local remote_ref
    remote_ref="$AUTO_PULL_REMOTE/$branch"
    local remote_ahead local_ahead
    read -r remote_ahead local_ahead <<<"$(read_branch_divergence "$remote_ref")"

    if [[ "$remote_ahead" -gt 0 && "$local_ahead" -gt 0 ]]; then
      echo "Auto update failed: local branch diverged from $remote_ref." >&2
      echo "Please resolve with manual git pull/rebase first." >&2
      return 1
    fi

    if [[ "$remote_ahead" -gt 0 ]]; then
      git merge --ff-only "$remote_ref"
      return 0
    fi

    if [[ "$local_ahead" -gt 0 ]]; then
      echo "Local branch is ahead of $remote_ref; skip fast-forward merge."
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
    echo "Assistant ($INSTANCE_ALIAS) is already running (pid $(cat "$PID_FILE"))."
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
    echo "Assistant ($INSTANCE_ALIAS) started in background (pid $pid)."
    echo "Log file: $APP_LOG_FILE"
  else
    echo "Assistant failed to start. Check logs: $APP_LOG_FILE" >&2
    rm -f "$PID_FILE"
    return 1
  fi
}

stop_background() {
  if ! is_running; then
    echo "Assistant ($INSTANCE_ALIAS) is not running."
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
  rm -f "$STDIN_PIPE"
  echo "Assistant ($INSTANCE_ALIAS) stopped."
}

status_background() {
  if is_running; then
    echo "Assistant ($INSTANCE_ALIAS) is running (pid $(cat "$PID_FILE"))."
  else
    echo "Assistant ($INSTANCE_ALIAS) is not running."
  fi
}

parse_alias_from_pid_file() {
  local filename
  filename="$(basename "$1")"

  if [[ "$filename" == "assistant.pid" ]]; then
    echo "$DEFAULT_INSTANCE_ALIAS"
    return 0
  fi

  local alias
  alias="${filename#assistant.}"
  alias="${alias%.pid}"
  echo "$alias"
}

list_background() {
  mkdir -p "$LOG_DIR"

  local filter_alias="${1:-}"
  local pid_files=()
  local pid_file
  local alias
  local pid
  local status
  local found=0

  shopt -s nullglob
  pid_files=("$LOG_DIR"/assistant.pid "$LOG_DIR"/assistant.*.pid)
  shopt -u nullglob

  for pid_file in "${pid_files[@]}"; do
    [[ -f "$pid_file" ]] || continue

    alias="$(parse_alias_from_pid_file "$pid_file")"
    if [[ -n "$filter_alias" && "$alias" != "$filter_alias" ]]; then
      continue
    fi

    pid="$(tr -d '[:space:]' <"$pid_file")"
    status="stopped"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      status="running"
    fi

    if [[ "$found" -eq 0 ]]; then
      printf "%-20s %-10s %-8s %s\n" "ALIAS" "STATUS" "PID" "PID_FILE"
    fi

    printf "%-20s %-10s %-8s %s\n" "$alias" "$status" "${pid:--}" "$pid_file"
    found=1
  done

  if [[ "$found" -eq 0 ]]; then
    if [[ -n "$filter_alias" ]]; then
      echo "No assistant instances found for alias: $filter_alias"
    else
      echo "No assistant instances found."
    fi
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
Usage: $(basename "$0") [--alias <name>] [start|stop|restart|status|list|run] [alias]

  start    Auto-update code then start assistant in background mode.
  stop     Stop background assistant.
  restart  Auto-update code, then restart background assistant.
  status   Show background assistant status.
  list     List all assistant instances (or filter by alias).
  run      Run assistant in current terminal (foreground).

Examples:
  $(basename "$0") start
  $(basename "$0") start dev
  $(basename "$0") --alias work status
  $(basename "$0") list

Environment:
  ASSISTANT_ALIAS=...            Default assistant alias (default: $DEFAULT_INSTANCE_ALIAS).
  ASSISTANT_AUTO_PULL=true|false  Enable auto update before start/restart (default: true).
  ASSISTANT_AUTO_PULL_REMOTE=...  Git remote used for update (default: origin).
  ASSISTANT_AUTO_PULL_BRANCH=...  Override target branch (default: current branch).
EOF
}

cmd="start"
positionals=()
alias_set_by_option=0
alias_set_by_cli=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -a|--alias)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for $1" >&2
        usage
        exit 1
      fi
      INSTANCE_ALIAS="$2"
      alias_set_by_option=1
      alias_set_by_cli=1
      shift 2
      ;;
    --alias=*)
      INSTANCE_ALIAS="${1#*=}"
      alias_set_by_option=1
      alias_set_by_cli=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      positionals+=("$1")
      shift
      ;;
  esac
done

if [[ ${#positionals[@]} -ge 1 ]]; then
  cmd="${positionals[0]}"
fi

if [[ ${#positionals[@]} -ge 2 ]]; then
  if [[ "$alias_set_by_option" -eq 1 ]]; then
    echo "Alias provided twice: use --alias or positional alias, not both." >&2
    usage
    exit 1
  fi
  INSTANCE_ALIAS="${positionals[1]}"
  alias_set_by_cli=1
fi

if [[ ${#positionals[@]} -gt 2 ]]; then
  echo "Too many positional arguments." >&2
  usage
  exit 1
fi

if ! validate_alias; then
  exit 1
fi

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
  list)
    if [[ "$alias_set_by_cli" -eq 1 ]]; then
      list_background "$INSTANCE_ALIAS"
    else
      list_background
    fi
    ;;
  run)
    run_foreground
    ;;
  *)
    usage
    exit 1
    ;;
esac
