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
