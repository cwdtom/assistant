from __future__ import annotations

from typing import Any


def _remaining_pending_plan_items(outer: Any) -> list[str]:
    latest_plan = getattr(outer, "latest_plan", None)
    if not isinstance(latest_plan, list):
        return []
    remaining: list[str] = []
    for step in latest_plan:
        if bool(getattr(step, "completed", False)):
            continue
        item = str(getattr(step, "item", "")).strip()
        if item:
            remaining.append(item)
    return remaining


def run_outer_plan_loop(agent: Any, task: Any) -> str:
    from assistant_app.agent_components.models import TaskInterruptedError, ThoughtToolCallingError

    try:
        while True:
            agent._raise_if_task_interrupted()
            if task.step_count >= agent._plan_replan_max_steps:
                return agent._finalize_planner_task(task, agent._format_step_limit_response(task))

            agent._emit_decision_progress(task)

            if not task.plan_initialized:
                if not agent._initialize_plan_once(task):
                    return agent._finalize_planner_task(task, agent._planner_unavailable_text())
                if task.plan_ack_only:
                    return agent._finalize_planner_task(task, "")

            if task.awaiting_clarification:
                agent._pending_plan_task = task
                return "请确认：请补充必要信息。"

            replan_outcome, replan_response = agent._run_replan_gate(task)
            if replan_outcome == "retry":
                continue
            if replan_outcome == "unavailable":
                return agent._finalize_planner_task(task, agent._planner_unavailable_text())
            if replan_outcome == "done":
                final_response = replan_response or agent._planner_unavailable_text()
                final_response = agent._rewrite_final_response(final_response)
                return agent._finalize_planner_task(task, final_response)

            task.inner_context = agent._new_inner_context(task)
            loop_outcome, payload = agent._run_inner_react_loop(task)
            if loop_outcome == "replan":
                continue
            if loop_outcome == "ask_user":
                agent._pending_plan_task = task
                return payload or "请确认：请补充必要信息。"
            if loop_outcome == "done_candidate":
                task.needs_replan = True
                continue
            if loop_outcome == "step_limit":
                return agent._finalize_planner_task(task, agent._format_step_limit_response(task))
            return agent._finalize_planner_task(task, agent._planner_unavailable_text())
    except TaskInterruptedError:
        return agent._finalize_interrupted_task(task)
    except ThoughtToolCallingError as exc:
        return agent._finalize_planner_task(task, str(exc))


def emit_decision_progress(agent: Any, task: Any) -> None:
    planned_total_text = agent._progress_total_text(task)
    current_plan_total = agent._current_plan_total_text(task)
    plan_suffix = f"（当前计划 {current_plan_total} 步）" if current_plan_total is not None else ""
    progress_text = (
        f"步骤进度：已执行 {task.step_count}/{planned_total_text}，"
        f"开始第 {task.step_count + 1} 步决策。{plan_suffix}"
    )
    agent._emit_progress(progress_text)


def initialize_plan_once(agent: Any, task: Any) -> bool:
    from assistant_app.agent_components.models import PlanStep

    outer = agent._outer_context(task)
    plan_payload = agent._request_plan_payload(task)
    if plan_payload is None:
        return False
    plan_decision = plan_payload.decision
    expanded_goal = plan_decision.goal.strip()
    if expanded_goal:
        outer.goal = expanded_goal
        task.goal = expanded_goal
    agent._append_planner_decision_observation(task, phase="plan", decision=plan_decision)
    latest_plan = [
        PlanStep(item=item.task, completed=item.completed, tools=item.tools)
        for item in plan_decision.plan
    ]
    if not latest_plan:
        task.plan_ack_only = True
        outer.latest_plan = []
        task.plan_initialized = True
        outer.current_plan_index = 0
        agent._emit_progress("规划完成：空计划，ACK 后结束。")
        return True
    task.plan_ack_only = False
    if expanded_goal:
        agent._notify_plan_goal_result(task, expanded_goal)
    outer.latest_plan = latest_plan
    task.plan_initialized = True
    outer.current_plan_index = 0
    agent._emit_progress(f"规划完成：共 {len(outer.latest_plan)} 步。")
    agent._emit_plan_progress(task)
    return True


def run_replan_gate(agent: Any, task: Any) -> tuple[str, str | None]:
    from assistant_app.agent_components.models import PlannerObservation, PlanStep
    from assistant_app.schemas.planner import ReplanDoneDecision

    outer = agent._outer_context(task)
    if not task.needs_replan:
        return "skipped", None

    task.step_count += 1
    replan_payload = agent._request_replan_payload(task)
    if replan_payload is None:
        task.planner_failure_rounds += 1
        agent._append_observation(
            task,
            PlannerObservation(
                tool="replan",
                input_text="plan",
                ok=False,
                result="replan 输出不符合 JSON 契约。",
            ),
        )
        if task.planner_failure_rounds >= agent._plan_continuous_failure_limit:
            return "unavailable", None
        agent._emit_progress("重规划失败：模型输出不符合契约，准备重试。")
        return "retry", None

    task.planner_failure_rounds = 0
    replan_decision = replan_payload.decision
    agent._append_planner_decision_observation(task, phase="replan", decision=replan_decision)
    if isinstance(replan_decision, ReplanDoneDecision):
        remaining_items = _remaining_pending_plan_items(outer)
        if remaining_items:
            task.needs_replan = False
            agent._append_observation(
                task,
                PlannerObservation(
                    tool="replan",
                    input_text="done",
                    ok=False,
                    result="replan 提前 done：仍有未完成步骤，忽略本次 done 并继续当前计划。",
                ),
            )
            agent._emit_progress(
                "重规划返回 done，但仍存在未完成步骤；已忽略该结果并继续执行剩余计划。"
            )
            return "ok", None
        response = replan_decision.response.strip()
        task.needs_replan = False
        return "done", response or None
    updated_plan = [
        PlanStep(item=step.task, completed=step.completed, tools=step.tools)
        for step in replan_decision.plan
    ]
    outer.latest_plan = updated_plan
    if not outer.latest_plan:
        return "unavailable", None
    outer.current_plan_index = 0
    agent._sync_current_plan_index(outer)
    task.needs_replan = False
    agent._emit_progress(f"重规划完成：共 {len(outer.latest_plan)} 步。")
    agent._emit_plan_progress(task)
    agent._notify_replan_continue_subtask_result(task)
    return "ok", None


def run_inner_react_loop(agent: Any, task: Any) -> tuple[str, str | None]:
    from assistant_app.agent_components.models import ClarificationTurn, PlannerObservation
    from assistant_app.agent_components.parsing_utils import _is_same_question_text
    from assistant_app.agent_components.render_helpers import _truncate_text
    from assistant_app.schemas.planner import ThoughtAskUserDecision, ThoughtDoneDecision

    outer = agent._outer_context(task)
    emit_progress = False
    while True:
        agent._raise_if_task_interrupted()
        if task.step_count >= agent._plan_replan_max_steps:
            return "step_limit", None

        if emit_progress:
            agent._emit_decision_progress(task)
        emit_progress = True

        agent._emit_current_plan_item_progress(task)
        task.step_count += 1
        thought_payload = agent._request_thought_payload(task)
        if thought_payload is None:
            task.planner_failure_rounds += 1
            agent._append_observation(
                task,
                PlannerObservation(
                    tool="thought",
                    input_text="decision",
                    ok=False,
                    result="thought 输出不符合 JSON 契约。",
                ),
            )
            if task.planner_failure_rounds >= agent._plan_continuous_failure_limit:
                return "unavailable", None
            agent._emit_progress("思考失败：模型输出不符合契约，准备重试。")
            continue

        thought_decision = thought_payload.decision
        status = thought_decision.status
        current_step = thought_decision.current_step.strip()
        if isinstance(thought_decision, ThoughtDoneDecision):
            response_text = str(thought_decision.response or "").strip()
            if not response_text:
                task.planner_failure_rounds += 1
                agent._append_observation(
                    task,
                    PlannerObservation(
                        tool="thought",
                        input_text=current_step or "done",
                        ok=False,
                        result="status=done 但 response 为空，准备重试。",
                    ),
                )
                if task.planner_failure_rounds >= agent._plan_continuous_failure_limit:
                    return "unavailable", None
                agent._emit_progress("思考失败：done 缺少 response，准备重试。")
                continue
        task.planner_failure_rounds = 0
        agent._append_planner_decision_observation(task, phase="thought", decision=thought_decision)
        agent._emit_progress(f"思考决策：{status} | {current_step or '（未提供步骤）'}")

        if isinstance(thought_decision, ThoughtDoneDecision):
            response = str(thought_decision.response or "").strip()
            inner_context = agent._ensure_inner_context(task)
            inner_context.response = response
            completed_item = agent._current_plan_item_text(task) or current_step or "当前子任务"
            latest_success_result = agent._latest_success_observation_result(task)
            completed_result = agent._merge_summary_with_detail(
                summary=response,
                detail=latest_success_result,
            )
            if not completed_result:
                completed_result = "子任务已完成。"
            agent._append_completed_subtask(
                task,
                item=completed_item,
                result=completed_result,
            )
            # done means current subtask is completed; advance plan cursor before replan.
            if outer.latest_plan:
                if 0 <= outer.current_plan_index < len(outer.latest_plan):
                    outer.latest_plan[outer.current_plan_index].completed = True
                outer.current_plan_index = min(outer.current_plan_index + 1, len(outer.latest_plan))
                agent._sync_current_plan_index(outer)
            task.needs_replan = True
            return "replan", None

        if isinstance(thought_decision, ThoughtAskUserDecision):
            question = thought_decision.question.strip()
            if not question:
                agent._append_observation(
                    task,
                    PlannerObservation(
                        tool="ask_user",
                        input_text="",
                        ok=False,
                        result="ask_user 缺少提问内容。",
                    ),
                )
                continue
            if (
                _is_same_question_text(task.last_ask_user_question, question)
                and len(outer.clarification_history) > task.last_ask_user_clarification_len
            ):
                task.ask_user_repeat_count += 1
                agent._append_observation(
                    task,
                    PlannerObservation(
                        tool="ask_user",
                        input_text=question,
                        ok=False,
                        result="重复提问：用户已补充信息，请基于已知信息执行重规划。",
                    ),
                )
                if task.ask_user_repeat_count >= agent._plan_continuous_failure_limit:
                    return (
                        "done_candidate",
                        "我已经拿到你的补充信息，但仍无法完成重规划。请直接使用 /schedule 命令。",
                    )
                continue
            ask_turns = sum(1 for turn in outer.clarification_history if turn.role == "assistant_question")
            if ask_turns >= 6:
                return "done_candidate", "澄清次数过多，我仍无法稳定重规划。请直接使用 /schedule 命令。"
            task.ask_user_repeat_count = 0
            task.last_ask_user_question = question
            task.last_ask_user_clarification_len = len(outer.clarification_history)
            outer.clarification_history.append(
                ClarificationTurn(role="assistant_question", content=question)
            )
            task.awaiting_clarification = True
            agent._emit_progress(f"步骤动作：ask_user -> {question}")
            return "ask_user", f"请确认：{question}"

        next_action = thought_decision.next_action
        action_tool = next_action.tool.strip().lower()
        action_input = next_action.input.strip()
        tool_call_id = thought_payload.tool_call_id
        agent._emit_progress(f"步骤动作：{action_tool} -> {action_input}")
        agent._raise_if_task_interrupted()
        task.step_count += 1
        observation = agent._execute_planner_tool(action_tool=action_tool, action_input=action_input)
        normalized_observation = agent._append_observation(task, observation)
        if tool_call_id:
            agent._append_thought_tool_result_message(
                task,
                observation=normalized_observation,
                tool_call_id=tool_call_id,
            )
        else:
            agent._append_thought_observation_message(task, normalized_observation)
        status_text = "成功" if observation.ok else "失败"
        if observation.ok:
            task.successful_steps += 1
        else:
            task.failed_steps += 1
        preview = _truncate_text(observation.result.replace("\n", " "), 220)
        agent._emit_progress(f"步骤结果：{status_text} | {preview}")
        planned_total_text = agent._progress_total_text(task)
        current_plan_total = agent._current_plan_total_text(task)
        plan_suffix = f"，当前计划 {current_plan_total} 步" if current_plan_total is not None else ""
        agent._emit_progress(
            "完成情况："
            f"成功 {task.successful_steps} 步，失败 {task.failed_steps} 步，"
            f"已执行 {task.step_count}/{planned_total_text} 步（上限 {agent._plan_replan_max_steps}{plan_suffix}）。"
        )
