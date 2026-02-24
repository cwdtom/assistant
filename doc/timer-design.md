# 定时器与提醒执行技术设计（V1）

## 1. 背景与目标

当前项目已经支持在数据层保存提醒字段：

- Todo：`due_at`、`remind_at`
- Schedule：`remind_at`
- Recurring Schedule：`remind_start_time`（随重复规则）

但当前实现仅“存储+展示”，尚未实现自动提醒触发。本文目标是设计一个本地定时器（Timer Engine），用于驱动待办/日程提醒任务执行。

## 2. 设计范围

### 2.1 本期范围（In Scope）

1. 在 CLI 进程内运行一个后台定时器。
2. 定时扫描数据库，触发到点提醒。
3. 支持 Todo、单次 Schedule、重复 Schedule 的提醒触发。
4. 提供基础去重机制，避免同一提醒重复触发。
5. 提供可配置参数（轮询间隔、补偿窗口、批量上限等）。

### 2.2 非目标（Out of Scope）

1. 第三方渠道推送（短信、邮件、企业 IM）。
2. 跨设备同步提醒。
3. Snooze（稍后提醒）与用户确认闭环（ack）流程。
4. 常驻守护进程（daemon）独立部署（先做 CLI 内嵌）。

## 3. 现状约束（来自当前代码）

1. 数据库为 SQLite，本地单用户模型（`assistant_app/db.py`）。
2. CLI 主入口是 `assistant_app/cli.py`，目前无后台任务线程。
3. 已有提醒字段和重复规则字段，具备触发提醒所需核心数据。
4. 代码中已有“重复事件展开”逻辑，可复用于重复提醒计算（建议抽出为可复用函数）。

## 4. 总体架构

```text
CLI main loop
   ├─ AssistantAgent（处理命令/自然语言）
   └─ TimerEngine（后台线程）
         ├─ ReminderScanner（按时间窗口查询候选提醒）
         ├─ ReminderDeduper（去重与投递记录）
         └─ ReminderSink（提醒输出通道，V1 为 stdout）
```

### 4.1 关键模块

1. `assistant_app/timer.py`
   - `TimerEngine`：启动/停止、tick 循环、异常保护。
   - `Clock` 协议：便于测试注入假时间。

2. `assistant_app/reminder_service.py`
   - `ReminderService`：收敛提醒查询、触发判定、投递与记录。
   - `ReminderCandidate`：统一候选提醒结构。

3. `assistant_app/reminder_sink.py`
   - `ReminderSink` 协议。
   - `StdoutReminderSink`：输出到 CLI（例如 `提醒> ...`）。

4. `assistant_app/db.py` 扩展
   - 新增提醒投递记录表与 CRUD（见第 5 节）。

## 5. 数据模型设计

为保证“至少一次且可去重”，新增投递记录表：

```sql
CREATE TABLE IF NOT EXISTS reminder_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reminder_key TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,          -- todo | schedule
    source_id INTEGER NOT NULL,
    occurrence_time TEXT,               -- 对 recurring schedule 存具体发生时间
    remind_time TEXT NOT NULL,          -- 实际提醒时间
    delivered_at TEXT NOT NULL,         -- 实际投递时间
    payload TEXT                        -- 预留扩展（JSON）
);
```

### 5.1 reminder_key 规则

- Todo：`todo:{id}:{remind_at}`
- 单次 Schedule：`schedule:{id}:{event_time}:{remind_at}`
- 重复 Schedule：`schedule:{id}:{occurrence_event_time}:{occurrence_remind_time}`

通过 `UNIQUE(reminder_key)` 保证幂等：重复 tick 或重启恢复都不会重复投递同一条提醒。

## 6. 提醒计算规则

设当前时间 `now`，单次扫描窗口为：

- `scan_start = now`
- `scan_end = now + lookahead_window`

说明：V1 不做离线补发，不回溯扫描历史窗口。

时间基准约定（V1）：

1. 所有时间按**系统本地时区**解释（与现有 `YYYY-MM-DD HH:MM` 存储格式一致）。
2. 暂不引入独立时区字段，不做跨时区换算。
3. 夏令时（DST）跳变按系统本地时间行为处理，不额外补偿。

### 6.1 Todo 提醒

触发条件：

1. `remind_at IS NOT NULL`
2. `done = 0`
3. `scan_start <= remind_at <= scan_end`

### 6.2 单次 Schedule 提醒

触发条件：

1. `schedules.remind_at IS NOT NULL`
2. `scan_start <= remind_at <= scan_end`

### 6.3 重复 Schedule 提醒

当 `recurring_schedules.enabled = 1` 时：

1. 先展开 occurrence（遵循已有 `start_time + n*interval`、`times` 规则）。
2. 计算每个 occurrence 的提醒时间：
   - 优先使用 `remind_start_time + n*interval`
   - 若无 `remind_start_time`，回退到基础日程 `remind_at` 与 `event_time` 的偏移量（若可计算）
   - 若两者都不可得，则该 occurrence 不触发提醒
3. 对落在扫描窗口内的 occurrence 生成候选提醒。

回退公式（V1 固定）：

- `delta_minutes = remind_at - event_time`
- `occurrence_remind_time = occurrence_event_time + delta_minutes`
- 允许 `delta_minutes <= 0`（即“提前提醒”或“同一时刻提醒”）。

## 7. 执行流程

每个 tick：

1. 扫描候选提醒（todo/schedule/recurring）。
2. 对每个候选生成 `reminder_key`。
3. 查询是否已投递；未投递则执行 sink 投递。
4. 投递成功后写入 `reminder_deliveries`。
5. 写入失败或投递失败：记录日志，下一轮继续尝试。

并发边界（V1）：

1. V1 仅保证**单进程（单 CLI 实例）**下的提醒幂等。
2. 多进程同时运行时不保证绝对不重复投递（后续可升级为“先占位写库再投递”事务语义）。

## 8. 配置项（建议）

新增环境变量（`assistant_app/config.py` + `.env.example`）：

1. `TIMER_ENABLED`（默认 `true`）
2. `TIMER_POLL_INTERVAL_SECONDS`（默认 `15`）
3. `TIMER_LOOKAHEAD_SECONDS`（默认 `30`）
4. `TIMER_CATCHUP_SECONDS`（V1 预留字段，读取后强制视为 `0`，不启用补发）
5. `TIMER_BATCH_LIMIT`（默认 `200`）
6. `REMINDER_DELIVERY_RETENTION_DAYS`（默认 `30`，用于清理历史记录）

## 9. 线程与并发

1. Timer 使用单独后台线程，`daemon=True`。
2. `AssistantDB` 每次操作自行建立连接（当前实现符合多线程连接隔离）。
3. 对 stdout 输出加轻量锁，避免与 CLI 输入提示互相打断。
4. 进程退出时执行优雅停止（`stop_event + join(timeout)`）。

CLI 输出交互约定（V1）：

1. 提醒统一输出格式：`提醒> ...`。
2. 若提醒发生在用户输入过程中，先换行输出提醒，再重绘输入提示符 `你> `。
3. 提醒输出不覆盖已输入文本（以可读性优先）。

## 10. 可观测性与故障处理

1. 打点日志：
   - tick 开始/结束
   - 候选数、投递数、失败数
   - 每条失败原因（简短）
2. 异常策略：
   - 单条提醒失败不影响整轮
   - 整轮异常捕获后睡眠并继续下一轮
3. 清理任务：
   - 每天一次清理过期 `reminder_deliveries`
   - 触发时机：TimerEngine 启动后按 24 小时间隔执行（首次启动不立即清理）

## 11. 测试策略

### 11.1 单元测试

1. reminder_key 生成与幂等判定。
2. todo/schedule 触发窗口判定边界（等于 start/end）。
3. recurring 提醒时间计算与展开边界（`times=-1`、`times>=2`）。
4. lookahead 边界行为（不回溯历史窗口）。

### 11.2 集成测试

1. 启动 TimerEngine，写入到点提醒，验证 sink 被调用且只调用一次。
2. 模拟重启（保留 delivery 记录），验证不会重复投递。
3. 模拟 sink 抛错，验证下轮可重试。

## 12. 分阶段落地计划

### Phase 1：最小可用提醒链路

状态：✅ 已完成

1. 增加 `reminder_deliveries` 表与 DB 方法。
2. 实现 Todo + 单次 Schedule 提醒触发。
3. CLI 启动/停止 TimerEngine。
4. 完成核心单元测试。

### Phase 2：重复日程提醒

状态：✅ 已完成

1. 抽离/复用 recurring 展开能力。
2. 支持 `remind_start_time` 的 occurrence 级提醒。
3. 完成 recurring 相关测试。

### Phase 3：稳定性增强

状态：⬜ 待实现

1. 批量控制、历史清理。
2. 完善日志与故障告警文案。
3. 评估系统通知 sink（macOS 通知中心等）扩展点。

## 13. 开源方案对比（选型结论）

可选方案：

1. 纯标准库轮询（`threading + time + sqlite3`）
2. APScheduler（成熟调度框架）
3. schedule 库（轻量定时）

V1 建议：**纯标准库轮询**。理由：

1. 当前项目依赖极简，便于快速落地与调试。
2. 提醒逻辑依赖 DB 动态查询（新增/更新任务实时生效），轮询模型天然匹配。
3. 先实现“正确触发+去重”核心能力，再决定是否引入调度框架。

后续若需要 cron 表达式、多 Job 持久化、复杂触发器，再评估迁移到 APScheduler。

## 14. 已确认决策（冻结）

1. 提醒输出渠道：V1 仅使用 CLI stdout 文本提醒。
2. 离线补偿：V1 不补发历史提醒（重启后仅处理当前与未来窗口）。
3. 重复日程提醒语义：采用 `remind_start_time + n*interval`。
4. Todo 自动完成：提醒触发后不自动标记 done。
5. 声音提示：V1 不加入 beep。
