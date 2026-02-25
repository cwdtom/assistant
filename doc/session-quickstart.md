# Session Quickstart（当前版本）

## 1. 项目一句话

本项目是一个本地优先 CLI 个人助手：支持待办/日程管理，并通过 **plan -> thought -> act -> observe -> replan**（纯 plan-only）处理自然语言任务。

## 2. 当前系统形态（重要）

- 自然语言输入：统一走 plan->thought 主循环；每个子任务的 thought->act->observe 循环完成后触发 replan 跟进进度，并由 replan 决定外层继续或结束（不再走 chat/legacy intent 分支）。
- thought 契约：`status=ask_user` 负责澄清提问；`status=continue` 仅允许 `todo|schedule|internet_search|history_search` 工具动作。
- planner 上下文包含 `time_unit_contract`，明确时长/间隔/次数/日期格式单位，供 plan/thought/replan 共用。
- slash 命令：`/help`、`/history`、`/todo`、`/schedule`、`/view` 仍走确定性命令执行路径。
- CLI 内置本地定时提醒线程（默认开启）：V1 自动触发待办提醒、单次日程提醒与重复日程 occurrence 级提醒，输出 `提醒> ...`。
- 搜索：默认优先 Bocha（可通过 env 切换 provider），缺少 Bocha key 时自动回退 Bing；实现已解耦为 `SearchProvider` 可替换。
- CLI 反馈：输出灰色“进度>”过程日志（可通过 env 关闭颜色）。
- 可选人格化改写：replan 收口后的最终答复与本地提醒文案都可按人设做一轮润色（失败回退原文）；最终答复会引导为“先结论后细节”，并可由模型自行判断是否拆成多条。
- 可选 Feishu 长连接（单聊模式）：与 CLI 同进程后台运行，默认先发消息表情回执 + 内存去重 + 先按空行做多条语义拆分再分片发送 + 发送失败重试 3 次。

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
  - 搜索 Provider 抽象与 Bocha/Bing provider 实现及工厂选择逻辑。
- `assistant_app/llm.py`
  - OpenAI-compatible SDK 封装。

## 4. 关键环境变量（运行时）

基础：
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `ASSISTANT_DB_PATH`

策略参数（已全部 env 化）：
- `PLAN_REPLAN_MAX_STEPS`（默认 `20`）
- `PLAN_REPLAN_RETRY_COUNT`（默认 `2`）
- `PLAN_OBSERVATION_CHAR_LIMIT`（默认 `10000`）
- `PLAN_OBSERVATION_HISTORY_LIMIT`（默认 `100`）
- `PLAN_CONTINUOUS_FAILURE_LIMIT`（默认 `2`）
- `TASK_CANCEL_COMMAND`（默认 `取消当前任务`）
- `INTERNET_SEARCH_TOP_K`（默认 `3`）
- `SEARCH_PROVIDER`（默认 `bocha`，支持 `bocha|bing`）
- `BOCHA_API_KEY`（Bocha 搜索 key，缺失时回退 Bing）
- `BOCHA_SEARCH_SUMMARY`（默认 `true`）
- `SCHEDULE_MAX_WINDOW_DAYS`（默认 `31`）
- `INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS`（默认 `31`）
- `CLI_PROGRESS_COLOR`（默认 `gray`，支持 `gray|off`）
- `PERSONA_REWRITE_ENABLED`（默认 `true`）
- `ASSISTANT_PERSONA`（默认空；设置后启用人设润色）
- `LLM_TRACE_LOG_PATH`（默认 `logs/llm_trace.log`，留空可关闭）
- `TIMER_ENABLED`（默认 `true`）
- `TIMER_POLL_INTERVAL_SECONDS`（默认 `15`）
- `TIMER_LOOKAHEAD_SECONDS`（默认 `30`）
- `TIMER_CATCHUP_SECONDS`（V1 固定按 `0` 处理）
- `TIMER_BATCH_LIMIT`（默认 `200`）
- `REMINDER_DELIVERY_RETENTION_DAYS`（默认 `30`）
- `FEISHU_ENABLED`（默认 `false`）
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`（启用时必填）
- `FEISHU_ALLOWED_OPEN_IDS`（默认空，不限制）
- `FEISHU_SEND_RETRY_COUNT`（默认 `3`）
- `FEISHU_TEXT_CHUNK_SIZE`（默认 `1500`）
- `FEISHU_DEDUP_TTL_SECONDS`（默认 `600`）
- `FEISHU_LOG_PATH`（默认 `logs/feishu.log`）
- `FEISHU_LOG_RETENTION_DAYS`（默认 `7`）
- `FEISHU_ACK_REACTION_ENABLED`（默认 `true`）
- `FEISHU_ACK_EMOJI_TYPE`（默认 `OK`）

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

- `1adbdd4` feat: add persona rewrite for final replies and reminders
- `2e7af7f` feat: add Bocha search provider with configurable runtime selection
- `17f6ff3` feat: include recent chat turns in plan and replan context
- `2b2980c` feat: persist chat turns and expose history search to planner
- `7f7860b` feat: wire observation history limit into planner runtime

## 7. 已知边界 / 风险

- Bocha 接口返回结构或鉴权策略变化时，可能需要同步调整 provider 解析逻辑。
- 在未配置 `BOCHA_API_KEY` 时会自动回退 Bing，仍存在 Bing HTML 结构变更的兼容性风险。
- planner 若持续输出低质量动作，会在步数上限后兜底返回建议。
- done 文案质量依赖模型输出，必要时可再触发一轮查询校验细节。

## 8. 后续 session 建议做法

1. 先看本文件 + 根 `README.md`，再按需翻历史文档。
2. `doc/archive/` 下文档是历史快照，不保证与当前实现逐行一致；实现口径以 `README.md`、本文件和源码为准。
3. 修改行为时优先补单测（`tests/test_agent.py` / `tests/test_cli.py` / `tests/test_config.py` / `tests/test_search.py` / `tests/test_persona.py`）。
4. 涉及命令语义或配置项，务必同步更新 `README.md` 与 `.env.example`。
