# Pydantic Migration Freeze Plan

> 本文档用于冻结当前项目下一阶段的 Pydantic 化改造计划，作为后续开发与验收基线。当前仅冻结计划，不自动进入开发。

## 1. Context

- Request summary:
  - 用户要求先将“项目整体是否符合 Pydantic”的评估结论，整理成一份更详细、可执行、可冻结的开发计划文档，并存入 `doc/`。
- Business/usage background:
  - 当前项目是本地优先 CLI personal assistant，核心路径包括 CLI、planner/thought/replan、SQLite、本地提醒、Feishu 适配、Feishu Calendar 同步、互联网搜索。
  - 当前分支为 `feat/refactor`，最近提交集中在将 planner、tool schema、Feishu 边界、db/search 校验迁移到 Pydantic。
  - 当前基线验证结果：`python -m unittest discover -s tests -p "test_*.py"` 共 349 个测试通过。
- Current assessment summary:
  - 项目已经显著采用 Pydantic：配置、领域模型、planner/tool schema、Feishu 部分边界、search 响应已经纳入 Pydantic。
  - 但仍不是完整的 Pydantic-first 架构：planner LLM 输出、Feishu 入站 envelope、Feishu Calendar SDK 响应、DB 写入边界、部分 orchestration payload 仍依赖手工 `dict[str, Any]` 归一化。
- Related files/modules:
  - `assistant_app/config.py`
  - `assistant_app/schemas/base.py`
  - `assistant_app/schemas/domain.py`
  - `assistant_app/schemas/planner.py`
  - `assistant_app/schemas/tools.py`
  - `assistant_app/schemas/search.py`
  - `assistant_app/agent.py`
  - `assistant_app/planner_plan_replan.py`
  - `assistant_app/planner_thought.py`
  - `assistant_app/feishu_adapter.py`
  - `assistant_app/feishu_calendar_client.py`
  - `assistant_app/db.py`
  - `assistant_app/llm.py`
  - `tests/test_schemas_*.py`
  - `tests/test_agent*.py`
  - `tests/test_feishu_*.py`
  - `tests/test_db.py`

## 2. Goals and Non-Goals

- Goals:
  - 将关键输入边界改为 Pydantic-first，而不是“先手工清洗、后模型校验”。
  - 保持当前 CLI、planner、Feishu、schedule、history、thoughts、search 的用户可见行为不变。
  - 收敛高风险 `dict[str, Any]` / `Any` 透传路径，提升类型约束与失败路径可预测性。
  - 为后续继续 Pydantic 化保留清晰阶段边界，避免一次性大改导致回归范围过大。
  - 通过单元测试和日志验证证明边界解析已迁移且行为稳定。
- Non-goals (explicitly out of scope):
  - 不在本轮冻结范围内重写整个 agent/planner 架构。
  - 不更换 OpenAI SDK、Feishu SDK、Playwright、requests 等现有主依赖。
  - 不修改数据库表结构，只允许新增输入校验模型与调用路径重构。
  - 不追求彻底消除全部 `Any`；第三方 SDK 适配层允许保留少量必要 `Any`。
  - 不改变任何现有 CLI 命令文案、参数语义、默认值或返回文本契约，除非为了维持现状而做兼容性封装。

## 3. Functional Contract

- User-visible behavior:
  - 用户通过 CLI、Feishu DM、定时器触发、主动提醒触发的现有行为保持不变。
  - `/schedule`、`/history`、`/thoughts`、自然语言 task loop、Feishu DM、主动提醒分数逻辑不新增功能、不减少功能。
  - 失败时仍保留当前“失败关闭”原则：非法 payload 不应 silently mutate into a valid action。
- CLI/API/Tool contract:
  - planner plan 输出继续使用 `planned`，字段为 `status/goal/plan`。
  - planner replan 输出继续使用 `replanned|done`。
  - thought 阶段继续支持 tool-calling 为主、legacy JSON/content fallback 为辅。
  - tool 参数 JSON schema 继续由 `assistant_app/schemas/tools.py` 派生，不新增独立手写 schema 源。
  - DB 对外方法的签名和返回契约保持兼容：
    - `bool` 返回的方法仍返回 `bool`
    - `None`/对象返回的方法仍保持原契约
- Input validation and error handling:
  - 原始外部 payload 必须优先进入 Pydantic schema/adapter，非法输入直接 fail closed。
  - 不允许先通过 `str(...)`、默认空字符串、默认空数组等方式弱化 `strict=True` 的约束后再校验，除兼容 legacy fallback 所必需的最小转换外。
  - legacy 兼容路径必须被显式标注，并有单测证明其仍可工作。
  - tool 参数中的额外字段、错误类型、空字符串、非法 URL、非法时间格式必须被拒绝。
- Data persistence impact:
  - 不新增表、不改表结构。
  - 允许新增 Pydantic 写入模型，例如 recurrence/update/create 的输入模型。
  - 读取模型继续使用 `assistant_app/schemas/domain.py`，写入入口补足 schema 校验。

## 4. Technical Plan

- Design overview:
  - 所有“外部原始输入”优先走 Pydantic schema/adapter。
  - 所有“内部业务上下文”尽量走明确模型，而不是在模块间透传裸 `dict[str, Any]`。
  - 对第三方 SDK 返回的复杂对象，优先使用“适配器模型 + 小范围转换函数”，避免侵入式重写 SDK 调用层。
  - JSON schema 仍统一由 Pydantic 模型导出，禁止新增平行维护的手写 schema。

- Key implementation steps:
  - Phase A: Planner / Thought / Replan 边界收敛
    - 目标：
      - 让 LLM 输出解析改为“JSON -> schema”，减少手工 `str/strip/lower/default` 预处理。
    - 具体动作：
      - 在 `assistant_app/schemas/planner.py` 增补 plan/replan/thought 的 raw payload adapter 或 envelope model。
      - 重写 `assistant_app/planner_plan_replan.py` 中 `normalize_plan_decision()` 与 `normalize_replan_decision()`。
      - 重写 `assistant_app/planner_thought.py` 中 `normalize_thought_decision()`，将 `status/current_step/question/response/next_action` 解析尽可能交给 schema validator。
      - 保留 tool-calling 与 legacy JSON content fallback，但两条路径都必须汇合到统一 schema。
    - 完成标志：
      - 正常与失败路径均通过模型驱动。
      - 核心 normalizer 中不再大面积手工拼接规范化 payload。

  - Phase B: Assistant message / tool-call 归一化统一
    - 目标：
      - 减少 `assistant_app/llm.py`、`assistant_app/agent.py`、`assistant_app/proactive_react.py` 中重复的 message/tool_call 归一化逻辑。
    - 具体动作：
      - 统一 `normalize_assistant_tool_message()` / `normalize_tool_call_payload()` 的调用方式。
      - 为 reply_with_tools 返回结果定义更明确的 envelope model，避免调用方继续拿 `dict[str, Any]` 拼字段。
      - 明确 thought/proactive 两类调用的共同边界和差异边界。
    - 完成标志：
      - `reply_with_tools` 的业务调用方以 typed payload 为主，不再频繁 `get("assistant_message")`、`get("tool_calls")`。

  - Phase C: Feishu 入站与 Calendar 响应适配
    - 目标：
      - 将 Feishu 入站事件和 Calendar SDK 响应从“路径提取式解析”升级为“schema/adapter 驱动”。
    - 具体动作：
      - 在 `assistant_app/schemas/feishu.py` 增补入站 event envelope models，覆盖 `event.message.*`、`sender.*`、`message.*` 的兼容结构。
      - 将 `assistant_app/feishu_adapter.py` 中 `extract_text_message()` 收敛到 schema adapter。
      - 为 `assistant_app/feishu_calendar_client.py` 定义 raw event adapter model，统一 `event_id/summary/description/start/end/timezone/create_time` 解析。
      - 尽量缩小 `_read_path()`、`_parse_int()`、`_first_non_empty()` 的职责范围，必要时仅保留在 adapter 层。
    - 完成标志：
      - Feishu message 和 Calendar event 的核心字段不再散落在业务代码手工抽取。
      - 缺字段、空字符串、错误时间戳、异常结构都有单测。

  - Phase D: DB 写入边界 Pydantic 化
    - 目标：
      - 补齐 DB public write methods 的输入模型，统一 schedule/thought/recurrence 写入校验。
    - 具体动作：
      - 在 `assistant_app/schemas/storage.py` 增加 recurrence/create/update 相关输入模型。
      - 将 `assistant_app/db.py` 中 `set_schedule_recurrence()`、`update_schedule()`、必要的 add/update 入口由手工 `isinstance` 和范围判断迁移至 schema 校验。
      - 保持 DB 方法签名与返回行为兼容，必要时在方法内部先构造 schema，再沿用原 SQL 执行流程。
    - 完成标志：
      - DB 层核心写入路径不再依赖重复的手工整型/布尔型判定。
      - `bool` 返回值语义不变。

  - Phase E: 内部上下文 payload typed 化
    - 目标：
      - 收敛 planner/thought 组装上下文中的裸字典结构。
    - 具体动作：
      - 为 `_build_planner_context()`、`_build_thought_context()` 及序列化结果建立 context payload models。
      - 将 `assistant_app/agent.py` 内部 `dict[str, Any]` 传递路径收敛到 serializer 边界。
    - 完成标志：
      - planner/thought context 的构建与消费拥有显式模型。
    - 备注：
      - 本阶段优先级低于 Phase A-D；如前四阶段已充分提升一致性，可将本阶段拆为后续独立冻结。

- Execution order:
  - 推荐严格顺序：Phase A -> Phase C -> Phase D -> Phase B -> Phase E
  - 原因：
    - A 直接影响最关键的 planner LLM 行为边界，回报最高。
    - C 解决第三方入站高不确定 payload。
    - D 统一写入边界，能减少后续隐式类型分支。
    - B/E 更偏内部清理，可在核心边界稳定后推进。

- Backward compatibility strategy:
  - CLI 文案、返回文本、工具名、JSON schema 外形、DB 返回类型保持不变。
  - 对 thought legacy fallback 保留兼容，但只作为非主路径。
  - Feishu 与 Calendar 兼容现有多种字段位置，不要求上游 payload 同步升级。
  - 对旧测试保持尽量少改；若行为不变仅实现路径变化，应优先补充而非重写测试。

## 5. Test Plan

- Unit tests to add/update:
  - Planner / Thought / Replan
    - 新增 malformed payload、extra fields、wrong types、empty strings、multi-tool-call、legacy JSON fallback 测试。
    - 验证统一 normalizer 在 strict 模式下仍保留现有兼容行为。
  - Feishu adapter
    - 增加 event envelope 缺失 `message_id/chat_id/content/open_id`、错误 `message_type/chat_type/sender_type`、空文本、非对象 JSON content 的测试。
  - Feishu calendar client
    - 增加错误时间戳、毫秒级 `create_time`、缺 `event_id`、缺 start/end range、timezone fallback 测试。
  - DB
    - 增加 recurrence/update/create 写入模型的错误类型测试，重点覆盖 `bool` 被误当成 `int`、空字符串、负值、`times=1` 等边界。
  - LLM wrapper / proactive
    - 验证 typed envelope 不破坏 `reply_with_tools()` 的 thought/proactive 路径。

- Integration/manual checks:
  - 运行全量单测：
    - `python -m unittest discover -s tests -p "test_*.py"`
  - 手动最小回归：
    - 启动 CLI，执行 `/help`、`/schedule add ...`、自然语言触发一个需要 planner 的任务。
    - 如本地有 Feishu/Calendar 配置，可进行最小 event parse 或 sync smoke test。

- Edge and failure scenarios:
  - LLM 返回空对象、数组、非 JSON 字符串、JSON 结构缺字段、字段类型错误。
  - tool_call `arguments` 为非字符串、非 JSON、空字符串、带额外字段。
  - Feishu payload 深层字段缺失或路径变化。
  - Calendar SDK 返回 `timestamp` / `time_stamp` 混用。
  - DB 写入收到 `bool`、`float`、空字符串、`None`、非法时间格式。

## 6. Log Verification Plan

- Log file/logger to use:
  - 通用日志：`APP_LOG_PATH`，logger `assistant_app.app`
  - LLM trace：`LLM_TRACE_LOG_PATH`，logger `assistant_app.llm_trace`

- Event names and key fields to validate:
  - 复用现有事件：
    - `llm_request`
    - `llm_response`
    - `llm_response_error`
    - `proactive_react_invalid_action`
    - `feishu_calendar_client_warning`
  - 计划新增低噪声验证事件：
    - `planner_payload_validation_failed`
      - fields: `phase`, `reason`, `payload_type`
    - `thought_tool_arguments_validation_failed`
      - fields: `tool_name`, `reason`
    - `feishu_event_payload_invalid`
      - fields: `reason`, `message_type`, `chat_type`
    - `feishu_calendar_event_schema_invalid`
      - fields: `reason`, `has_event_id`, `has_start`, `has_end`
    - `db_input_validation_failed`
      - fields: `method`, `reason`

- Trigger actions (which command/request should produce logs):
  - 通过测试或手工注入非法 planner/thought payload，触发 validation failed 日志。
  - 通过伪造非法 Feishu event / Calendar event，触发 Feishu 相关 invalid 日志。
  - 通过调用 DB 写入接口传入非法 recurrence/update 参数，触发 DB validation failed 日志。
  - 正常任务执行仍应继续产出 `llm_request` / `llm_response`。

- Expected log values and pass/fail rules:
  - 对非法输入：
    - 必须能看到对应 `*_validation_failed` 或 warning event。
    - 日志字段必须能定位失败阶段和失败原因。
    - 不得出现 silent success。
  - 对正常输入：
    - 不应新增异常 warning/error 噪声。
    - planner/thought 正常日志链条完整。

## 7. Risks and Mitigations

- Risk 1:
  - planner/thought 改为 Pydantic-first 后，可能误伤当前依赖手工 `str()` 归一化的兼容样例。
- Mitigation:
  - 先补覆盖现有兼容样例的测试，再迁移 normalizer；必要时把兼容转换显式下沉到 `field_validator(mode="before")`。

- Risk 2:
  - Feishu/Calendar 上游 payload 实际形态复杂，过度收紧 schema 会造成误拒绝。
- Mitigation:
  - 采用 adapter model，而不是一次性把所有上游字段强行定义成严格业务模型；对 optional/alias/兼容路径逐步收敛。

- Risk 3:
  - DB 写入层输入模型引入后，可能改变某些旧调用的隐式容错行为。
- Mitigation:
  - 明确保留旧返回语义；对 caller 依赖的失败返回值建立回归测试。

- Risk 4:
  - 一次性改造范围过大，review 和回归成本失控。
- Mitigation:
  - 按 Phase A/C/D/B/E 分阶段提交，每阶段保持 tests green，可单独回滚或暂停。

## 8. Acceptance Criteria

- Execution status snapshot (`2026-03-09 13:48 +0800 CST`):
  - 已完成并通过本地验证：Phase A、Phase C、Phase D
  - 尚未开始：Phase B、Phase E
  - 最新验证：
    - `ruff check assistant_app/schemas/storage.py assistant_app/db.py tests/test_db.py tests/test_schemas_storage.py`
    - `python -m unittest discover -s tests -p "test_*.py"` -> `365 tests OK`

- [x] Phase A 完成后，plan/replan/thought 核心 normalizer 以 schema 驱动解析为主，不再大量手工预清洗关键字段。
- [x] Phase C 完成后，Feishu 入站消息与 Calendar event 的关键解析路径由明确 schema/adapter 承接。
- [x] Phase D 完成后，DB 核心写入接口具备明确输入模型，且返回契约保持兼容。
- [x] 新增负向测试覆盖非法 payload、extra fields、错误类型、缺字段和 legacy fallback。
- [x] 全量单测通过，且不少于当前基线的 349 个测试。
- [ ] 日志验证证据证明边界校验失败能被明确观测，正常路径未引入额外噪声。
- [ ] 用户可见 CLI / Feishu / schedule 行为没有契约层变化。
- [ ] Log evidence proves the change is effective and correct.

## 9. Open Questions

- None.

## 10. Decision Log

- Decision:
  - 以“关键边界 Pydantic-first”作为本轮主目标，而不是追求全项目零 `Any`。
- Reason:
  - 这是最高收益、最低架构风险的切入点，能显著提升稳定性而不要求重写系统。
- Impact:
  - 改造将优先集中在 planner/Feishu/DB 边界，而不是 UI 或非关键内部实现。

- Decision:
  - Feishu 与 Calendar 采用 adapter model 方案，而不是强行把 SDK 响应直接当业务模型使用。
- Reason:
  - 第三方 SDK 结构不稳定，adapter 更适合兼容多种字段形态。
- Impact:
  - 允许保留极少量解析辅助函数，但其职责必须收缩到 adapter 层。

- Decision:
  - DB 层保持现有 public method 签名和返回契约，内部新增输入模型。
- Reason:
  - 避免连带修改大量调用方，降低回归面。
- Impact:
  - 迁移重点在方法内部边界，不做对外 API 破坏式调整。

- Decision:
  - Phase E 内部上下文 typed 化优先级低于 planner/Feishu/DB 边界收敛。
- Reason:
  - 前三类边界直接处理外部不可信输入，风险更高。
- Impact:
  - 如实施节奏需要压缩，可将 Phase E 延后为下一版冻结。

## 11. Change Control After Freeze

- Freeze version:
  - `pydantic-freeze-plan-v1`
- Freeze timestamp:
  - `2026-03-09 13:21:44 +0800 CST`
- Owner/approver:
  - Codex 起草；用户于 2026-03-09 确认后作为实施基线。
- Pre-freeze review confirmation (user said "no issues"):
  - Confirmed. 用户明确回复“无误，继续”。
- Change process:
  1. Propose change
  2. Update spec + impact
  3. User approve new freeze
  4. Implement against latest freeze
