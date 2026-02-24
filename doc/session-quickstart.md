# Session Quickstart（当前版本）

## 1. 项目一句话

本项目是一个本地优先 CLI 个人助手：支持待办/日程管理，并通过 **plan -> thought -> act -> observe -> replan**（纯 plan-only）处理自然语言任务。

## 2. 当前系统形态（重要）

- 自然语言输入：统一走 plan->thought 主循环；每个子任务的 thought->act->observe 循环完成后触发 replan 跟进进度，并由 replan 决定外层继续或结束（不再走 chat/legacy intent 分支）。
- thought 契约：`status=ask_user` 负责澄清提问；`status=continue` 仅允许 todo/schedule/internet_search 工具动作。
- planner 上下文包含 `time_unit_contract`，明确时长/间隔/次数/日期格式单位，供 plan/thought/replan 共用。
- slash 命令：`/todo`、`/schedule`、`/view` 仍走确定性命令执行路径。
- CLI 内置本地定时提醒线程（默认开启）：V1 自动触发待办提醒、单次日程提醒与重复日程 occurrence 级提醒，输出 `提醒> ...`。
- 搜索：默认 Bing，实现已解耦为 `SearchProvider` 可替换。
- CLI 反馈：输出灰色“进度>”过程日志（可通过 env 关闭颜色）。

## 3. 核心代码入口

- `assistant_app/cli.py`
  - CLI 启动、进度输出、配置注入。
- `assistant_app/agent.py`
  - 外层流程编排（plan 初始化、inner reAct 驱动、replan 收口判定）、工具执行、slash 命令路由。
- `assistant_app/planner_plan_replan.py`
  - plan/replan 的提示词与 JSON 契约归一化逻辑。
- `assistant_app/planner_thought.py`
  - thought 的提示词与 JSON 契约归一化逻辑。
- `assistant_app/config.py`
  - `.env` 与环境变量加载（含策略参数）。
- `assistant_app/db.py`
  - SQLite 模型与读写（todo/schedule/recurrence）。
- `assistant_app/search.py`
  - 搜索 Provider 抽象与 Bing 默认实现。
- `assistant_app/llm.py`
  - OpenAI-compatible SDK 封装。

## 4. 关键环境变量（运行时）

基础：
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `ASSISTANT_DB_PATH`

策略参数（已全部 env 化）：
- `PLAN_REPLAN_MAX_STEPS`
- `PLAN_REPLAN_RETRY_COUNT`
- `PLAN_OBSERVATION_CHAR_LIMIT`
- `PLAN_OBSERVATION_HISTORY_LIMIT`
- `PLAN_CONTINUOUS_FAILURE_LIMIT`
- `TASK_CANCEL_COMMAND`
- `INTERNET_SEARCH_TOP_K`
- `SCHEDULE_MAX_WINDOW_DAYS`
- `INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS`
- `CLI_PROGRESS_COLOR`（`gray|off`）
- `LLM_TRACE_LOG_PATH`（默认 `logs/llm_trace.log`，留空可关闭）
- `TIMER_ENABLED`
- `TIMER_POLL_INTERVAL_SECONDS`
- `TIMER_LOOKAHEAD_SECONDS`
- `TIMER_CATCHUP_SECONDS`（V1 固定按 0 处理）
- `TIMER_BATCH_LIMIT`
- `REMINDER_DELIVERY_RETENTION_DAYS`

参考示例：`.env.example`

## 5. 常用开发命令

```bash
# 运行 CLI
python main.py

# 全量单元测试
python3 -m unittest discover -s tests -p "test_*.py"

# lint
python3 -m ruff check assistant_app tests
```

## 6. 最近关键演进（按提交）

- `5e98728` feat: move runtime strategy knobs into env config
- `5060f41` refactor: enforce pure plan-only flow
- `655aa11` fix: only report plan list when replan changes it
- `836fd4e` feat: implement plan-replan tool loop with progress output
- `2d38036` fix: harden recurrence windowing and conflict checks

## 7. 已知边界 / 风险

- 搜索结果解析依赖 Bing 页面结构，未来可能需要调整解析规则。
- planner 若持续输出低质量动作，会在步数上限后兜底返回建议。
- done 文案质量依赖模型输出，必要时可再触发一轮查询校验细节。

## 8. 后续 session 建议做法

1. 先看本文件 + 根 `README.md`，再按需翻历史文档。
2. `doc/archive/` 下文档是历史快照，不保证与当前实现逐行一致；实现口径以 `README.md`、本文件和源码为准。
3. 修改行为时优先补单测（`tests/test_agent.py` / `tests/test_cli.py` / `tests/test_config.py`）。
4. 涉及命令语义或配置项，务必同步更新 `README.md` 与 `.env.example`。
