#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
ENV_EXAMPLE="$ROOT_DIR/.env.example"
ENV_FILE="$ROOT_DIR/.env"

INSTALL_DEV=false
SKIP_INSTALL=false
SKIP_DB=false
FORCE_ENV=false

print_usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Initialize local project environment:
  1) create .venv (if missing)
  2) install dependencies
  3) create .env from .env.example (if missing)
  4) initialize SQLite schema

Options:
  --dev           Install development dependencies (.[dev]).
  --skip-install  Skip virtualenv/dependency installation.
  --skip-db       Skip SQLite initialization.
  --force-env     Always overwrite .env from .env.example.
  -h, --help      Show this help.
EOF
}

resolve_python3() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  echo "python3 not found. Please install Python 3.10+ first." >&2
  return 1
}

ensure_venv() {
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    return 0
  fi
  local python3_bin
  python3_bin="$(resolve_python3)"
  echo "Creating virtual environment at $VENV_DIR ..."
  "$python3_bin" -m venv "$VENV_DIR"
}

install_dependencies() {
  ensure_venv
  local venv_python="$VENV_DIR/bin/python"
  echo "Installing dependencies ..."
  "$venv_python" -m pip install --upgrade pip
  if [[ "$INSTALL_DEV" == "true" ]]; then
    "$venv_python" -m pip install -e "$ROOT_DIR[dev]"
  else
    "$venv_python" -m pip install -e "$ROOT_DIR"
  fi
}

init_env_file() {
  if [[ ! -f "$ENV_EXAMPLE" ]]; then
    echo "Skip .env initialization: $ENV_EXAMPLE not found."
    return 0
  fi
  if [[ -f "$ENV_FILE" && "$FORCE_ENV" != "true" ]]; then
    echo "Keeping existing .env."
    return 0
  fi
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  echo "Initialized .env from .env.example."
}

resolve_runtime_python() {
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    echo "$VENV_DIR/bin/python"
    return 0
  fi
  resolve_python3
}

init_sqlite_schema() {
  local runtime_python
  runtime_python="$(resolve_runtime_python)"
  echo "Initializing SQLite schema ..."
  (
    cd "$ROOT_DIR"
    "$runtime_python" - <<'PY'
from assistant_app.config import load_config
from assistant_app.db import AssistantDB

cfg = load_config()
db = AssistantDB(cfg.db_path)
print(f"SQLite initialized at: {db.db_path}")
PY
  )
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dev)
      INSTALL_DEV=true
      shift
      ;;
    --skip-install)
      SKIP_INSTALL=true
      shift
      ;;
    --skip-db)
      SKIP_DB=true
      shift
      ;;
    --force-env)
      FORCE_ENV=true
      shift
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      print_usage
      exit 1
      ;;
  esac
done

mkdir -p "$ROOT_DIR/logs"

if [[ "$SKIP_INSTALL" != "true" ]]; then
  install_dependencies
else
  echo "Skipping dependency installation."
fi

init_env_file

if [[ "$SKIP_DB" != "true" ]]; then
  init_sqlite_schema
else
  echo "Skipping SQLite initialization."
fi

echo "Project bootstrap completed."
