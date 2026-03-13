#!/usr/bin/env bash

set -euo pipefail

DB_PATH="assistant.db"
DRY_RUN=false
NO_BACKUP=false
TABLE_NAME="thoughts"

print_usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Fix thoughts.status data/schema from Chinese statuses to English enum values.

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

table_exists() {
  sqlite3 "$DB_PATH" "SELECT 1 FROM sqlite_master WHERE type='table' AND name='$(sql_quote "$TABLE_NAME")' LIMIT 1;"
}

schema_rebuild_required() {
  local sql_text
  sql_text="$(sqlite3 "$DB_PATH" "SELECT lower(sql) FROM sqlite_master WHERE type='table' AND name='$(sql_quote "$TABLE_NAME")' LIMIT 1;")"
  if [[ "$sql_text" == *"'pending'"* && "$sql_text" == *"'completed'"* && "$sql_text" == *"'deleted'"* ]]; then
    echo "false"
    return
  fi
  echo "true"
}

collect_fixes() {
  local output_file="$1"
  sqlite3 -tabs -noheader "$DB_PATH" <<SQL > "$output_file"
SELECT
  id,
  status,
  CASE status
    WHEN '未完成' THEN 'pending'
    WHEN '完成' THEN 'completed'
    WHEN '删除' THEN 'deleted'
    ELSE status
  END AS fixed_status
FROM thoughts
WHERE status IN ('未完成', '完成', '删除')
ORDER BY id ASC;
SQL
}

apply_data_fix() {
  sqlite3 "$DB_PATH" <<'SQL'
UPDATE thoughts
SET status = CASE status
  WHEN '未完成' THEN 'pending'
  WHEN '完成' THEN 'completed'
  WHEN '删除' THEN 'deleted'
  ELSE status
END
WHERE status IN ('未完成', '完成', '删除');
SQL
}

rebuild_table_schema() {
  sqlite3 "$DB_PATH" <<'SQL'
ALTER TABLE thoughts RENAME TO thoughts_old;
CREATE TABLE thoughts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'deleted')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO thoughts (id, content, status, created_at, updated_at)
SELECT
  id,
  content,
  CASE status
    WHEN '未完成' THEN 'pending'
    WHEN '完成' THEN 'completed'
    WHEN '删除' THEN 'deleted'
    WHEN 'pending' THEN 'pending'
    WHEN 'completed' THEN 'completed'
    WHEN 'deleted' THEN 'deleted'
    ELSE 'pending'
  END,
  created_at,
  updated_at
FROM thoughts_old;
DROP TABLE thoughts_old;
SQL
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

if [[ "$(table_exists)" != "1" ]]; then
  echo "Mode: $([[ "$DRY_RUN" == "true" ]] && echo dry-run || echo apply)"
  echo "Table thoughts exists: false"
  echo "Schema rebuild required: false"
  echo "Pending fixes: 0"
  exit 0
fi

SCHEMA_REBUILD_REQUIRED="$(schema_rebuild_required)"
TMP_FILE="$(mktemp)"
trap 'rm -f "$TMP_FILE"' EXIT
collect_fixes "$TMP_FILE"
PENDING_FIXES="$(wc -l < "$TMP_FILE" | tr -d ' ')"

BACKUP_PATH=""
if [[ "$DRY_RUN" == "false" && "$NO_BACKUP" == "false" ]]; then
  BACKUP_PATH="${DB_PATH}.bak-$(date +%Y%m%d%H%M%S)"
  cp "$DB_PATH" "$BACKUP_PATH"
fi

SCHEMA_REBUILT="false"
if [[ "$DRY_RUN" == "false" ]]; then
  if [[ "$SCHEMA_REBUILD_REQUIRED" == "true" ]]; then
    rebuild_table_schema
    SCHEMA_REBUILT="true"
  elif [[ "$PENDING_FIXES" -gt 0 ]]; then
    apply_data_fix
  fi
fi

MODE="apply"
if [[ "$DRY_RUN" == "true" ]]; then
  MODE="dry-run"
fi

echo "Mode: $MODE"
echo "Table thoughts exists: true"
echo "Schema rebuild required: $SCHEMA_REBUILD_REQUIRED"
echo "Schema rebuilt: $SCHEMA_REBUILT"
echo "Pending fixes: $PENDING_FIXES"
if [[ -n "$BACKUP_PATH" ]]; then
  echo "Backup created: $BACKUP_PATH"
fi

if [[ "$PENDING_FIXES" -gt 0 ]]; then
  awk -F '\t' '{counts[$2 "->" $3] += 1} END {for (key in counts) print key "\t" counts[key]}' "$TMP_FILE" \
    | sort \
    | while IFS=$'\t' read -r key count; do
        echo "- $key: $count"
      done

  while IFS=$'\t' read -r row_id before after; do
    echo "  row thoughts.id=${row_id} status: ${before} -> ${after}"
  done < "$TMP_FILE"
fi
