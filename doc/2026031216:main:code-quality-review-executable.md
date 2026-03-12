# 可执行 CR 清单（忽略设计选择变更）

> 时间：2026-03-12 16:00（Asia/Shanghai）
> 分支：main
> 说明：仅包含明确缺陷修复与风险收敛，不包含架构偏好重构。

## 忽略项（本轮不做）

1. `.env` 覆盖系统环境变量策略调整。
2. `db.py` 拆分、dispatch table 全量重写、`Any -> Protocol` 全域替换。
3. 风格类改造（长函数拆分、Enum 化、魔法数字抽取、import 排序）。
4. 无压测证据的性能优化（连接池/客户端复用）。

## PR-1（P0：正确性）

### 1) 任务状态并发竞态

- [ ] 目标：统一 `_pending_plan_task` 与 `_latest_plan_step_trace_by_source` 的加锁读写。
- [ ] 代码：`assistant_app/agent.py`
- [ ] 实施：
  - 新增统一状态锁。
  - 读写状态字段全部通过锁访问。
  - 锁内仅做状态更新，避免调用外部逻辑。
- [ ] 测试：`tests/test_agent.py` 增补中断与 trace 并发场景。
- [ ] 验收：多轮测试无随机失败，无行为回归。

### 2) 生产路径 assert 去除

- [ ] 目标：业务分支不依赖 `assert`。
- [ ] 代码：
  - `assistant_app/agent_components/tools/schedule.py`
  - `assistant_app/agent_components/planner_session.py`
- [ ] 实施：替换为显式校验/分支，保留现有错误语义。
- [ ] 测试：`tests/test_agent.py`
- [ ] 验收：`python -O` 下行为一致。

### 3) Optional/null 契约一致化

- [ ] 目标：区分“字段缺省”与“显式 null”。
- [ ] 代码：`assistant_app/schemas/tool_args.py`（及必要联动）
- [ ] 实施：
  - 缺省：走默认/不变逻辑。
  - 显式 null：按规则拒绝并返回明确错误。
  - 依赖 `model_fields_set` 做判定。
- [ ] 测试：
  - `tests/test_schemas_commands.py`
  - `tests/test_agent.py`
- [ ] 验收：CLI 与 JSON payload 语义一致。

## PR-2（P1：数据一致性与安全）

### 4) Feishu bootstrap 改为增量对齐

- [ ] 目标：消除“先删后建”中断风险窗口。
- [ ] 代码：`assistant_app/feishu_calendar_sync_service.py`
- [ ] 实施：
  - 启动同步改为按 identity 对齐（新增/更新/清理）。
  - 单条失败不影响全局流程。
- [ ] 测试：`tests/test_feishu_calendar_sync_service.py`
- [ ] 验收：异常中断不出现批量丢失。

### 5) LIKE 特殊字符转义

- [ ] 目标：`%`/`_`/`\` 按字面匹配。
- [ ] 代码：`assistant_app/db.py`
- [ ] 实施：增加 LIKE 转义并使用 `ESCAPE '\\'`。
- [ ] 测试：`tests/test_db.py` 增加特殊字符检索用例。
- [ ] 验收：结果不因通配符扩大。

### 6) Feishu 日志脱敏

- [ ] 目标：日志中不输出明文 open_id 与完整用户文本。
- [ ] 代码：`assistant_app/feishu_adapter.py`
- [ ] 实施：
  - open_id 打码显示。
  - text 只输出截断预览。
- [ ] 测试：`tests/test_feishu_adapter.py` 增补日志断言。
- [ ] 验收：保留排障信息且无敏感明文。

## PR-3（P2：稳定性补强）

### 7) Planner callback 并发安全

- [ ] 目标：`set_*_callback` 与 emit 并发时不出现竞态。
- [ ] 代码：`assistant_app/agent_components/planner_session.py`
- [ ] 实施：对 callback 读写加锁，调用在锁外执行。
- [ ] 测试：`tests/test_agent.py`（必要时新增并发用例）。
- [ ] 验收：无死锁、无异常、无明显丢消息。

### 8) legacy OpenAI SDK 互斥保护（可选）

- [ ] 目标：legacy 分支中的全局 `openai.api_key/api_base` 写入不互串。
- [ ] 代码：`assistant_app/llm.py`
- [ ] 实施：legacy 路径增加模块级锁。
- [ ] 测试：`tests/test_llm.py`
- [ ] 验收：并发调用配置不串线。

## 统一门禁

- [ ] 先跑定向测试，再跑全量测试：
  - `python -m unittest discover -s tests -p "test_*.py"`
- [ ] 若门禁失败，必须先修复失败项再进入下一 PR 批次。
