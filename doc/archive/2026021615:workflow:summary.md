# 当前版本总结（代码 + 文档口径）

## 1. 基线信息

- 分支：`thought-workflow`
- 基线提交：`51cacb1`（`refactor: split planner modules and tighten loop workflow contracts`）
- 总体形态：自然语言任务走 **plan -> thought -> act -> observe -> replan** 双层循环；slash 命令走确定性路径。

## 2. 当前代码逻辑（以实现为准）

### 2.1 执行主链路

1. 非 slash 输入进入任务编排：
   - 外层：计划推进与收口判定
   - 内层：单个子任务的 reAct 循环
2. `plan` 仅在新任务启动时执行一次。
3. 每个子任务在内层完成后触发 `replan`，由 `replan` 决定继续执行还是最终收口。
4. `thought.done` 仅表示“当前子任务完成”，不直接输出最终结论。

### 2.2 模块边界

- `assistant_app/agent.py`
  - 负责编排（outer/inner loop）、工具执行、状态推进、进度输出。
- `assistant_app/planner_plan_replan.py`
  - `PLAN_ONCE_PROMPT`、`REPLAN_PROMPT` 与对应 JSON 归一化。
- `assistant_app/planner_thought.py`
  - `THOUGHT_PROMPT` 与 thought JSON 归一化。
- `assistant_app/planner_common.py`
  - 公共 `normalize_plan_items()`，消除 plan/replan 与 thought 的重复实现。

### 2.3 契约与约束

- thought 严格区分：
  - `status=ask_user`：仅澄清提问
  - `status=continue`：仅执行 `todo|schedule|internet_search`
- planner 上下文提供 `time_unit_contract`，统一时间单位口径：
  - `--duration`、`--interval`：分钟
  - `--times`：次数
  - 时间格式：`YYYY-MM-DD HH:MM`（本地时间）

## 3. doc 目录逻辑（文档治理口径）

### 3.1 推荐读取顺序

1. `doc/session-quickstart.md`：当前有效事实与运行入口
2. 根 `README.md`：对外使用说明、命令与配置
3. `doc/archive/`：历史快照与决策追溯

### 3.2 当前有效文档 vs 历史快照

- 当前有效口径：
  - `README.md`
  - `doc/session-quickstart.md`
- 历史快照（不要求逐行与现代码一致）：
  - `doc/archive/*`

> 结论：后续开发/排障应优先遵循 `README.md` + `doc/session-quickstart.md` + 源码；`archive` 用于追溯“为什么这样改”。

## 4. 一致性检查结论

- 代码主流程与当前文档口径一致：
  - 外层负责计划推进与收口判定
  - 内层负责子任务 reAct 执行
  - `replan` 为最终收口决策点
- 时间单位契约已同时在代码上下文与文档中体现，减少“小时/分钟混淆”。
- tests 已覆盖核心流程与契约边界（含 thought 契约与 replan 触发节奏）。

## 5. 验证记录（最近一次）

- `python3 -m ruff check assistant_app tests`：通过
- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p "test_*.py"`：`Ran 119 tests`，`OK`

## 6. 残余风险（非阻断）

1. 时间单位正确性目前仍以 LLM 遵守契约为主，执行层尚未增加语义级硬校验（例如“3小时”自动换算为 180）。
2. `doc/archive` 为快照，信息可能过期；新同学若跳过 quickstart 直接读 archive，仍可能产生理解偏差。

