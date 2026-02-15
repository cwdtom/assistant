## a. Commit message
- 未提交（本轮已完成开发与测试，待你确认后再提交）

## b. Modified Files and Summary of Changes
- `assistant_app/agent.py`
  - 新增 plan-replan 主循环（非 slash 输入默认进入）
  - 新增四类工具路由：todo/schedule/internet_search/ask_user
  - 新增 ask_user 进程内 pending 会话态（可跨 slash 命令继续）
  - 新增 max_steps=20 兜底、planner 失败兜底、`取消当前任务` 终止机制
  - 新增“用户澄清后必须先执行一次 replan 工具动作”约束，避免 ask_user 重复追问死循环
  - 新增可选进度回调（progress callback），输出步骤进度、计划列表、动作结果与完成情况
  - 保留 legacy intent JSON 兼容路径，避免现有用例回归
- `assistant_app/cli.py`
  - 移除“正在思考...”等待动画
  - 接入 agent 进度回调，在 CLI 交互中逐步输出执行进程
- `assistant_app/search.py`
  - 新增搜索解耦抽象（`SearchProvider`）
  - 新增 `BingSearchProvider` 默认实现（标准库 urllib）
  - 新增 Bing 结果轻量提取与标准化结构 `SearchResult`
- `tests/test_agent.py`
  - 新增 plan-replan 相关测试：多步工具执行、ask_user 澄清、pending 与 slash 共存
  - 新增 internet_search 工具测试、max_steps 兜底测试、取消任务测试
  - 新增“澄清后禁止立即再次 ask_user”的回归测试
  - 新增 progress callback 输出回归测试
- `tests/test_cli.py`
  - 更新反馈测试为“进度输出”断言，确认不再依赖等待动画文案
- `README.md`
  - 更新自然语言机制说明为 plan-replan
  - 补充 ask_user / Bing 搜索 / 20 步兜底说明
  - 更新 CLI 反馈机制说明为“输出每一步进度”

## c. Reasons and Purposes of Each Modification
- 从单轮 intent 执行升级为循环代理，提升多目标任务与信息不完整场景的处理能力。
- 引入 ask_user 让代理在缺参时主动澄清，减少错误动作。
- 引入 internet_search 并做 provider 解耦，满足联网检索需求且便于后续替换搜索后端。
- 保留 legacy 兼容，确保当前已有命令和自然语言测试不回归。

## d. Potential Issues in Current Code
- Bing HTML 结构可能变化，解析规则需后续根据线上表现调整。
- observation 上限提升到 10000 * 100 后，真实运行时可能增加 token 开销。
- planner 若长期返回低质量 JSON，仍会触发兜底而非无限重试（这是设计取舍）。
- 进度输出较详细，在超长任务场景会增加 CLI 输出量（可后续引入 verbosity 配置）。

## e. Unit Test Report
- 执行命令：`python3 -m unittest discover -s tests -p "test_*.py"`
- 结果：`Ran 101 tests ... OK`
- lint：`python3 -m ruff check assistant_app tests` -> `All checks passed!`
