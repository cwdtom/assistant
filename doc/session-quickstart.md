# Session Quickstart（当前版本）

## 1. 项目一句话

本项目是一个本地优先 CLI 个人助手：支持待办/日程管理，并通过 **plan -> thought -> act -> observe -> replan**（纯 plan-only）处理自然语言任务。

## 2. 当前系统形态（重要）

- 自然语言输入：统一走 plan->thought 主循环；仅在用户澄清后触发 replan（不再走 chat/legacy intent 分支）。
- slash 命令：`/todo`、`/schedule`、`/view` 仍走确定性命令执行路径。
- 搜索：默认 Bing，实现已解耦为 `SearchProvider` 可替换。
- CLI 反馈：输出灰色“进度>”过程日志（可通过 env 关闭颜色）。

## 3. 核心代码入口

- `assistant_app/cli.py`
  - CLI 启动、进度输出、配置注入。
- `assistant_app/agent.py`
  - plan/thought/replan 主循环、工具执行、slash 命令路由。
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
2. 修改行为时优先补单测（`tests/test_agent.py` / `tests/test_cli.py` / `tests/test_config.py`）。
3. 涉及命令语义或配置项，务必同步更新 `README.md` 与 `.env.example`。
