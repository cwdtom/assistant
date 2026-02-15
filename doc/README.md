# 文档导航（Session 快速入口）

目标：让后续 session 在 5~10 分钟内掌握项目当前状态与历史背景。

## 推荐阅读顺序

1. `doc/session-quickstart.md`
   - 当前系统形态（plan-only）、关键入口、运行/测试命令、配置项、已知边界。
2. `README.md`（项目根目录）
   - 对外使用说明、命令清单、环境变量说明。
3. `doc/archive/`
   - 历史阶段文档（设计方案、CR 报告、阶段总结），用于追溯决策背景。

## 目录结构

- `doc/session-quickstart.md`：当前版本的单页总览（建议每次较大迭代后更新）。
- `doc/archive/2026021515:loop_agent:plan-replan.md`：loop_agent 阶段提交与测试报告。
- `doc/archive/loop_agent.md`：loop_agent 设计与决策冻结文档。
- `doc/archive/2026021514:main:cr.md`：阶段性深度 CR 报告。
- `doc/archive/2026021422:main:work-summary.md`：早期阶段成果总结。

## 维护约定

- 新迭代文档优先放 `doc/archive/`，避免根目录堆积。
- `doc/session-quickstart.md` 只保留“当前有效事实”，不写过时计划。
- 若行为变更，请同时同步：
  - 根 `README.md`
  - `doc/session-quickstart.md`
