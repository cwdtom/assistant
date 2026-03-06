#!/usr/bin/env bash

set -euo pipefail

DB_PATH="assistant.db"
DRY_RUN=false
NO_BACKUP=false
SCANNED_COLUMNS=6
DATETIME_GLOB='????-??-?? ??:??:??'

SPECS=(
  'schedules|id|event_time'
  'schedules|id|remind_at'
  'recurring_schedules|id|start_time'
  'recurring_schedules|id|remind_start_time'
  'reminder_deliveries|id|occurrence_time'
  'reminder_deliveries|id|remind_time'
)

print_usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Normalize minute-precision datetime fields accidentally stored with seconds.

Options:
  --db-path PATH  SQLite database path. Default: assistant.db
  --dry-run       Show pending fixes without modifying the database.
  --no-backup     Skip creating a backup before applying changes.
  -h, --help      Show this help.
USAGE
}

require_sqlite3() {
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "sqlite3 not found. Please install sqlite3 first." >&2
    exit 1
  fi
}

sql_quote() {
  printf "%s" "$1" | sed "s/'/''/g"
}

collect_fixes() {
  local output_file="$1"
  : > "$output_file"

  local spec table id_column column table_exists query
  for spec in "${SPECS[@]}"; do
    IFS='|' read -r table id_column column <<<"$spec"
    table_exists="$(sqlite3 "$DB_PATH" "SELECT 1 FROM sqlite_master WHERE type='table' AND name='$(sql_quote "$table")' LIMIT 1;")"
    if [[ "$table_exists" != "1" ]]; then
      continue
    fi

    query=$(cat <<SQL
SELECT ${id_column}, '${table}', '${id_column}', '${column}', ${column}, substr(${column}, 1, 16)
FROM ${table}
WHERE ${column} IS NOT NULL
  AND length(${column}) = 19
  AND ${column} GLOB '${DATETIME_GLOB}'
ORDER BY ${id_column};
SQL
)
    sqlite3 -tabs -noheader "$DB_PATH" "$query" >> "$output_file"
  done
}

apply_fixes() {
  local spec table id_column column table_exists query
  for spec in "${SPECS[@]}"; do
    IFS='|' read -r table id_column column <<<"$spec"
    table_exists="$(sqlite3 "$DB_PATH" "SELECT 1 FROM sqlite_master WHERE type='table' AND name='$(sql_quote "$table")' LIMIT 1;")"
    if [[ "$table_exists" != "1" ]]; then
      continue
    fi

    query=$(cat <<SQL
UPDATE ${table}
SET ${column} = substr(${column}, 1, 16)
WHERE ${column} IS NOT NULL
  AND length(${column}) = 19
  AND ${column} GLOB '${DATETIME_GLOB}';
SQL
)
    sqlite3 "$DB_PATH" "$query"
  done
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db-path)
      DB_PATH="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --no-backup)
      NO_BACKUP=true
      shift
      ;;
    -h|--help)
      print_usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      print_usage >&2
      exit 1
      ;;
  esac
done

require_sqlite3

if [[ ! -f "$DB_PATH" ]]; then
  echo "database not found: $DB_PATH" >&2
  exit 1
fi

TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT

collect_fixes "$TMP_FILE"
PENDING_FIXES="$(wc -l < "$TMP_FILE" | tr -d ' ')"

BACKUP_PATH=""
if [[ "$PENDING_FIXES" -gt 0 && "$DRY_RUN" == "false" && "$NO_BACKUP" == "false" ]]; then
  BACKUP_PATH="${DB_PATH}.bak-$(date +%Y%m%d%H%M%S)"
  cp "$DB_PATH" "$BACKUP_PATH"
fi

if [[ "$PENDING_FIXES" -gt 0 && "$DRY_RUN" == "false" ]]; then
  apply_fixes
fi

MODE="apply"
if [[ "$DRY_RUN" == "true" ]]; then
  MODE="dry-run"
fi

echo "Mode: $MODE"
echo "Scanned columns: $SCANNED_COLUMNS"
echo "Pending fixes: $PENDING_FIXES"
if [[ -n "$BACKUP_PATH" ]]; then
  echo "Backup created: $BACKUP_PATH"
fi

if [[ "$PENDING_FIXES" -gt 0 ]]; then
  awk -F '\t' '{counts[$2 "." $4] += 1} END {for (key in counts) print key "\t" counts[key]}' "$TMP_FILE" \
    | sort \
    | while IFS=$'\t' read -r key count; do
        echo "- $key: $count"
      done

  while IFS=$'\t' read -r row_id table id_column column before after; do
    echo "  row ${table}.${id_column}=${row_id} ${column}: ${before} -> ${after}"
  done < "$TMP_FILE"
fi
