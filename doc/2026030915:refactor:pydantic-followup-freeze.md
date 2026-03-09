# Pydantic Follow-up Freeze

## 1. Context

- Request summary:
  - 用户要求先将“项目里剩余可用 Pydantic 提升工程化的工作”冻结成后续开发文档，避免上下文耗尽后丢失实施计划。
- Business/usage background:
  - 当前分支 `feat/refactor` 正在持续推进 typed boundary / Pydantic 迁移。
  - 本轮已经完成一项关键收敛：thought tool-calling 的已验证参数不再只以 JSON 字符串在运行时传递，而是额外携带 runtime typed payload 直达执行层。
  - 接下来需要继续减少重复校验、重复 schema 和跨层字符串协议。
- Related files/modules:
  - [assistant_app/planner_thought.py](/Users/lingdong/workspace/assistant/assistant_app/planner_thought.py)
  - [assistant_app/schemas/planner.py](/Users/lingdong/workspace/assistant/assistant_app/schemas/planner.py)
  - [assistant_app/schemas/routing.py](/Users/lingdong/workspace/assistant/assistant_app/schemas/routing.py)
  - [assistant_app/agent.py](/Users/lingdong/workspace/assistant/assistant_app/agent.py)
  - [assistant_app/agent_components/tools/planner_tool_routing.py](/Users/lingdong/workspace/assistant/assistant_app/agent_components/tools/planner_tool_routing.py)
  - [assistant_app/agent_components/tools/schedule.py](/Users/lingdong/workspace/assistant/assistant_app/agent_components/tools/schedule.py)
  - [assistant_app/agent_components/tools/history.py](/Users/lingdong/workspace/assistant/assistant_app/agent_components/tools/history.py)
  - [assistant_app/agent_components/tools/thoughts.py](/Users/lingdong/workspace/assistant/assistant_app/agent_components/tools/thoughts.py)
  - [assistant_app/agent_components/tools/internet_search.py](/Users/lingdong/workspace/assistant/assistant_app/agent_components/tools/internet_search.py)
  - [assistant_app/agent_components/command_handlers.py](/Users/lingdong/workspace/assistant/assistant_app/agent_components/command_handlers.py)
  - [assistant_app/agent_components/parsing_utils.py](/Users/lingdong/workspace/assistant/assistant_app/agent_components/parsing_utils.py)
  - [assistant_app/feishu_calendar_client.py](/Users/lingdong/workspace/assistant/assistant_app/feishu_calendar_client.py)
  - [assistant_app/feishu_adapter.py](/Users/lingdong/workspace/assistant/assistant_app/feishu_adapter.py)
  - [assistant_app/search.py](/Users/lingdong/workspace/assistant/assistant_app/search.py)
  - [assistant_app/schemas/search.py](/Users/lingdong/workspace/assistant/assistant_app/schemas/search.py)

## 2. Goals and Non-Goals

- Goals:
  - 把剩余高收益的弱类型边界继续收敛到 Pydantic schema。
  - 让同一条业务规则尽量只有一份“唯一真相源”，避免 CLI、thought tool、执行器、DB 各写一套。
  - 在不改变外部命令契约的前提下，减少内部 JSON 文本往返和手工 `dict` 校验。
  - 保持现有自然语言计划链路、CLI 命令、Feishu/搜索功能行为兼容。
- Non-goals (explicitly out of scope):
  - 不在本冻结阶段重做数据库模型或引入 ORM。
  - 不把所有内部 dataclass/轻量对象都替换成 Pydantic。
  - 不修改用户可见命令语法。
  - 不在本阶段移除 legacy command fallback，只允许缩小其在主链路中的使用范围。

## 3. Functional Contract

- User-visible behavior:
  - `/schedule`、`/history`、`/thoughts`、自然语言 task、Feishu、搜索输出文案保持兼容。
  - 迁移完成后，用户不应感知参数格式变化。
- CLI/API/Tool contract:
  - thought tool-calling 继续以现有 function schema 暴露给模型。
  - planner runtime 内部允许优先传递 typed payload；仅在无 typed payload 时回退到 JSON/legacy 路径。
  - CLI 命令层后续应补充 command model，但外部命令文本契约不变。
- Input validation and error handling:
  - 同一业务参数校验逻辑只允许保留一份主实现，其他层仅做适配。
  - 若兼容层仍需接受 JSON 文本，必须在边界立即转换为已验证模型或明确失败。
  - 错误文案尽量沿用当前实现，除非为消除歧义必须调整。
- Data persistence impact:
  - 不新增表，不做 schema migration。
  - 允许只调整入库前验证和内存中 payload 传递方式。

## 4. Technical Plan

- Design overview:
  - 以“typed boundary 继续向外扩”为主线推进。
  - 已完成部分：thought tool-call -> runtime typed payload -> 执行层。
  - 后续按收益排序继续向 CLI 命令层、外部 SDK/HTTP 响应层收敛。
- Key implementation steps:
  - Step 1:
    - 巩固刚完成的 runtime typed payload 主链路。
    - 清理执行器中 typed path 与 dict path 的重复逻辑，优先提取共用 helpers，避免 schedule 文件继续膨胀。
    - 补充针对 runtime payload 的更细粒度单测，覆盖 `schedule_update`、`thoughts_update`、`internet_search_fetch_url`。
  - Step 2:
    - 为 CLI 命令新增 command-level Pydantic models。
    - 目标覆盖 `/schedule add|update|view|repeat|list`、`/history list|search`、`/thoughts list|update|get|delete`。
    - `command_handlers.py` 与 `parsing_utils.py` 从 regex tuple 解析迁移到“文本解析 -> 结构化 model -> 执行”模式。
  - Step 3:
    - 收敛 schedule 规则定义。
    - 检查 [assistant_app/schemas/tools.py](/Users/lingdong/workspace/assistant/assistant_app/schemas/tools.py) 与 [assistant_app/schemas/storage.py](/Users/lingdong/workspace/assistant/assistant_app/schemas/storage.py) 的字段与约束是否重复。
    - 如可行，提取共享 mixin/base model 或明确“tool args model -> storage input model”的单向映射，避免再复制一套校验。
  - Step 4:
    - 为 Feishu SDK 响应补 envelope schema。
    - 目标覆盖 `code/msg/data/page_token/has_more/items` 等读路径，减少 [assistant_app/feishu_calendar_client.py](/Users/lingdong/workspace/assistant/assistant_app/feishu_calendar_client.py) 与 [assistant_app/feishu_adapter.py](/Users/lingdong/workspace/assistant/assistant_app/feishu_adapter.py) 中的 `_read_path` / `Any` 传播。
  - Step 5:
    - 细化搜索 provider schema。
    - 为 Bocha `summary`、`webPages.value` 等结构引入更明确的 item model，减少 [assistant_app/search.py](/Users/lingdong/workspace/assistant/assistant_app/search.py) 中的 `isinstance`/`dict.get` 分支。
  - Step 6:
    - 复查日志与 trace payload 是否还存在“先构造任意 dict，再由 formatter 宽松处理”的边界。
    - 仅在收益明确时，为核心日志 event 增加轻量 schema，不盲目全量建模。
- Backward compatibility strategy:
  - 所有主改动必须保留当前 JSON 文本输入兼容层。
  - thought tool schema 不改函数名，不改字段名，不改 required/optional 语义。
  - 任何执行结果文案变更都必须先更新断言测试。

## 5. Test Plan

- Unit tests to add/update:
  - 更新 `tests/test_agent.py`，覆盖 typed runtime payload 在 `schedule_update`、`schedule_repeat`、`thoughts_update`、`internet_search_fetch_url` 路径下的行为。
  - 新增 CLI command model 相关测试，覆盖合法输入、缺字段、越界、格式错误、空值语义。
  - 为 Feishu response schema 增加 `model_validate` 成功/失败测试。
  - 为 search response schema 增加 `summary` 多种形态的解析测试。
- Integration/manual checks:
  - 运行 `python -m unittest discover -s tests -p "test_*.py"`。
  - 至少手动验证一次 `/schedule add`、`/history search`、`/thoughts update` 命令路径。
  - 至少手动验证一次自然语言任务触发 schedule/history/thoughts tool-calling。
- Edge and failure scenarios:
  - `times` 缺少 `interval_minutes`。
  - `remind_start_time` 缺少 `interval_minutes`。
  - `history_search` 缺少 `keyword`。
  - `thoughts_update` 显式传非法状态。
  - Feishu 返回缺失 `page_token` 但 `has_more=true`。
  - Bocha 返回 schema 不完整或 `summary` 混合类型。

## 6. Log Verification Plan

- Log file/logger to use:
  - `assistant_app.app`
  - 必要时查看 `logs/app.log`
- Event names and key fields to validate:
  - `planner_tool_internet_search_start`
  - `planner_tool_internet_search_done`
  - `planner_tool_thoughts_start`
  - `planner_tool_thoughts_done`
  - `planner_payload_validation_failed`
  - `thought_tool_arguments_validation_failed`
  - 新增 Feishu/search schema 迁移后，如需增加日志，只允许记录边界校验失败事件，不增加常态噪声。
- Trigger actions (which command/request should produce logs):
  - 自然语言任务触发 `schedule_*` / `history_*` / `thoughts_*` tool call。
  - `/thoughts list --status 进行中` 这类失败输入。
  - 搜索抓取 URL 的自然语言任务。
- Expected log values and pass/fail rules:
  - 成功路径必须出现 start/done 成对事件。
  - 校验失败路径必须出现单次明确失败事件，`reason` 可区分 JSON 无效、schema 无效、tool 未允许等原因。
  - 不允许出现因类型迁移引入的重复失败日志风暴。

## 7. Risks and Mitigations

- Risk 1:
  - typed path 与 legacy dict path 并存，容易出现双份实现漂移。
- Mitigation:
  - 下一轮优先抽公共 helper，尽量让 typed/dict 两条入口落到同一业务实现。

- Risk 2:
  - `schedule.py` 已经较长，继续直接堆逻辑会降低可维护性。
- Mitigation:
  - 后续拆出 `schedule_runtime.py` 或局部 helper，不在单文件继续平铺分支。

- Risk 3:
  - CLI command model 迁移可能影响现有错误文案或空值语义。
- Mitigation:
  - 先补测试锁定现有文案和行为，再迁移实现。

- Risk 4:
  - Feishu SDK 响应对象既可能是 dict 也可能是带属性对象，schema 设计不当会误杀兼容场景。
- Mitigation:
  - 复用已有 `from_attributes=True` 的兼容模型模式，先覆盖最常见 envelope，再逐步细化。

## 8. Acceptance Criteria

- [x] thought tool-calling 主链路已可携带 runtime typed payload 传递到执行层。
- [ ] `schedule/history/thoughts/internet_search` 执行器的 typed/dict 重复逻辑被进一步收敛，而不是继续扩散。
- [x] CLI `/schedule`、`/history`、`/thoughts` 命令解析迁移到 command-level Pydantic model。
- [x] Feishu calendar / message SDK 关键响应已用 schema 封装，不再在主路径大量依赖 `_read_path`。
- [x] Bocha search 响应结构进一步类型化，主逻辑中的 `isinstance`/`dict.get` 明显减少。
- [x] 全量单测通过。
- [ ] Log evidence proves the change is effective and correct.

## 9. Open Questions

- None for this freeze baseline.
- 若下一轮继续推进，默认优先级为：
  - `schedule/history/thoughts/internet_search` 执行器 typed/dict 双路径去重
  - 日志 / trace payload schema 收敛
  - 运行时日志证据补齐

## 10. Decision Log

- Decision:
  - 本次冻结以“后续实施基线”形式记录，不回溯修改历史文档。
- Reason:
  - 当前上下文接近上限，需要快速保存下一轮工作的边界和顺序。
- Impact:
  - 下一轮可以直接按该文档执行，无需重新梳理大部分上下文。

- Decision:
  - 不执行 skill 中的 `doc/` 与 `logs/` 清理步骤。
- Reason:
  - 当前仓库明确把 `doc/` 作为历史快照目录，清理会破坏已有记录。
- Impact:
  - 本次冻结文档以新增快照方式保存，不影响历史文件。

- Decision:
  - 保留 legacy command fallback，不在当前冻结中要求立即删除。
- Reason:
  - 当前兼容路径仍承担旧模型/旧输出的兜底职责，直接删除风险高。
- Impact:
  - 后续实现只收缩主链路中的使用频率，不破坏兼容性。

- Decision:
  - 当前“已完成基线”定义为 runtime typed payload 已打通，但 CLI 命令层尚未类型化。
- Reason:
  - 这是当前分支最清晰的阶段边界。
- Impact:
  - 后续工作从 CLI 和外部响应边界继续向外推进，而不是重复改 planner 内部。

## 11. Change Control After Freeze

- Freeze version:
  - v1
- Freeze timestamp:
  - 2026-03-09 15:00 Asia/Shanghai
- Owner/approver:
  - Codex 起草，等待用户基于该文档继续实施
- Pre-freeze review confirmation (user said "no issues"):
  - 用户在本轮明确要求“先把后续要做的冻结成文档”；视为要求产出冻结基线，但未单独做逐条复核确认
- Change process:
  1. Propose change
  2. Update spec + impact
  3. User approve new freeze
  4. Implement against latest freeze

## Current Baseline Snapshot

- Current branch:
  - `feat/refactor`
- Current uncommitted work included in this baseline:
  - planner runtime 已支持 `RuntimePlannerActionPayload`
  - `history/thoughts/schedule/internet_search` 执行器已接入 typed payload 优先分支
  - 新增了 route/agent 相关测试覆盖
- Validation already completed before freezing:
  - `python -m unittest discover -s tests -p "test_*.py"`
  - 结果：382 tests, OK

## 12. Progress Updates

- Progress update:
  - 2026-03-09 16:50 Asia/Shanghai
- Completed since freeze:
  - CLI command-level Pydantic models 已落地主路径，`/schedule`、`/history`、`/thoughts` 命令解析统一产出 typed runtime payload，再进入执行层。
  - Feishu SDK response envelope 已补充 schema，覆盖 `code/msg`、calendar create/list 的 `data.event.event_id`、`items/has_more/page_token` 等主读路径。
  - `assistant_app/feishu_calendar_client.py` 与 `assistant_app/feishu_adapter.py` 已切到 typed response parsing，主路径不再依赖 `_read_path` 读取上述关键字段。
  - Bocha search response 已继续类型化，`summary` 多形态与 `webPages.value` item 解析下沉到 schema，`assistant_app/search.py` 中对应的 `isinstance`/`dict.get` 分支明显减少。
- Verification completed after progress update:
  - `ruff check assistant_app/feishu_adapter.py assistant_app/feishu_calendar_client.py assistant_app/schemas/__init__.py assistant_app/schemas/feishu.py assistant_app/schemas/search.py assistant_app/search.py tests/test_feishu_adapter.py tests/test_feishu_calendar_client.py tests/test_search.py`
  - `python -m unittest discover -s tests -p "test_*.py"`
  - 结果：399 tests, OK
- Remaining highest-priority work:
  - 继续收敛 `schedule/history/thoughts/internet_search` 执行器中 typed path 与 legacy dict path 的双份实现。
  - 复查日志 / trace payload 是否仍存在宽松 `dict` 边界，并补最小必要的 schema。
  - 如需把本冻结文档推进到“完成”状态，还需要补运行时日志证据，而不仅是单元测试验证。
