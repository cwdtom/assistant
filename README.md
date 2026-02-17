# CLI AI Personal Assistant (MVP)

一个中文优先的本地 CLI 个人助手，支持：
- 自然语言任务执行（plan-only，DeepSeek 优先，兼容 OpenAI-compatible API）
- 待办管理
- 日程管理

## Quick Start

1. 创建虚拟环境并安装依赖
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
# 开发工具（ruff/mypy/pre-commit）
pip install -e ".[dev]"
```

2. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

默认模型：
- `DEEPSEEK_MODEL=deepseek-chat`（通用对话）
- 可选 `deepseek-reasoner`（更强推理，延迟通常更高）

可选运行参数（均支持写入 `.env`）：
- `PLAN_REPLAN_MAX_STEPS`：plan 循环最大执行步数（默认 `20`）
- `PLAN_REPLAN_RETRY_COUNT`：planner JSON 失败重试次数（默认 `2`）
- `PLAN_OBSERVATION_CHAR_LIMIT`：单条 observation 最大保留字符（默认 `10000`）
- `PLAN_CONTINUOUS_FAILURE_LIMIT`：连续失败兜底阈值（默认 `2`）
- `TASK_CANCEL_COMMAND`：取消当前任务命令文本（默认 `取消当前任务`）
- `INTERNET_SEARCH_TOP_K`：互联网搜索返回条数（默认 `3`）
- `SCHEDULE_MAX_WINDOW_DAYS`：日程查询窗口最大天数（默认 `31`）
- `INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS`：无限重复冲突检测预览天数（默认 `31`）
- `CLI_PROGRESS_COLOR`：进度输出颜色，支持 `gray|off`（默认 `gray`）
- `LLM_TRACE_LOG_PATH`：LLM 请求/响应日志文件路径（默认 `logs/llm_trace.log`，留空可关闭）

3. 运行
```bash
python main.py
# 或 assistant
```

## 命令
- `/help`
- `/view list`
- `/view <all|today|overdue|upcoming|inbox> [--tag <标签>]`
- `/todo add <内容> [--tag <标签>] [--priority <>=0>] [--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]`
- `/todo list [--tag <标签>] [--view <all|today|overdue|upcoming|inbox>]`
- `/todo search <关键词> [--tag <标签>]`
- `/todo get <id>`
- `/todo update <id> <内容> [--tag <标签>] [--priority <>=0>] [--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]`
- `/todo delete <id>`
- `/todo done <id>`
- `/schedule add <YYYY-MM-DD HH:MM> <标题> [--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] [--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]`
- `/schedule get <id>`
- `/schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM]`
- `/schedule update <id> <YYYY-MM-DD HH:MM> <标题> [--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] [--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]`
- `/schedule repeat <id> <on|off>`
- `/schedule delete <id>`
- `/schedule list`
- 待办和日程均支持增删改查（CRUD）
- 日程支持 `duration_minutes` 字段（单位分钟，新增默认 `60`；更新时不传则保留原值）
- 日程支持重复创建（interval 分钟 + times），重复规则单独存储，查询时与普通日程拼接
- 日程支持提醒时间字段（`--remind`，不填则不提醒）
- 重复日程支持提醒开始时间字段（`--remind-start`，仅用于重复规则）
- 当前版本仅存储并展示提醒相关字段，尚未实现自动提醒触发
- 当提供 `--interval` 但省略 `--times` 时，默认重复次数为 `-1`（无限循环）
- 重复规则支持启用/停用（停用后仅保留基础日程，不展开后续重复实例）
- `/schedule list` 默认展示“从前天开始向后 1 个月”的窗口，最大查询范围固定为 1 个月
- `/schedule view` 会按传入锚点（day/week/month）计算时间窗口查询，不依赖“当前时间”展开重复日程
- CLI 查看日程时会展示重复相关字段（重复间隔、重复次数、重复启用状态）
- 日程支持日历视图（day/week/month）
- 日程新增/修改时会做冲突检测（时间区间重叠会提示冲突，会考虑时长）
- 对 `times=-1` 的无限重复，冲突检测按“起始时间起未来 31 天”窗口校验
- 待办支持关键词搜索（可选按标签范围搜索）
- 待办支持视图（all/today/overdue/upcoming/inbox）
- 待办支持 `priority` 字段（默认 `0`，数值越小优先级越高，最小为 `0`）
- 待办和日程查询结果默认以表格样式输出，便于在 CLI 快速浏览
- 日程列表和日程详情会展示“时长(分钟)”列
- 待办列表和待办详情均展示标签、优先级、创建时间、完成时间、截止时间、提醒时间（提醒需配合截止时间）
- 进入 CLI 和退出 CLI 时，会自动清空当前终端显示历史（scrollback）
- 自然语言任务会实时输出循环进度：步骤进度、计划列表、工具执行结果与完成情况
- 支持自然语言命令（plan -> thought -> act -> observe -> replan 循环）
- plan 仅在每个新任务开始时执行一次；每个子任务的 thought->act->observe 循环完成后会触发 replan 跟进进度（澄清恢复后也会触发），并由 replan 决定外层是继续还是收口输出
- thought 会围绕当前计划项逐步决策，并在 todo/schedule/internet_search/ask_user 四种动作间切换
- thought JSON 契约严格区分：`ask_user` 必须使用 `status=ask_user`，`status=continue` 仅允许 `todo|schedule|internet_search`
- planner 上下文会显式提供时间单位契约（`time_unit_contract`），统一约束分钟/次数/时间格式，避免 `3小时 -> --duration 3` 这类误用
- ask_user 工具触发时，会以 `请确认：...` 发起单问题澄清；输入 `TASK_CANCEL_COMMAND` 对应文本可终止当前循环任务
- internet_search 默认使用 Bing 作为搜索源，返回 Top-3 摘要和链接（实现解耦，可替换 provider）
- 自然语言任务默认最多执行 20 个决策步骤（含 thought/replan/tool 动作，ask_user 等待不计步），超限后会返回“已完成部分 + 未完成原因 + 下一步建议”
- 支持自然语言命令，示例：
  - `添加待办 买牛奶，标签是 life，优先级 1，截止 2026-02-25 18:00，提醒 2026-02-25 17:30`
  - `查看待办 1`
  - `搜索待办 牛奶`
  - `看一下今天待办`
  - `把待办 1 改成 买牛奶和面包，标签 life，优先级 0，截止 2026-02-26 20:00`
  - `删除待办 1`
  - `完成待办 1`
  - `查看 work 标签的待办`
  - `添加日程 2026-02-15 09:30 站会`
  - `添加日程 2026-02-15 09:30 站会，时长45分钟`
  - `添加日程 2026-02-15 09:30 站会，每周重复三次`
  - `查看 2026-02-15 这一周的日程`
  - `查看日程 1`
  - `把日程 1 改到 2026-02-16 09:30 站会`
  - `删除日程 1`
  - `查看待办`
  - `查看日程`
- 直接输入任意文本：始终进入 plan->thought->act->observe->replan 流程（纯 plan-only，不再走 legacy intent/chat 分支）

## 视图说明
- `all`：全部待办（含已完成）
- `today`：今天到期且未完成
- `overdue`：已逾期且未完成
- `upcoming`：未来 7 天到期且未完成
- `inbox`：未设置截止时间且未完成

## 日历视图说明
- `day`：按天查看日程，参数格式 `YYYY-MM-DD`
- `week`：按周查看日程（周一到周日），参数格式 `YYYY-MM-DD`
- `month`：按月查看日程，参数格式 `YYYY-MM`

## 数据库表结构（SQLite）
- `todos`：待办主表，存储内容、标签、优先级、完成状态、创建/完成时间、截止时间、提醒时间。
- `schedules`：日程主表，存储标题、事件时间、时长、提醒时间、创建时间。
- `recurring_schedules`：重复规则表（关联 `schedules.id`），存储重复开始时间、间隔分钟、重复次数、重复提醒开始时间、启停状态；删除日程会级联删除规则。
- `chat_history`：聊天历史表，存储消息角色、消息内容和创建时间。

## 初始化数据库（可选）
- 初始化 SQL 文件：`sql/init_assistant_db.sql`
- 适用场景：手动创建全新 SQLite 库（不含历史迁移步骤）。
- 示例命令：
```bash
# 按默认库名初始化
sqlite3 assistant.db < sql/init_assistant_db.sql

# 或初始化到自定义路径（与 ASSISTANT_DB_PATH 一致）
sqlite3 /path/to/assistant.db < sql/init_assistant_db.sql
```

## 测试
```bash
python -m unittest discover -s tests -p "test_*.py"
```

## 代码质量（lint/type-check）
```bash
# Ruff lint
ruff check .

# Ruff format
ruff format .

# mypy type check
mypy
```

## pre-commit
```bash
# 安装 git hooks
pre-commit install

# 手动对全仓执行
pre-commit run --all-files
```
