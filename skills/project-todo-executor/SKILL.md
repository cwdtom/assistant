---
name: project-todo-executor
description: 按项目待办驱动开发执行。用于用户要求读取 todo 中 tag=project 的待办，按 priority 从小到大逐项实现，并在每完成一项后立即调用 $commit 技能执行“CR + README 同步 + commit”。
---

# Project Todo Executor

按以下顺序执行，保持一项一提交。

## 1) 读取 project 待办

- 优先读取 `project` 标签：
  - `printf "/todo list --tag project\nexit\n" | python3 main.py`
- 仅保留状态为“待办”的条目；忽略“完成”条目。
- 若为空，先告知用户并停止后续实现。

## 2) 生成执行顺序

- 按 `priority` 升序排序（数值越小优先级越高）。
- 同优先级按 `id` 升序排序，保证顺序稳定。
- 在开始编码前，先回报将要执行的顺序清单。

## 3) 逐项实现

- 每次只处理一个待办，不并行改多个需求。
- 对当前条目先消除歧义；若需求不清，先向用户确认再改代码。
- 采用小步修改，优先复用现有实现。
- 同步补充或更新测试。
- 任何行为变化都同步更新 `README.md`。

## 4) 每项完成后立即验证

- 依次执行：
  - `python3 -m ruff check .`
  - `python3 -m mypy --config-file=pyproject.toml assistant_app main.py`
  - `python3 -m pytest -q`
- 若失败，先修复再进入提交步骤。

## 5) 每项完成后立即提交

- 对每个完成的待办，立即调用 `$commit` 技能完成：
  - 代码审查（CR）
  - README 一致性校验/修正
  - 生成并提交 commit
- 提交完成后，立即把对应待办标记为完成：
  - `printf "/todo done <id>\nexit\n" | python3 main.py`
- 一次 commit 只包含当前待办对应改动。
- 标记完成后回报：待办 id、commit hash、剩余待办数量。

## 6) 迭代到清空

- 重复步骤 3~5，直到当前 `project` 标签下无“待办”条目。
- 最终给出汇总：完成列表、未完成原因（如有）、下一步建议。
