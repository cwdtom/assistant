# CLI AI Personal Assistant (Current Code Snapshot)

一个中文优先的本地 CLI 个人助手。当前代码已实现：
- 自然语言任务执行（plan -> thought -> act -> observe -> replan）
- thought 阶段使用 chat tool-calling（结构化参数）直接调用本地系统函数
- 待办管理（CRUD、标签、优先级、视图、搜索、提醒）
- 日程管理（CRUD、时长、重复规则、提醒、冲突检测、日历视图）
- 历史会话持久化与检索
- 本地提醒线程（待办、单次日程、重复日程）
- 可选 Feishu 长连接接入

## Runtime Requirements
- Python 3.10+
- SQLite（Python 标准库 `sqlite3`）
- DeepSeek/OpenAI-compatible API Key（通过 `.env` 配置）

## Quick Start
1. 创建虚拟环境并安装依赖
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

2. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env，至少填入 DEEPSEEK_API_KEY
```

3. 启动
```bash
python main.py
# 或 assistant
```

## Core Environment Variables
- `DEEPSEEK_API_KEY`：必填
- `DEEPSEEK_BASE_URL`：默认 `https://api.deepseek.com`
- `DEEPSEEK_MODEL`：默认 `deepseek-chat`
- `LLM_TEMPERATURE`：所有 LLM 调用温度（默认 `0.3`，范围 `0.0~2.0`）
- `ASSISTANT_DB_PATH`：SQLite 路径（默认 `assistant.db`）
- `SEARCH_PROVIDER`：搜索 provider（`bocha|bing`）
- `BOCHA_API_KEY`：当 provider 为 `bocha` 时推荐配置
- `TIMER_ENABLED`：是否启用本地提醒线程（默认 `true`）
- `FEISHU_ENABLED`：是否启用 Feishu 长连接（默认 `false`）

完整变量与行为开关见 `AGENTS.md` 与 `.env.example`。

## Command Overview
- `/help`
- `/todo add|list|get|update|delete|done|search`
- `/schedule add|list|get|update|delete|repeat|view`
- `/history list|search`
- `/view list|<all|today|overdue|upcoming|inbox>`

说明：
- `/todo add|update` 支持 `--tag --priority --due --remind`
- `/schedule add|update` 支持 `--duration --remind --interval --times --remind-start`
- 非 `/` 开头输入会进入 plan/replan 流程；thought 使用 tool-calling 并以结构化参数直接执行本地动作（不走 `/todo` 命令串）
- 当前 thought 工具链路不支持 thinking 模式（例如 `deepseek-reasoner`）；检测到 reasoning 输出会直接报错并终止该轮任务

## Project Structure
- `assistant_app/cli.py`：交互入口与 CLI 主循环
- `assistant_app/agent.py`：命令分发与自然语言流程编排
- `assistant_app/planner_plan_replan.py`：plan/replan 核心循环
- `assistant_app/db.py`：SQLite 数据访问
- `assistant_app/llm.py`：模型网关
- `tests/`：单元测试
- `main.py`：本地启动入口

## Storage
- 默认数据库：`assistant.db`
- 初始化 SQL：`sql/init_assistant_db.sql`
- 默认日志（均为 JSON Lines）：
  - `logs/app.log`：统一日志文件（app/llm_trace/feishu 都写入该文件）
  - 以上路径可通过环境变量覆盖；`LLM_TRACE_LOG_PATH` / `FEISHU_LOG_PATH` 默认跟随 `APP_LOG_PATH`
  - 置空表示禁用对应日志输出

## Logging
- 日志格式：统一 JSON Lines（每行一个 JSON 对象），核心字段包含 `ts`、`level`、`logger`。
- 常见排障字段：
  - `event`：事件名，例如 `llm_request`、`timer_tick`、`user_profile_read_failed`
  - `context`：事件上下文（message_id、call_id、路径、统计值等）
- 快速排查示例：
```bash
# 看最近 30 条日志
tail -n 30 logs/app.log

# 看通用错误日志
rg '"level": "ERROR"|"level": "WARNING"' logs/app.log

# 看 Feishu 任务中断链路
rg 'interrupted|done reaction|ack reaction' logs/app.log
```

## Test
```bash
python -m unittest discover -s tests -p "test_*.py"
```

## License
MIT License，详见 `LICENSE`。
