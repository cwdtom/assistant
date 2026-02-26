# CLI Personal Assistant AGENTS Guide

## Project Goal
Build a local-first CLI personal assistant that supports:
1. AI chat (Chinese-first)
2. Todo/task management
3. Local schedule management

Current MVP constraints:
- Single user, no login
- SQLite local storage
- No third-party calendar/email integration in V1
- LLM via DeepSeek API (`DEEPSEEK_BASE_URL` + `DEEPSEEK_API_KEY`)

## Tech Stack (MVP)
- Python 3.10+
- Official OpenAI Python SDK (OpenAI-compatible endpoint)
- SQLite (`sqlite3` in stdlib)
- `unittest` for unit tests

## Directory Convention
- `assistant_app/` core application
  - `cli.py`: interactive loop
  - `agent.py`: command parsing and orchestration
  - `llm.py`: model gateway
  - `db.py`: SQLite persistence
  - `config.py`: environment config
- `tests/`: unit tests
- `main.py`: local entrypoint

## Command Contract (MVP)
Supported input forms in CLI:
- `/help`
- `/todo add <content> [--tag <tag>]`
- `/todo list [--tag <tag>]`
- `/todo done <id>`
- `/schedule add <YYYY-MM-DD HH:MM> <title>`
- `/schedule list`
- natural language -> model intent recognition -> execute local action (e.g. `添加待办 买牛奶`, `查看日程`)
- free text => send to LLM

## Development Workflow
### Step 1: collect information
1. Check current branch history and workspace status.
2. Clarify unclear requirements with the user before coding.
3. Reuse existing implementation patterns; prefer official SDKs.

### Step 2: coding
1. Use small incremental changes; keep code runnable.
2. Add/update unit tests with each functional change.
3. Fix existing defects before extending features.
4. Keep interfaces stable and explicit.

### Step 3: output report
- Report generation is optional for current user request.
- If required later, use file name:
  `{yyyyMMddHH}:{branch-last-segment}:{command}.md`

## Definition of Done (MVP)
1. CLI starts and accepts commands.
2. Todo/schedule data persists in SQLite.
3. Free-text chat can call configured LLM endpoint.
4. Unit tests pass locally.

---

## Supplement: Runtime Flags (moved from README)
Default model:
- `DEEPSEEK_MODEL=deepseek-chat` (general)
- 当前 thought tool-calling 链路不支持 `deepseek-reasoner` / thinking 模式；检测到 reasoning 输出会直接报错

Optional runtime flags (all supported in `.env`):
- `LLM_TEMPERATURE`: temperature for all LLM calls (default `0.3`, range `0.0~2.0`)
- `PLAN_REPLAN_MAX_STEPS`: max plan-loop steps (default `20`)
- `PLAN_REPLAN_RETRY_COUNT`: planner JSON retry count (default `2`)
- `PLAN_OBSERVATION_CHAR_LIMIT`: max chars per observation (default `10000`)
- `PLAN_OBSERVATION_HISTORY_LIMIT`: observation history cap in thought context (default `100`)
- `PLAN_CONTINUOUS_FAILURE_LIMIT`: fallback threshold for continuous failures (default `2`)
- `TASK_CANCEL_COMMAND`: task cancel phrase (default `取消当前任务`)
- `INTERNET_SEARCH_TOP_K`: top-k search results (default `3`)
- `SEARCH_PROVIDER`: search provider (default `bocha`, supports `bocha|bing`)
- `BOCHA_API_KEY`: Bocha Web Search API key (fallback to Bing when empty)
- `BOCHA_SEARCH_SUMMARY`: whether Bocha returns summary (default `true`)
- `SCHEDULE_MAX_WINDOW_DAYS`: max days in schedule list window (default `31`)
- `INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS`: preview days for infinite-repeat conflict checks (default `31`)
- `TIMER_ENABLED`: enable local reminder thread (default `true`)
- `TIMER_POLL_INTERVAL_SECONDS`: reminder poll interval (default `15`)
- `TIMER_LOOKAHEAD_SECONDS`: reminder lookahead window (default `30`)
- `TIMER_CATCHUP_SECONDS`: reminder catch-up window (reserved in V1, default `0`)
- `TIMER_BATCH_LIMIT`: max reminders per poll batch (default `200`)
- `REMINDER_DELIVERY_RETENTION_DAYS`: retention days for delivery records (reserved in V1, default `30`)
- `CLI_PROGRESS_COLOR`: progress output color (`gray|off`, default `gray`)
- `PERSONA_REWRITE_ENABLED`: enable persona rewrite (default `true`)
- `ASSISTANT_PERSONA`: assistant persona text
- `USER_PROFILE_PATH`: user profile markdown file path (loaded content is injected into plan/replan context)
- `APP_LOG_PATH`: general runtime log path (JSON Lines, default `logs/app.log`, empty to disable)
- `APP_LOG_RETENTION_DAYS`: app log retention days for daily rotation (default `7`)
- `LLM_TRACE_LOG_PATH`: LLM trace log path (default follows `APP_LOG_PATH`, empty to disable)
- `FEISHU_ENABLED`: enable Feishu long connection (default `false`)
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`: Feishu app credentials
- `FEISHU_ALLOWED_OPEN_IDS`: open_id whitelist (comma separated)
- `FEISHU_SEND_RETRY_COUNT`: send retry count (default `3`)
- `FEISHU_TEXT_CHUNK_SIZE`: long message chunk size (default `1500`)
- `FEISHU_DEDUP_TTL_SECONDS`: dedup window in seconds (default `600`)
- `FEISHU_LOG_PATH`: Feishu log path (default follows `APP_LOG_PATH`)
- `FEISHU_LOG_RETENTION_DAYS`: Feishu log retention days (default `7`)
- `FEISHU_ACK_REACTION_ENABLED`: send ack reaction on incoming DM (default `true`)
- `FEISHU_ACK_EMOJI_TYPE`: ack emoji type (default `OK`)
- `FEISHU_DONE_EMOJI_TYPE`: done emoji type (default `DONE`)

## Supplement: Detailed Behavior Notes (moved from README)
- Todo/Schedule both support full CRUD.
- Every non-`/` input persists into `chat_history` with final assistant reply.
- `/history search` supports fuzzy keyword search on user input and assistant output.
- Schedule includes `duration_minutes` (default `60` on create).
- Recurring schedules are stored in `recurring_schedules` and merged in list/view results.
- Schedule supports reminder timestamps (`--remind`).
- Recurring schedule supports reminder start (`--remind-start`).
- CLI starts a local reminder thread by default (`TIMER_ENABLED=off` to disable).
- V1 reminders include todo reminders, single schedule reminders, and recurring occurrence reminders.
- If `--interval` is provided without `--times`, default `times=-1` (infinite repeat).
- Repeat rules support enable/disable via `/schedule repeat <id> <on|off>`.
- `/schedule list` default window is from two days before now to +31 days.
- `/schedule view` computes by explicit day/week/month anchor window.
- CLI outputs repeat metadata for schedule list/detail.
- Schedule conflict detection checks overlapping time ranges, including duration and repeats.
- For infinite repeats (`times=-1`), conflict check is window-based with configurable preview days.
- Todo supports search and `all|today|overdue|upcoming|inbox` views.
- Todo priority is integer with smaller value = higher priority (minimum `0`).
- Todo/schedule query outputs use table-style formatting in CLI.
- Entering and exiting CLI clears terminal history (scrollback).
- Natural-language tasks show live progress for plan list, step status, tool calls, and outcomes.
- Thought uses chat tool-calling with tools: `todo|schedule|internet_search|history_search|ask_user|done`.
- Thought 的所有 tool calls 都必须传结构化参数；禁止传 `/todo`、`/schedule` 等命令字符串。
- 时间格式与单位约束通过 thought 的 tools schema 字段描述提供（不再单独注入 `time_unit_contract` 上下文）。
- `ask_user` sends a single clarification question prefixed with `请确认：...`.
- `TASK_CANCEL_COMMAND` phrase interrupts current task loop.
- Replan completion can trigger persona rewrite on final answer (fallback to original on failure).
- Local reminder output can also be persona-rewritten (fallback on failure).
- Feishu mode supports DM queue isolation, dedup, interruption/requeue, semantic split, and retry.
- Default natural-language step cap is `20`; timeout returns partial completion + next-step suggestion.
- Runtime logs use JSON Lines format; by default app/llm/feishu are consolidated into `app.log`.

## Supplement: View Semantics (moved from README)
- `all`: all todos (including done)
- `today`: due today and not done
- `overdue`: overdue and not done
- `upcoming`: due in next 7 days and not done
- `inbox`: no due date and not done

## Supplement: Calendar View Semantics (moved from README)
- `day`: `YYYY-MM-DD`
- `week`: week range by Monday-Sunday, anchor format `YYYY-MM-DD`
- `month`: `YYYY-MM`

## Supplement: Data Model (moved from README)
- `todos`: content, tag, priority, done status, created/done time, due/remind time.
- `schedules`: title, start datetime, duration, reminder datetime, created time.
- `recurring_schedules`: repeat rule linked by `schedule_id`, with interval/times/remind-start/enabled.
- `chat_history`: stores `user_content`, `assistant_content`, and `created_at`.

## Supplement: Manual DB Initialization (moved from README)
- SQL file: `sql/init_assistant_db.sql`
- Example:
```bash
sqlite3 assistant.db < sql/init_assistant_db.sql
sqlite3 /path/to/assistant.db < sql/init_assistant_db.sql
```

## Supplement: Dev Commands (moved from README)
```bash
# unit tests
python -m unittest discover -s tests -p "test_*.py"

# lint/format/type-check
ruff check .
ruff format .
mypy

# pre-commit
pre-commit install
pre-commit run --all-files
```

## Supplement: Doc Directory Cleanup (2026-02-26)

Purpose: reduce duplicated descriptions across `README.md` / `doc/` and keep only high-frequency, current-state docs at `doc/` root.

### Cleanup Result

- Active docs in `doc/` root are now:
  - `doc/README.md`: concise doc navigation and maintenance rules.
  - `doc/session-quickstart.md`: 5-minute onboarding for current runtime and troubleshooting.
  - `doc/timer-design.md`: current timer/reminder behavior and boundaries.
- Historical materials are indexed in:
  - `doc/archive/README.md`
- Long-form timer design has been archived to:
  - `doc/archive/2026022611:main:timer-design-v1-full.md`

### Ongoing Maintenance Rules

1. Current facts must be maintained in `README.md` + `doc/session-quickstart.md`.
2. Complete parameter/behavior details stay in `AGENTS.md`.
3. Phase reports and design drafts go to `doc/archive/` and should be added to `doc/archive/README.md`.
4. Avoid copying the same long explanation into multiple files; prefer short summary + reference link.
