# CLI AI Personal Assistant (Current Code Snapshot)

一个中文优先的本地 CLI 个人助手。当前代码已实现：
- 自然语言任务执行（plan -> thought -> act -> observe -> replan）
- thought 阶段默认使用 chat tool-calling（结构化参数）直接调用本地系统函数
- 待办管理（CRUD、标签、优先级、视图、搜索、提醒）
- 日程管理（CRUD、时长、重复规则、提醒、日历视图）
- 历史会话持久化与检索
- 本地提醒线程（待办、单次日程、重复日程）
- 可选 Feishu 长连接接入
- Feishu 任务执行中可异步回传进度：plan 完成后的扩展目标（`任务目标：...`）与子任务完成状态（默认直出，不走 persona 重写）

## Runtime Requirements
- Python 3.10+
- SQLite（Python 标准库 `sqlite3`）
- DeepSeek/OpenAI-compatible API Key（通过 `.env` 配置）

## Quick Start
推荐先执行一键初始化脚本：
```bash
./scripts/bootstrap.sh
# 开发场景可安装 dev 依赖
./scripts/bootstrap.sh --dev
```

它会自动完成：
- 创建 `.venv`（若不存在）
- 安装依赖（默认 `pip install -e .`）
- 初始化 `.env`（若不存在则由 `.env.example` 复制）
- 初始化 SQLite 表结构

也支持按需跳过：
```bash
./scripts/bootstrap.sh --skip-install
./scripts/bootstrap.sh --skip-db
./scripts/bootstrap.sh --force-env
```

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

可选：使用启动脚本（支持后台启动/重启）
```bash
# 后台启动
./scripts/assistant.sh start

# 以别名启动多实例（会使用独立 pid/stdin/log 文件）
./scripts/assistant.sh start work
./scripts/assistant.sh --alias sidecar start

# 重启
./scripts/assistant.sh restart

# 针对某个别名查看状态 / 停止
./scripts/assistant.sh status work
./scripts/assistant.sh stop work

# 查看状态 / 停止
./scripts/assistant.sh status
./scripts/assistant.sh list
./scripts/assistant.sh list work
./scripts/assistant.sh stop

# 当前终端前台运行（等价于 python main.py）
./scripts/assistant.sh run
```

说明：
- `start`/`restart` 默认会先从 `origin/<当前分支>` 拉取：
  - 远端领先：执行 fast-forward 合并；
  - 本地领先：跳过合并，仅启动/重启；
  - 分叉（双方都有新提交）：报错并退出，需先手动处理分支同步。
- 如需跳过自动拉取，可临时执行：`ASSISTANT_AUTO_PULL=false ./scripts/assistant.sh start`
- 可用 `ASSISTANT_ALIAS` 设置默认别名，例如：`ASSISTANT_ALIAS=work ./scripts/assistant.sh start`

## Core Environment Variables
- `.env` 加载优先级最高：若系统环境与 `.env` 同名，最终以 `.env` 值为准
- `DEEPSEEK_API_KEY`：必填
- `DEEPSEEK_BASE_URL`：默认 `https://api.deepseek.com`
- `DEEPSEEK_MODEL`：默认 `deepseek-chat`
- `LLM_TEMPERATURE`：所有 LLM 调用温度（默认 `0.3`，范围 `0.0~2.0`）
- `ASSISTANT_DB_PATH`：SQLite 路径（默认 `assistant.db`）
- `USER_PROFILE_PATH`：user_profile 文件路径（用于计划上下文与自动刷新）
- `USER_PROFILE_REFRESH_ENABLED`：是否启用 user_profile 自动刷新（默认 `true`）
- `SEARCH_PROVIDER`：搜索 provider（`bocha|bing`）
- `BOCHA_API_KEY`：当 provider 为 `bocha` 时推荐配置
- `TIMER_ENABLED`：是否启用本地提醒线程（默认 `true`）
- `FEISHU_ENABLED`：是否启用 Feishu 长连接（默认 `false`）

完整变量与行为开关以 `AGENTS.md` 为准；`.env.example` 提供常用配置示例。

## Command Overview
- `/help`
- `/version`
- `/profile refresh`
- `/todo add|list|get|update|delete|done|search`
- `/schedule add|list|get|update|delete|repeat|view`
- `/history list|search`

说明：
- `/todo add|update` 支持 `--tag --priority --due --remind`
- `/schedule add|update` 支持 `--tag --duration --remind --interval --times --remind-start`
- `/schedule list` 支持 `--tag`，`/schedule view` 支持 `--tag` 过滤
- 非 `/` 开头输入会进入 plan/replan 流程；thought 标准路径使用 tool-calling 结构化参数直接执行本地动作（保留旧模型命令串兼容兜底，非标准契约）
- `/profile refresh` 会立即执行一次画像刷新并返回最新 profile 文件内容（同自动刷新链路）
- `/version` 返回启动时从 `pyproject.toml` 读取并缓存的版本（格式：`当前版本：v<version>`；读取失败返回 `当前版本：unknown`）
- plan 阶段要求返回 `status/goal/plan`；其中 `goal` 为扩展后的执行目标，并会覆盖该任务后续上下文中的原始用户输入
- plan/replan 中 `plan` 使用对象项契约：`task/completed/tools`；初始 plan 的 `completed` 固定为 `false`
- thought 每轮仅暴露当前子任务可用 `tools`，并在运行时自动补齐 `ask_user`/`done`（若缺失才补，最终去重）
- 若启用 Feishu，plan 成功后会异步推送一条 `任务目标：<扩展 goal>` 进度消息（每任务仅一次，replan 不重复发送）
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
- 数据库表结构会在启动时由 `assistant_app.db.AssistantDB` 自动初始化
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
