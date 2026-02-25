# Feishu 长连接接入方案冻结文档

## 0. 冻结范围与上下文

- 冻结时间：2026-02-25 10:19（本地时间）
- 分支：`feat/im`
- 冻结目标：将“应用机器人 + 长连接订阅”MVP 接入方案固化为可执行版本，并在编码前做一次深度清晰度检查。

## 1. 已确认决策（冻结）

1. 机器人形态：使用飞书 **应用机器人**（非群自定义机器人）。
2. 订阅方式：改用 **长连接订阅模式**（WebSocket，非回调 URL）。
3. 范围：先做单用户/单聊 MVP（不处理群消息）。
4. 数据策略：先做共享数据，不做用户隔离。
5. 消息能力：先做文本消息收发，不做卡片交互。
6. 去重策略：MVP 使用内存去重（基于 `message_id`）。
7. 慢响应策略：先不做“处理中 + 异步补发”。
8. 发送失败策略：失败后最多重试 3 次。
9. 超长输出策略：采用分片输出。
10. 运行形态：与 CLI 同进程运行。
11. 日志策略：日志本地保留 7 天。
12. 密钥管理：先不接入密钥管理，采用环境变量的简化方案。

## 2. 目标与非目标

### 2.1 目标（MVP）

- 在飞书单聊中，机器人可接收用户消息并调用本地 Assistant 核心能力回复。
- 复用当前 `assistant_app.agent` 处理链路，尽量不改动核心业务语义。
- 保持本地优先与可演进：后续可扩展用户隔离、权限细化、卡片消息、多通道。

### 2.2 非目标（当前不做）

- 多租户隔离、复杂 ACL、审计后台。
- 卡片交互、文件/图片/语音多模态。
- 高可用多活架构与自动扩缩容。

## 3. 总体架构（冻结）

```text
Feishu WS Event (im.message.receive_v1)
  -> Feishu Adapter (event parse / dedupe / guard)
  -> AssistantAgent.handle_input(...)
  -> Reply Builder (text)
  -> Feishu Send Message API
```

模块落地建议：

- 新增：`assistant_app/feishu_adapter.py`
  - 长连接客户端初始化与生命周期管理
  - 事件订阅与分发
  - 事件去重、防回环、输入提取
  - 回复发送
- 最小改动：`main.py`
  - 增加启动入口（例如 `--channel feishu`）
- 配置新增：`.env` / `.env.example`
  - `FEISHU_APP_ID`
  - `FEISHU_APP_SECRET`
  - `FEISHU_ENABLED`
  - `FEISHU_ALLOWED_OPEN_IDS`（可选）

## 4. 执行步骤（冻结）

1. 建立 Feishu 长连接客户端并监听 `im.message.receive_v1`。
2. 解析 `message.content`（先支持 `text`），抽取用户输入。
3. 事件保护：
   - 忽略机器人自身消息；
   - 按 `message_id` 去重；
   - 可选按用户白名单放行。
4. 调用现有 `AssistantAgent` 获取回复文本。
5. 使用发送消息接口按 `chat_id` 回复纯文本。
6. 增加最小单测（事件解析/去重/回复构造）与联调脚本。

## 5. 与现有系统的接口约束（冻结）

- 当前 `AssistantAgent` 以“单轮字符串输入 -> 单轮字符串输出”为核心契约。
- 先保持该契约不变，不引入新会话上下文模型。
- Feishu 侧的会话信息（chat_id/user_open_id/message_id）先在适配层处理；
  仅在需要权限/路由增强时再扩展到 Agent 层。

## 6. 深度清晰度检查（重点）

以下为编码前关键口径核对清单（当前均已确认）：

1. 权限最小化口径（已确认）
   - 结论：仅接收单聊消息，不接收任何群消息。
   - 备注：群相关权限暂不申请，降低权限面和审核复杂度。

2. “谁可以触发机器人”规则（已确认）
   - 结论：仅单聊触发；群内触发场景不存在。
   - 风险提示：若未来开放群消息，需要单独补充权限边界策略。

3. 去重存储层级（已确认）
   - 结论：`message_id` 使用内存去重（TTL）。
   - 风险：进程重启后去重状态丢失，可能出现短时重复回复（MVP 接受）。

4. 处理超时与降级策略（已确认）
   - 事实：长连接模式事件处理建议 3 秒内完成且不抛异常。
   - 结论：先不处理慢响应，不引入“处理中 + 异步补发”机制。
   - 风险：极端慢响应时可能触发重推，需依赖 `message_id` 去重兜底。

5. 发送失败重试策略（已确认）
   - 结论：发送失败最多重试 3 次（含首次失败后的重试）。
   - 风险提示：需结合限频做退避，避免短时间内重复触发限流。

6. 群范围策略（已收敛）
   - 结论：当前 MVP 不接群消息，因此不需要群范围配置。
   - 备注：若未来开放群消息，再引入 `allowed_chat_id` 等群边界控制。

7. 输出长度与格式策略（已确认）
   - 结论：超长回复采用分片发送。
   - 风险提示：需保证分片顺序与片段边界可读性。

8. 运行形态（已确认）
   - 结论：与 CLI 同进程双模式运行。
   - 风险提示：生命周期耦合与日志混杂风险保留，后续按需要拆分。

9. 可观测性口径（已确认）
   - 结论：日志保留周期为 7 天。
   - 落地建议：最小日志字段包含 `event_id`、`message_id`、`open_id`、`latency_ms`、`error_code`。

10. 安全与配置管理（已确认）
    - 结论：MVP 先不接入密钥管理，凭证通过环境变量配置。
    - 风险提示：需控制 `.env` 文件权限并避免日志打印敏感信息。

## 7. 建议的默认口径（可直接落地）

1. 权限：仅申请单聊消息接收和单聊回复所需权限；不申请群消息权限。
2. 触发范围：仅单聊会话生效，不处理群会话消息。
3. 去重：MVP 使用内存 TTL 去重（如 10 分钟），暂不落库。
4. 时延：先不做慢响应降级机制，保持同步处理链路。
5. 失败重试：发送失败最多重试 3 次，并使用基础退避避免触发限频。
6. 输出策略：超长答复按片段顺序发送。
7. 运行：与 CLI 同进程运行，先保证接入简单可用。
8. 日志策略：本地日志保留 7 天。
9. 密钥管理：MVP 阶段采用环境变量，不接入外部密钥管理系统。

## 8. 参考依据（官方文档）

- 事件订阅总览：
  https://open.feishu.cn/document/server-docs/event-subscription-guide/overview
- 使用长连接接收事件：
  https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case
- 接收消息事件：
  https://open.feishu.cn/document/server-docs/im-v1/message/events/receive
- 发送消息 API：
  https://open.feishu.cn/document/server-docs/im-v1/message/create

---

## a. Commit message

`docs: freeze feishu long-connection integration plan and ambiguity checklist`

## b. Modified Files and Summary of Changes

1. `doc/archive/2026022510:im:feishu-long-connection-plan.md`
   - 新增 Feishu 长连接接入冻结文档。
   - 固化已确认决策、MVP 架构、执行步骤与接口约束。
   - 增加“深度清晰度检查”与“默认落地口径”。

## c. Reasons and Purposes of Each Modification

1. 先冻结方案，避免编码过程中口径漂移。
2. 在实现前暴露关键歧义点，降低返工和权限申请失败风险。
3. 给后续编码提供明确“先做什么、先不做什么”的边界。

## d. Potential Issues in Current Code

1. 当前仓库尚无 Feishu 通道适配层，直接编码易出现接口耦合与职责混杂。
2. 现有 Agent 面向 CLI 设计，缺少 channel 级上下文隔离策略。
3. 若不先明确权限/去重/时延策略，MVP 联调期间可能出现重复回复和权限超配。

## e. Unit Test Report

- 本次仅新增方案文档，未执行单元测试（无代码逻辑变更）。
