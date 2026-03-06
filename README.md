# CLI AI Personal Assistant (Current Code Snapshot)

一个中文优先的本地 CLI 个人助手。当前代码已实现：
- 自然语言任务执行（plan -> thought -> act -> observe -> replan）
- thought 阶段默认使用 chat tool-calling（结构化参数）直接调用本地系统函数
- 日程管理（CRUD、时长、重复规则、提醒、日历视图）
- 碎片想法管理（CRUD，最小字段：content + status）
- 历史会话持久化与检索
- 本地提醒线程（单次日程、重复日程）
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

Playwright 依赖安装（`internet_search_fetch_url` 需要）：
```bash
# 安装 Python 包（若上一步已执行 pip install -e .，可跳过）
pip install playwright

# 安装 Chromium 浏览器内核（必需）
python -m playwright install chromium
```

常见问题：
- 若出现 `Executable doesn't exist ...`，通常是未执行 `playwright install chromium`。
- 若在受限环境中运行（例如沙箱/容器），可能需要调整运行权限后再启动 Playwright。

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
- LLM 配置仅支持 `DEEPSEEK_*` 环境变量，不再兼容 `OPENAI_*` 同名配置
- `LLM_TEMPERATURE`：默认 LLM 调用温度（默认 `1.3`，范围 `0.0~2.0`；`user_profile refresh` 固定使用 `0.0`）
- `ASSISTANT_DB_PATH`：SQLite 路径（默认 `assistant.db`）
- `USER_PROFILE_PATH`：user_profile 文件路径（用于计划上下文与自动刷新）
- `USER_PROFILE_REFRESH_ENABLED`：是否启用 user_profile 自动刷新（默认 `true`）
- `SEARCH_PROVIDER`：搜索 provider（`bocha|bing`）
- `BOCHA_API_KEY`：当 provider 为 `bocha` 时推荐配置
- `BOCHA_SEARCH_SUMMARY`：是否请求 Bocha 返回 summary（默认 `true`）
- `INTERNET_SEARCH_TOP_K`：Bocha rerank 的 `rerankTopK` 目标值（默认 `3`）
- `TIMER_ENABLED`：是否启用本地提醒线程（默认 `true`）
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`：配置后自动启用 Feishu 长连接
- `FEISHU_CALENDAR_ID`：配置后自动启用本地日程与 Feishu 日历同步；需同时配置 Feishu 凭据
- `FEISHU_CALENDAR_RECONCILE_INTERVAL_MINUTES`：Feishu 为准对账间隔分钟（默认 `10`）
- `FEISHU_CALENDAR_BOOTSTRAP_PAST_DAYS`：启动重建窗口回看天数（默认 `2`）
- `FEISHU_CALENDAR_BOOTSTRAP_FUTURE_DAYS`：启动重建窗口前瞻天数（默认 `5`）
  - 启动重建窗口按自然日对齐：`start=(today-past_days) 00:00:00`，`end=(today+future_days) 23:59:59`
- `PROACTIVE_REMINDER_TARGET_OPEN_ID`：配置后自动启用主动提醒目标；需同时配置 Feishu 凭据
- `PROACTIVE_REMINDER_INTERVAL_MINUTES`：主动提醒评估间隔分钟（默认 `60`，最小 `60`）
- `PROACTIVE_REMINDER_LOOKAHEAD_HOURS`：主动提醒上下文前瞻窗口小时数（默认 `24`）
- `PROACTIVE_REMINDER_NIGHT_QUIET_HINT`：夜间静默软约束提示（默认 `23:00-08:00`）

完整变量与行为开关以 `AGENTS.md` 为准；`.env.example` 提供最小可运行模板，额外调优项可按需从 `AGENTS.md` 拾取。

## Command Overview
- `/help`
- `/version`
- `/profile refresh`
- `/schedule add|list|get|update|delete|repeat|view`
- `/history list|search`
- `/thoughts add|list|get|update|delete`

说明：
- `/schedule add|update` 支持 `--tag --duration --remind --interval --times --remind-start`
- `/schedule list` 支持 `--tag`，`/schedule view` 支持 `--tag` 过滤
- `/thoughts list` 支持 `--status <未完成|完成|删除>`；默认仅展示 `未完成|完成`
- `/thoughts delete` 为软删除（状态置为 `删除`）
- 非 `/` 开头输入会进入 plan/replan 流程；thought 标准路径使用 tool-calling 结构化参数直接执行本地动作（保留旧模型命令串兼容兜底，非标准契约）
- `/profile refresh` 会立即执行一次画像刷新并返回最新 profile 文件内容（同自动刷新链路）
- `/version` 返回启动时从 `pyproject.toml` 读取并缓存的版本（格式：`当前版本：v<version>`；读取失败返回 `当前版本：unknown`）
- plan 阶段要求返回 `status/goal/plan`；其中 `goal` 为扩展后的执行目标，并会覆盖该任务后续上下文中的原始用户输入
- plan/replan 中 `plan` 使用对象项契约：`task/completed/tools`；初始 plan 的 `completed` 固定为 `false`；plan 阶段允许输出空数组（ack-only）
- 当用户输入是对上一轮最终回答的简短确认/致谢（例如“谢谢”“好的”“明白了”）时，plan 可输出空计划并直接结束：不进入 thought/replan，不落库 `chat_history`
- thought 每轮仅暴露当前子任务可用 `tools`，并在运行时自动补齐 `ask_user`/`done`（若缺失才补，最终去重）；当子任务工具含 group 时，会展开为：`schedule` -> `schedule_add|schedule_list|schedule_view|schedule_get|schedule_update|schedule_delete|schedule_repeat`，`internet_search` -> `internet_search_tool|internet_search_fetch_url`，`history` -> `history_list|history_search`，`thoughts` -> `thoughts_add|thoughts_list|thoughts_get|thoughts_update|thoughts_delete`（记录碎片想法）
- Bocha 搜索请求固定使用 `count=50`，并默认启用 rerank（`rerankModel=gte-rerank`，`rerankTopK=INTERNET_SEARCH_TOP_K`）
- 当 rerank 请求失败时，会自动降级重试为非 rerank Bocha 搜索
- `internet_search` 在收到裸 `http/https` URL 输入时会自动按 `fetch_url` 路径执行（不再按关键词搜索）
- `fetch_url` 默认先走 Playwright；若 Playwright 失败，会自动降级为 `requests` 直连抓取并提取文本
- 当前搜索展示不再做本地二次截断，按 provider 返回结果输出
- Bocha 结果摘要提取规则：优先 `summary`，缺失时回退 `snippet`
- 若启用 Feishu，非空 plan 成功后会异步推送一条 `任务目标：<扩展 goal>` 进度消息（每任务仅一次，replan 不重复发送）
- 若启用 Feishu，ack-only 空计划分支仅发送 ACK/DONE reaction，不发送正文文本
- 当前 thought 工具链路不支持 thinking 模式（例如 `deepseek-reasoner`）；检测到 reasoning 输出会直接报错并终止该轮任务
- 若启用主动提醒：timer 会按配置周期触发独立 Proactive ReAct 评估，并在 `notify=true` 时向固定 `open_id` 主动发送 Feishu 文本
- Proactive ReAct 提示词会注入 `USER_PROFILE_PATH` 内容（可用时），并基于未来 24 小时 schedule + 过去 24 小时 chat_history 进行决策
- 若启用 Feishu 日历同步：同一条日程按 `title + description(tag) + start + end`（分钟粒度）严格匹配；启动时会先按窗口执行本地->飞书重建；首次飞书->本地对账会延后到一个 `FEISHU_CALENDAR_RECONCILE_INTERVAL_MINUTES` 周期后
- Feishu 日历周期对账由 timer 驱动；当 `TIMER_ENABLED=false` 时不会执行周期对账

## Project Structure
- `assistant_app/cli.py`：交互入口与 CLI 主循环
- `assistant_app/agent.py`：命令分发与自然语言流程编排
- `assistant_app/agent_components/`：`agent.py` 拆分后的组件目录（command handlers / planner loop / parsing utils / render helpers / tools / shared models）
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
- Feishu 通道日志会记录消息内容文本：
  - 入站：`feishu inbound message received`（含 `message_id/chat_id/open_id/text`）
  - 出站：`feishu response sent`、`feishu subtask progress sent`、`feishu proactive response sent`（含 `text`）
  - 若需避免记录消息正文，可将 `FEISHU_LOG_PATH` 置空禁用该 logger 输出
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
