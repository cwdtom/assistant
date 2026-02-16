# loop_agent 流程改造设计（仅文档，不含代码）

## 1. 背景

当前 loop_agent 采用 `plan -> act -> observe -> replan`，每执行一步工具都会再次调用 planner。

本次改造目标由需求明确为：

1. 主流程升级为 `plan -> thought -> act -> observe -> replan`。
2. `plan` 仅在每个新任务的第一次用户输入时触发（不是全会话只触发一次）。
3. `replan` 仅在用户完成澄清（ask_user）后触发。
4. 通过 `thought` 模块顺序执行全部计划项，而不是每步都 replan。
5. 本文档先定义改造方案与测试计划，暂不提交代码。

### 1.1 需求澄清记录（冻结）

以下口径已在需求沟通中明确，后续实现与测试需严格遵循：

1. `step_count` 仅将 `thought`、`replan`、工具执行计入预算；`plan_once` 与 `ask_user` 等待不计步。
2. `thought`/`replan` 的解析失败（JSON 失败、字段校验失败）也计步，不可忽略。
3. 步数上限检查必须放在每轮主循环开头，命中后本轮不再执行 thought/replan/tool。

### 1.2 需求修订（2026-02-16）

新增修订口径（覆盖 1.0 版本“replan 仅澄清后触发”的约束）：

1. 严格采用双层语义：
   - 外层：`plan -> reAct -> done or replan`
   - 内层：`thought -> act -> observe`
2. `replan` 触发时机调整为：每个子任务内层 `thought->act->observe` 循环完成后触发一次，用于跟进任务进度。
3. 用户澄清恢复任务后同样需要触发 replan，但不再是唯一触发条件。

---

## 2. 新流程定义

### 2.1 总体状态机

#### 初始用户输入（非 slash）

`INPUT -> PLAN_ONCE -> (THOUGHT_LOOP -> ACT -> OBSERVE -> REPLAN)* -> DONE`

- `PLAN_ONCE`：只执行一次，生成完整计划列表。
- `THOUGHT_LOOP`：围绕“当前计划项”做决策。
- `ACT/OBSERVE`：执行工具并记录 observation。
- 当 thought 判断“当前子任务”完成时输出 done，并交由 replan 决定是否最终收口。

#### 发生澄清场景

`THOUGHT_LOOP -> ASK_USER -> WAIT_USER -> REPLAN -> THOUGHT_LOOP`

- `ASK_USER`：仅当信息不足时触发。
- `WAIT_USER`：等待用户补充。
- `REPLAN`：每个子任务的内层执行（thought->act->observe）循环完成后触发；澄清恢复后也会触发。

### 2.2 核心约束（需求冻结）

1. 每个子任务内层执行（thought->act->observe）循环完成后，都触发一次 replan。
2. plan/replan 产物都是“完整计划列表”；thought 负责逐项推进。
3. thought 必须按计划顺序执行（可在单个计划项内多轮 thought-act-observe，直到该项完成/阻塞）。
4. `plan` 仅在“创建新任务后的首次非 slash 输入”触发一次；同一任务内不重复 plan。
5. 计步口径：`thought` 和 `replan` 都计步；`todo/schedule/internet_search` 计步；`ask_user` 不计步。
6. 连续失败达到阈值时，thought 必须二选一：`ask_user` 或 `done(子任务结束信号)`，禁止无限空转。
7. 等待澄清期间，若用户输入 slash 命令，则先执行 slash，再保持当前 pending 任务不丢失。

### 2.3 计步口径（明确）

统一使用 `step_count` 作为总预算计数器，默认规则：

1. 每轮主循环开头先检查 `step_count >= max_steps`；命中即终止并返回步数上限兜底结果。
2. 每次 thought 决策尝试都会计步（含 JSON 解析失败/字段校验失败），`step_count += 1`。
3. 每次 replan 决策尝试都会计步（含 JSON 解析失败/字段校验失败），`step_count += 1`。
4. 每次执行工具动作（todo/schedule/internet_search），`step_count += 1`。
5. `plan_once` 不计步（仅作为任务初始化）。
6. `ask_user` 与“等待用户输入”阶段不计步。
7. 达到步数上限后，输出“已完成部分 + 未完成原因 + 下一步建议”。

---

## 3. 模块职责拆分

### 3.1 Plan 模块（首次输入）

- 输入：goal + 当前时间 + 工具契约。
- 输出：计划列表（`plan_items`）。
- 特点：一次性生成，不在每步后调用。

### 3.2 Thought 模块（执行引擎）

- 输入：
  - 当前计划项（`current_plan_item`）
  - 历史 observation
  - 澄清历史
  - 已执行统计
- 输出（单次 thought 决策）：
  - 执行某个工具动作（todo/schedule/internet_search）
  - 标记当前计划项完成（进入下一项）
  - ask_user（信息不足）
  - done（全部目标完成）
- 特点：不改写全局计划，仅推进执行。

### 3.3 Replan 模块（每个子任务完成后）

- 触发时机：每个子任务内层执行完成后；用户回答 ask_user 恢复任务后同样触发。
- 输入：原 goal + 新澄清 + 截至当前 observation + 剩余计划。
- 输出：更新后的计划列表（执行游标在系统侧默认重置为 0）。
- 特点：用于跟进执行进度、吸收 observation，并动态更新后续计划。

---

## 4. 数据结构调整建议

以 `PendingPlanTask` 为中心，建议新增/调整字段：

- `plan_initialized: bool`：是否已执行首次 plan。
- `plan_items: list[str]`：当前有效计划列表。
- `current_plan_index: int`：当前执行到第几个计划项。
- `awaiting_clarification: bool`：是否处于 ask_user 等待态。
- `needs_replan: bool`：是否需要在下一轮触发 replan（由“澄清恢复”或“完成一次工具执行”置为 true）。
- `last_thought_snapshot: str | None`：最近一次 thought 摘要（用于进度日志，可选）。

保留现有：

- `observations`
- `step_count/successful_steps/failed_steps`
- `clarification_history`
- `last_ask_user_question`

---

## 5. 协议（JSON 契约）调整建议

为降低一次性改造风险，建议拆成三个独立契约。

### 5.1 Plan 契约（首次）

```json
{
  "status": "planned",
  "plan": ["步骤1", "步骤2"]
}
```

规则：

1. plan 只负责产出计划，不直接产出最终用户答复。
2. 若任务接近完成，thought 可给出 `status=done` 作为“子任务结束信号”，最终是否收口由 replan 统一决定。

### 5.2 Thought 契约（循环）

```json
{
  "status": "continue|ask_user|done",
  "current_step": "string",
  "next_action": {
    "tool": "todo|schedule|internet_search",
    "input": "string"
  } | null,
  "question": "string|null",
  "response": "string|null"
}
```

字段规则矩阵：

1. `status=continue`
   - 必填：`current_step`、`next_action.tool`、`next_action.input`
   - 必须为空：`question`、`response`
2. `status=ask_user`
   - 必填：`current_step`、`question`
   - 必须为空：`next_action`、`response`
3. `status=done`
   - 必须为空：`next_action`、`question`
   - `response` 可选（用于子任务结论，传给 replan 上下文）

最终答复规则（统一收口）：

- thought 的 `status=done` 仅表示“子任务完成”；最终用户答复由 replan 决定（`status=done` 收口或 `status=replanned` 继续）。
- 当计划项游标走到末尾时，不直接返回；需先进入 replan 决策是否收口。

### 5.3 Replan 契约（每个子任务完成后）

```json
{
  "status": "replanned|done",
  "plan": ["更新后的步骤1", "更新后的步骤2"],
  "response": "string|null"
}
```

补充规则：

1. 仅在 `needs_replan=true` 时允许调用 replan。
2. replan 成功后必须原子更新：
   - `status=replanned` 时：替换 `plan_items`，并根据剩余计划对齐 `current_plan_index`
   - `status=done` 时：输出最终 `response` 并结束外层循环
   - `needs_replan=false`
3. `status=replanned` 时 plan 不可为空；`status=done` 时 `response` 必填。

---

## 6. 执行流程伪代码（目标行为）

```text
if slash command:
    run existing deterministic command path
else:
    if no active task:
        task = new task
        plan_once(task)

    while True:  # 外层：计划推进
        if step_count >= max_steps:
            return step_limit_fallback

        if task.awaiting_clarification:
            suspend and wait user input

        if task.needs_replan:
            replan(task)
            step_count += 1

        while True:  # 内层：当前子任务 reAct
            thought = think_for_current_step(task)
            step_count += 1

            if thought.ask_user:
                task.awaiting_clarification = True
                return "请确认：..."

            if thought.done:
                task.current_plan_index += 1
                if thought.response:
                    task.pending_final_response = thought.response
                task.needs_replan = True
                break  # 子任务完成，回外层做 replan

            action_result = execute_tool(thought.next_action)
            step_count += 1
            append_observation(action_result)
            update_step_metrics()
```

---

## 7. 日志与交互输出改造建议

当前进度日志保留，并新增 thought 语义，建议最小增强：

1. `规划完成：共 N 步`（仅首次）
2. `当前计划项：i/N - <step>`
3. `思考决策：执行动作/步骤完成/请求澄清/任务完成`
4. `步骤动作：tool -> input`
5. `步骤结果：成功|失败`
6. `重规划完成：共 M 步`（每个子任务内层完成后）

注意：仍不输出模型原始推理链，不引入公开 CoT。

---

## 8. 与现有实现的主要差异点（用于后续编码定位）

1. 当前 `_run_plan_replan` 每轮先调 planner，再执行一个动作；改造后需要拆为 `plan_once + thought_loop + conditional_replan`。
2. 当前 `_request_planner_payload` 单入口承载“计划+动作”；改造后应拆为三个请求函数：
   - `_request_plan_payload`
   - `_request_thought_payload`
   - `_request_replan_payload`
3. 当前 `_normalize_planner_decision` 只有 continue/done；需扩展或拆分为三种 normalize。
4. 当前 tests 大量依赖 `_planner_continue/_planner_done`；需要迁移到 plan/thought/replan 三类 fake payload。
5. 当前 slash 与 pending 并存逻辑需要保留：澄清等待态执行 slash 后，不应清空 pending 任务。

---

## 9. 风险与应对

### 9.1 风险：子任务级 replan 频率提升导致模型调用开销增加

- 影响：每个子任务完成后都重规划，token 与时延上升。
- 应对：保持 replan 输出结构轻量；必要时增加“低变化场景跳过重规划”策略阈值。

### 9.2 风险：状态机复杂度上升

- 影响：pending 状态、澄清状态和计划游标可能错乱。
- 应对：增加状态字段与不变量检查（如 `current_plan_index <= len(plan_items)`）。

### 9.3 风险：测试基线大改

- 影响：现有 plan-replan 测试会出现系统性失败。
- 应对：先引入“兼容层测试辅助函数”，分阶段迁移测试，再删除旧 helper。

---

## 10. 测试计划（代码阶段执行）

### 10.1 新增测试

1. 首次输入只触发一次 plan（后续不重复调 plan）。
2. 无澄清场景下，完成一个子任务循环后会触发 replan。
3. ask_user 后输入澄清，恢复执行时同样触发 replan。
4. thought 可按顺序推进多个计划项直至完成。
5. 工具失败后会记录 observation，并继续留在子任务内层循环；子任务完成后再触发 replan。
6. `thought` 与 `replan` 会计入 `step_count`，并参与上限判定。
7. 超步数上限时仍返回“已完成部分 + 未完成原因 + 下一步建议”。
8. 空计划（`plan=[]`）场景会触发 thought 显式输出 `done/ask_user`，不会直接崩溃或卡死。
9. thought/replan 的 JSON 解析失败或字段校验失败同样计步。
10. 步数上限在每轮循环开头检查；命中后不再执行 thought/replan/tool。

### 10.2 回归测试

1. `/todo`、`/schedule`、`/view` slash 路径行为不变。
2. 进度输出仍包含计划列表、步骤动作、步骤结果、完成情况。
3. 未配置 LLM 的错误提示不变。

---

## 11. 分阶段落地建议（小步可验证）

1. **阶段 A：状态模型重构**
   - 引入 plan/thought/replan 状态字段，保持旧行为不变。
2. **阶段 B：plan_once 接入**
   - 首次只调 plan；动作仍走旧接口，验证不回归。
3. **阶段 C：thought_loop 接入**
   - 以 thought 决策替代“每步 planner 决策”。
4. **阶段 D：子任务后 replan 接入**
   - 将 replan 触发条件收敛为“每个子任务内层完成后 + 澄清恢复后”。
5. **阶段 E：清理旧接口与文档/测试同步**
   - 删除旧 planner 单契约逻辑，统一三契约。

---

## 12. 本文档结论

在“plan 仅首次、每个子任务完成后 replan 跟进进度”的约束下，推荐将当前单一 planner 拆为三职责模块，并以 thought 作为执行编排核心。该方案可满足需求，同时保留 slash 命令直通与现有工具契约，后续编码应采用分阶段迁移以降低回归风险。
