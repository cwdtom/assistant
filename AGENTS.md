# CLI Personal Assistant AGENTS Guide

## Project Goal
Build a local-first CLI personal assistant that supports:
1. AI chat (Chinese-first)
2. Local schedule/task management

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

## Command Contract (Current)
Supported input forms in CLI:
- `/help`
- `/version`
- `/date`
- `/history list [--limit <>=1>]`
- `/history search <关键词> [--limit <>=1>]`
- `/thoughts add|list|get|update|delete`
- `/schedule add|list|get|update|delete|repeat|view`
- non-`/` input goes through `plan -> thought -> act -> observe -> replan`
- thought stage uses tool-calling with structured arguments by default; legacy command-string fallback remains for compatibility and is not the primary contract

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
5. New structured payloads should prefer Pydantic schemas under `assistant_app/schemas/`; import from the concrete submodule instead of relying on `assistant_app.schemas` barrel exports unless the type is a shared base/domain model.

### Step 3: output report
- Report generation is optional for current user request.
- If required later, use file name:
  `{yyyyMMddHH}:{branch-last-segment}:{command}.md`

## Definition of Done (MVP)
1. CLI starts and accepts commands.
2. Schedule/chat history data persists in SQLite.
3. Free-text chat can call configured LLM endpoint.
4. Unit tests pass locally.

---

## Supplement: Runtime Flags (moved from README)
Default model:
- `DEEPSEEK_MODEL=deepseek-chat` (general)
- 当前 thought tool-calling 链路不支持 `deepseek-reasoner` / thinking 模式；检测到 reasoning 输出会直接报错
- 配置读取优先级：若系统环境变量与 `.env` 同名，最终以 `.env` 中的值为准

Optional runtime flags (all supported in `.env`):
- `LLM_TEMPERATURE`: default temperature for general LLM calls (default `0.5`, range `0.0~2.0`)
- `PLAN_REPLAN_MAX_STEPS`: max plan-loop steps (default `100`)
- `PLAN_REPLAN_RETRY_COUNT`: planner JSON retry count (default `3`)
- `PLAN_OBSERVATION_CHAR_LIMIT`: max chars per observation (default `10000`)
- `PLAN_OBSERVATION_HISTORY_LIMIT`: observation history cap in thought context (default `100`)
- `PLAN_CONTINUOUS_FAILURE_LIMIT`: fallback threshold for continuous failures (default `3`)
- `TASK_CANCEL_COMMAND`: task cancel phrase (default `取消当前任务`)
- `INTERNET_SEARCH_TOP_K`: target top-k for Bocha reranker (`rerankTopK`, default `3`)
- `SEARCH_PROVIDER`: search provider (default `bocha`, supports `bocha|bing`)
- `BOCHA_API_KEY`: Bocha Web Search API key (fallback to Bing when empty)
- `BOCHA_SEARCH_SUMMARY`: whether Bocha returns summary (default `true`; parsing prefers `summary` and falls back to `snippet`)
- `SCHEDULE_MAX_WINDOW_DAYS`: max days in schedule list window (default `31`)
- `TIMER_ENABLED`: enable periodic background timer thread (default `true`)
- `TIMER_POLL_INTERVAL_SECONDS`: periodic background timer poll interval (default `15`)
- `TIMER_LOOKAHEAD_SECONDS`: retained for compatibility; schedule reminder polling is removed, so current runtime no longer consumes this value (default `30`)
- `TIMER_BATCH_LIMIT`: retained for compatibility; schedule reminder polling is removed, so current runtime no longer consumes this value (default `200`)
- `CLI_PROGRESS_COLOR`: progress output color (`gray|off`, default `gray`)
- `PERSONA_REWRITE_ENABLED`: enable persona rewrite (default `true`)
- `ASSISTANT_PERSONA`: assistant persona text
- `USER_PROFILE_PATH`: user profile markdown file path (loaded content is injected into plan/replan context)
- `SQLITE_RAG_DB_PATH`: sqlite-rag database path for async chat_history indexing (default `sqliterag.sqlite`)
- `APP_LOG_PATH`: general runtime log path (JSON Lines, default `logs/app.log`, empty to disable)
- `APP_LOG_RETENTION_DAYS`: app log retention days for daily rotation (default `7`)
- `LLM_TRACE_LOG_PATH`: LLM trace log path (default follows `APP_LOG_PATH`, empty to disable)
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`: Feishu app credentials; when both are non-empty, Feishu long connection is enabled
- `FEISHU_ALLOWED_OPEN_IDS`: open_id whitelist (comma separated)
- `FEISHU_SEND_RETRY_COUNT`: send retry count (default `3`)
- `FEISHU_TEXT_CHUNK_SIZE`: long message chunk size (default `5000`)
- `FEISHU_DEDUP_TTL_SECONDS`: dedup window in seconds (default `600`)
- `FEISHU_LOG_PATH`: Feishu log path (default follows `APP_LOG_PATH`)
- `FEISHU_LOG_RETENTION_DAYS`: Feishu log retention days (default `7`)
- `FEISHU_ACK_REACTION_ENABLED`: send ack reaction on incoming DM (default `true`)
- `FEISHU_ACK_EMOJI_TYPE`: ack emoji type (default `Get`)
- `FEISHU_DONE_EMOJI_TYPE`: done emoji type (default `DONE`)
- `FEISHU_CALENDAR_ID`: target Feishu calendar id for sync; when non-empty and Feishu credentials are present, local schedule <-> Feishu calendar sync is enabled
- `FEISHU_CALENDAR_BOOTSTRAP_PAST_DAYS`: startup bootstrap sync lookback days (default `2`)
- `FEISHU_CALENDAR_BOOTSTRAP_FUTURE_DAYS`: startup bootstrap sync lookahead days (default `5`)
- `PROACTIVE_REMINDER_TARGET_OPEN_ID`: fixed Feishu target open_id for scheduled planner task final-result delivery; when non-empty and Feishu credentials are present, scheduled final message sending can be enabled

## Supplement: Detailed Behavior Notes (moved from README)
- Every non-`/` input persists into `chat_history` with final assistant reply, except plan ack-only completion and scheduled tasks with `should_send=false`.
- Every `chat_history` INSERT triggers async sqlite-rag indexing (`uri=assistant://chat_history/{chat_id}`); this path is optional-dependency best-effort, and failures only emit logs without blocking main flow.
- `/history search` now prefers sqlite-rag retrieval first; when sqlite-rag is unavailable, errors, returns empty, or returns unmappable hits, it falls back to SQL fuzzy keyword search on user input and assistant output.
- Thoughts supports minimal fields: `content` + `status` (`未完成|完成|删除`).
- Thoughts delete uses soft-delete semantics (`status=删除`); default `/thoughts list` excludes deleted records.
- Schedule includes `duration_minutes` (default `60` on create).
- Timer also scans `timer_tasks` every `TIMER_POLL_INTERVAL_SECONDS`; rows with `run_limit != 0` and due `next_run_at` are queued serially, pushed into the existing planner flow via `prompt`, and do not catch up missed runs. Starting execution decrements `run_limit` once, except `-1` which remains unlimited.
- New database initialization seeds two default `timer_tasks`: `每日用户侧写更新` (`0 4 * * *`) and `每小时提醒` (`0 * * * *`), both with `run_limit=-1` and `next_run_at=NULL` before timer initialization.
- Schedule supports `tag` labels (default `default`), and list/view can filter by tag.
- Recurring schedules are stored in `recurring_schedules` and merged in list/view results.
- Schedule supports reminder timestamps (`--remind`).
- Schedule reminder fields remain persisted, but runtime no longer auto-polls or auto-delivers local schedule reminders.
- Optional Feishu calendar sync uses identity matching by `title + description(tag) + start + end` (minute-level); local writes still sync asynchronously, and updates perform old-identity cleanup + new-identity upsert.
- Optional Feishu calendar startup bootstrap window is day-aligned by default:
  start=`(today-2d) 00:00:00`, end=`(today+5d) 23:59:59`.
- Feishu calendar startup bootstrap performs identity-based incremental alignment (keep matched events, create missing events, delete stale extras); it no longer uses a full "delete then rebuild" pass.
- Feishu calendar runtime no longer performs periodic Feishu->local reconcile; after startup bootstrap, only local schedule writes are synced to Feishu asynchronously.
- Recurring schedule supports reminder start (`--remind-start`).
- CLI starts a timer thread for periodic background tasks by default (`TIMER_ENABLED=off` to disable).
- If `--interval` is provided without `--times`, default `times=-1` (infinite repeat).
- Repeat rules support enable/disable via `/schedule repeat <id> <on|off>`.
- `/schedule list` default window is from two days before now to +31 days.
- `/schedule view` computes by explicit day/week/month anchor window.
- CLI outputs repeat metadata for schedule list/detail.
- Schedule add/update allows overlapping time ranges; no conflict pre-check is performed.
- Schedule query outputs use table-style formatting in CLI.
- Entering and exiting CLI clears terminal history (scrollback).
- Natural-language tasks show live progress for plan list, step status, tool calls, and outcomes.
- Plan output schema is `status/goal/plan`; `goal` must be the expanded executable target and will overwrite the task goal used in subsequent plan/replan context.
- Replan output accepts optional `should_send` (boolean); omitted means `true`.
- Plan phase allows empty `plan` as ack-only completion (for short confirmation/thanks messages like `谢谢/好的/明白了`); this path skips thought/replan, skips `chat_history` persistence, and does not emit `任务目标：...` progress message.
- Thought uses chat tool-calling with tools: `ask_user|done` + `schedule` group（展开为 `schedule_add|schedule_list|schedule_view|schedule_get|schedule_update|schedule_delete|schedule_repeat`）+ `timer` group（展开为 `timer_add|timer_list|timer_get|timer_update|timer_delete`，用于管理通用定时 planner 任务，不是普通日程，且仅对交互式 thought 开放）+ `internet_search` group（展开为 `internet_search_tool|internet_search_fetch_url`）+ `history` group（展开为 `history_list|history_search`）+ `thoughts` group（展开为 `thoughts_add|thoughts_list|thoughts_get|thoughts_update|thoughts_delete`，用于记录碎片想法）+ `user_profile` group（展开为 `user_profile_get|user_profile_overwrite`，用于读取/整份覆盖画像文件；缺文件按空处理，overwrite 支持空字符串清空）+ `system` group（展开为 `system_date`，用于读取当前本地时间）.
- Scheduled planner task source reuses the same plan/replan runtime but omits `ask_user` from thought tools; when replan final output is `should_send=false`, final planner response will skip `chat_history` persistence.
- Main planner source lanes are `interactive` + `scheduled`; lanes can run in parallel, while each lane keeps FIFO serialization. Pending/cancel/interrupt/recent-trace runtime states are isolated by source.
- Thought 的标准契约要求 tool calls 传结构化参数；`/schedule` 等命令字符串仅保留兼容兜底，不作为主路径。
- Plan/replan outer history now stores the raw user/assistant LLM payloads directly (no `plan_decision`/`replan_decision` wrapper).
- 时间格式与单位约束通过 thought 的 tools schema 字段描述提供（不再单独注入 `time_unit_contract` 上下文）。
- `ask_user` sends a single clarification question prefixed with `请确认：...`.
- `TASK_CANCEL_COMMAND` phrase interrupts current task loop.
- Replan completion can trigger persona rewrite on final answer (fallback to original on failure).
- Feishu mode supports DM queue isolation, dedup, interruption/requeue, semantic split, and retry.
- Feishu ack-only completion (task completed with empty response) sends ACK/DONE reactions only and skips text sending.
- Feishu ACK/DONE reaction send failure handling: if HTTP status `400` is detectable, skip reaction immediately (no retry); otherwise keep existing retry path (including cases where HTTP status is unavailable).
- Scheduled planner task execution appends `**以上消息为系统自动触发，在最后发送前需要判定内容是否有提醒价值，结合其他信息如果价值过低，should_send应该赋值为false**` to the prompt before planner execution.
- Scheduled planner task completion sends planner `final_response` directly when `should_send` is true (or omitted), `PROACTIVE_REMINDER_TARGET_OPEN_ID` is non-empty, and `final_response` is non-empty; intermediate planner progress/subtask updates are never sent for this source.
- Default natural-language step cap is `20`; timeout returns partial completion + next-step suggestion.
- Runtime logs use JSON Lines format; by default app/llm/feishu are consolidated into `app.log`.
- Feishu message logs store masked open_id and masked text previews (not raw full text).
- Bocha internet search requests always send `count=50` and enable reranker by default (`rerankModel=gte-rerank`, `rerankTopK=INTERNET_SEARCH_TOP_K`).
- `internet_search` keyword search supports optional `freshness` filter (`noLimit|oneYear|oneMonth|oneWeek|oneDay|YYYY-MM-DD|YYYY-MM-DD..YYYY-MM-DD`); currently this filter is effective on Bocha provider path and ignored by Bing fallback.
- `internet_search` no-result responses are treated as successful empty outcomes (`ok=true` with no-result message), not tool failures.
- If rerank request fails, search automatically retries once without reranker.
- `internet_search` receives a plain `http/https` URL input and auto-routes to `fetch_url` execution instead of keyword search.
- `fetch_url` uses Playwright first; if Playwright fails, it falls back to direct HTTP fetch via `requests`.
- Search output no longer performs local second truncation; it renders provider-returned results directly.
- Bocha result text extraction prefers `summary`; if unavailable, it falls back to `snippet`.

## Supplement: Calendar View Semantics (moved from README)
- `day`: `YYYY-MM-DD`
- `week`: week range by Monday-Sunday, anchor format `YYYY-MM-DD`
- `month`: `YYYY-MM`

## Supplement: Data Model (moved from README)
- `schedules`: title, tag, start datetime, duration, reminder datetime, created time.
- `recurring_schedules`: repeat rule linked by `schedule_id`, with interval/times/remind-start/enabled.
- `chat_history`: stores `user_content`, `assistant_content`, and `created_at`.
- `sqliterag.sqlite` (optional): stores rag `documents/chunks/sentences` for async chat_history indexing results.
- `timer_tasks`: stores `task_name`, `run_limit`, `cron_expr`, `prompt`, `next_run_at`, `last_run_at`, `created_at`, and `updated_at`.
- `thoughts`: stores `content`, `status`, `created_at`, and `updated_at`.

## Supplement: Dev Commands (moved from README)
```bash
# one-command bootstrap
./scripts/bootstrap.sh
./scripts/bootstrap.sh --dev

# unit tests
python -m unittest discover -s tests -p "test_*.py"

# startup helper
./scripts/assistant.sh start
./scripts/assistant.sh start work
./scripts/assistant.sh restart
./scripts/assistant.sh status work
./scripts/assistant.sh status
./scripts/assistant.sh list
./scripts/assistant.sh list work
./scripts/assistant.sh stop work
./scripts/assistant.sh stop
./scripts/assistant.sh run
# start/restart 默认先 git fetch；若远端领先则 ff merge，本地领先则跳过，分叉则报错退出
# 临时跳过自动拉取：ASSISTANT_AUTO_PULL=false ./scripts/assistant.sh start
# 也可设置默认别名：ASSISTANT_ALIAS=work ./scripts/assistant.sh start

# lint/format/type-check
ruff check .
ruff format .
mypy

# pre-commit
pre-commit install
pre-commit run --all-files
```

## Supplement: Doc Directory Convention (2026-02-28)

Purpose: `doc/` is now used for one-off generated artifacts (freeze specs, phase reports, execution snapshots), not long-lived canonical docs.

### Ongoing Maintenance Rules

1. Current runtime facts must be maintained in `README.md` + `AGENTS.md`.
2. `doc/` files are disposable/generated records; do not treat fixed paths in `doc/` as stable dependencies.
3. New generated docs should follow naming conventions already used in this repo (for example, timestamp + branch segment + command/topic).
4. If a one-off document becomes long-term knowledge, summarize it back into `README.md` or `AGENTS.md` and keep the `doc/` file as historical snapshot.
