## a. Commit message
- `5060f41 refactor: enforce pure plan-only flow`

## b. Modified Files and Summary of Changes
- `assistant_app/agent.py`
  - 删除 legacy intent/chat 兼容链路（不再解析 `intent`、不再做 legacy fallback）
  - 自然语言输入统一走 plan -> act -> observe -> replan 主循环
  - 保留并强化工具契约上下文：todo/schedule/internet_search/ask_user
  - planner 不可用提示统一为“计划执行服务暂时不可用”
- `assistant_app/cli.py`
  - CLI 继续保持“步骤进度 + 计划列表 + 执行结果 + 完成情况”的过程化输出（无“正在思考”）
  - 进度信息使用淡灰色前缀输出，便于区分助手结果文本
- `assistant_app/search.py`
  - 保持 SearchProvider 抽象与 Bing 默认实现解耦，方便后续替换搜索源
- `tests/test_agent.py`
  - 去除对 legacy intent/chat 分支行为的依赖，改为纯 planner 返回契约测试
  - 更新自然语言用例为 continue+done 的 plan-only 交互序列
  - 更新服务不可用断言文案为“计划执行服务”
  - 调整无效命令场景：通过 planner 观察后 done 收敛，避免旧链路语义
- `README.md`
  - 明确“纯 plan-only”机制（移除 legacy intent=chat 描述）
  - 首页能力描述改为“自然语言任务执行（plan-only）”

## c. Reasons and Purposes of Each Modification
- 满足“纯 plan-only”要求：杜绝 chat/intent 分叉，统一模型行为和调试路径。
- 降低维护成本：删除双链路后，工具契约、进度输出、失败兜底都集中在同一执行框架中。
- 提升测试可解释性：测试直接对 planner 契约建模，减少旧 intent 结构对新架构的干扰。

## d. Potential Issues in Current Code
- 若 planner 长期输出低质量 continue（例如重复无效命令），仍可能消耗多步后触发步数上限。
- internet_search 目前默认依赖 Bing 页面结构，解析规则后续可能需要随页面变化调整。
- 目前 done 文本质量仍依赖模型，若总结不充分，用户可能需要再发一轮查询查看细节。

## e. Unit Test Report
- 执行命令：`python3 -m unittest discover -s tests -p "test_*.py"`
- 结果：`Ran 105 tests ... OK`
- 执行命令：`python3 -m ruff check assistant_app tests`
- 结果：`All checks passed!`
