# thought-workflow 改造阶段报告

## a. Commit message（建议）

`refactor: switch loop_agent to plan-thought-act-observe-replan runtime`

## b. Modified Files and Summary of Changes

1. `assistant_app/agent.py`
   - 将自然语言执行主循环改为 `plan -> thought -> act -> observe -> replan`。
   - `plan` 仅在新任务首次输入触发；`replan` 仅在用户澄清后触发。
   - 新增/调整 pending task 状态字段（计划初始化、澄清等待、重规划标记、当前计划项游标）。
   - 新增 thought/replan 计步行为：thought、replan、tool 执行计入 `step_count`，`ask_user` 等待不计步。
   - 在每轮循环开头执行 `step_count >= max_steps` 判定，命中后立即返回兜底结果。
   - 增加当前计划项进度输出（`当前计划项：i/N - ...`）。
   - 计划与重规划解析器收敛为严格契约：仅接受 `planned` / `replanned`。

2. `tests/test_agent.py`
   - 迁移为严格三阶段响应：`planned` + thought 决策 + `replanned`（澄清后）。
   - 新增 thought/replan 失败计步与当前计划项进度相关覆盖。
   - 删除旧的 intent->planner 兼容 helper 和 phase 适配注入逻辑。
   - `FakeLLMClient` 简化为纯顺序响应，保留 `model_call_count` 用于断言。

3. `README.md`
   - 明确自然语言路径为 `plan->thought->act->observe->replan`。

4. `doc/session-quickstart.md`
   - 明确主循环与 replan 触发条件（仅用户澄清后）。

5. `doc/archive/2026021610:thought-workflow:thought-workflow-design.md`
   - 增补“需求澄清记录（冻结）”：
     - thought/replan/tool 计步
     - 解析失败计步
     - 每轮循环开头判定步数上限

## c. Reasons and Purposes of Each Modification

1. **降低规划抖动**：将 replan 限制在澄清后，避免每步重规划造成计划漂移。
2. **提高执行可控性**：通过 thought 推进当前计划项，行为更线性、可解释。
3. **统一失败预算**：解析失败纳入步数预算，防止模型输出异常导致“隐性无限重试”。
4. **提升可观测性**：补充当前计划项进度，便于排查“卡在第几步”。
5. **减少隐式兼容分支**：测试层移除 runtime 适配注入，保证用例契约与生产逻辑一致。

## d. 当前代码潜在问题

1. `plan_replan_retry_count` 在单次 thought/replan 请求内部仍可能触发多次模型调用；当前计步按“模块决策轮次”而非“内部每次重试”统计。若后续希望“每次模型重试都计步”，需要额外改造计步位置。
2. 进度输出依赖模型返回 `current_step` 或计划游标推断；当模型返回质量不稳定时，进度可读性仍受影响。

## e. Unit Test Report

- 执行命令：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p "test_*.py"`
- 结果：`Ran 115 tests in 5.108s`，`OK`

- 执行命令：`python3 -m ruff check assistant_app tests`
- 结果：`All checks passed!`
