from __future__ import annotations

import unittest

from assistant_app.planner_plan_replan import (
    PLAN_ONCE_PROMPT,
    PLANNER_CAPABILITIES_TEXT,
    REPLAN_PROMPT,
    normalize_plan_decision,
    normalize_replan_decision,
)
from assistant_app.planner_thought import normalize_thought_decision
from assistant_app.schemas.planner import (
    ObservationPayload,
    PlannedDecision,
    PlanPromptPayload,
    PlanResponsePayload,
    ReplanDoneDecision,
    ReplannedDecision,
    ReplanResponsePayload,
    ThoughtContinueDecision,
    ThoughtDecisionMessagePayload,
    ThoughtObservationMessagePayload,
    ThoughtPromptPayload,
    ThoughtResponsePayload,
    ToolReplyPayload,
    parse_tool_reply_payload,
)
from pydantic import ValidationError


class PlannerSchemaTest(unittest.TestCase):
    def test_plan_and_replan_prompts_describe_timer_and_user_profile_capabilities(self) -> None:
        self.assertIn("- timer：通用定时 planner 任务管理", PLANNER_CAPABILITIES_TEXT)
        self.assertIn("- user_profile：读取和覆盖用户画像文件", PLANNER_CAPABILITIES_TEXT)
        self.assertIn("schedule|timer|internet_search|history|thoughts|user_profile|system", PLAN_ONCE_PROMPT)
        self.assertIn("schedule|timer|internet_search|history|thoughts|user_profile|system", REPLAN_PROMPT)

    def test_planned_decision_normalizes_tools(self) -> None:
        decision = PlannedDecision.model_validate(
            {
                "status": "planned",
                "goal": "查询最近历史",
                "plan": [
                    {"task": "检索历史", "completed": False, "tools": ["history", "history"]},
                ],
            }
        )

        self.assertEqual(decision.plan[0].tools, ["history"])

    def test_planned_decision_rejects_completed_step(self) -> None:
        with self.assertRaises(ValidationError):
            PlannedDecision.model_validate(
                {
                    "status": "planned",
                    "goal": "查询最近历史",
                    "plan": [{"task": "检索历史", "completed": True, "tools": ["history"]}],
                }
            )

    def test_planned_decision_rejects_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            PlannedDecision.model_validate(
                {
                    "status": "planned",
                    "goal": "查询最近历史",
                    "plan": [{"task": "检索历史", "completed": False, "tools": ["history"]}],
                    "unexpected": "value",
                }
            )

    def test_replanned_decision_requires_pending_step(self) -> None:
        with self.assertRaises(ValidationError):
            ReplannedDecision.model_validate(
                {
                    "status": "replanned",
                    "plan": [{"task": "检索历史", "completed": True, "tools": ["history"]}],
                }
            )

    def test_thought_continue_rejects_non_execution_tool(self) -> None:
        with self.assertRaises(ValidationError):
            ThoughtContinueDecision.model_validate(
                {
                    "status": "continue",
                    "current_step": "继续执行",
                    "next_action": {"tool": "ask_user", "input": "请确认"},
                    "question": None,
                    "response": None,
                }
            )

    def test_tool_reply_payload_accepts_nullable_content(self) -> None:
        payload = ToolReplyPayload.model_validate(
            {
                "assistant_message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "schedule_list", "arguments": "{}"},
                        }
                    ],
                },
                "reasoning_content": None,
            }
        )

        self.assertIsNone(payload.assistant_message.content)
        self.assertEqual(payload.assistant_message.tool_calls[0].function.name, "schedule_list")

    def test_parse_tool_reply_payload_normalizes_reasoning_and_filters_bad_calls(self) -> None:
        payload = parse_tool_reply_payload(
            {
                "assistant_message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "broken", "type": "function"},
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "schedule_list", "arguments": "{}"},
                        },
                    ],
                },
                "reasoning_content": 123,
            }
        )

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.reasoning_content, "123")
        self.assertEqual(len(payload.assistant_message.tool_calls), 1)
        self.assertEqual(payload.assistant_message.tool_calls[0].function.name, "schedule_list")

    def test_plan_response_payload_wraps_decision_model(self) -> None:
        payload = PlanResponsePayload.model_validate(
            {
                "decision": {
                    "status": "planned",
                    "goal": "查询最近历史",
                    "plan": [{"task": "检索历史", "completed": False, "tools": ["history"]}],
                },
                "raw_response": '{"status":"planned"}',
            }
        )

        self.assertIsInstance(payload.decision, PlannedDecision)
        self.assertEqual(payload.decision.goal, "查询最近历史")

    def test_plan_prompt_payload_wraps_context_models(self) -> None:
        payload = PlanPromptPayload.model_validate(
            {
                "phase": "plan",
                "goal": "查询最近历史",
                "clarification_history": [{"role": "assistant_question", "content": "请确认范围"}],
                "step_count": 1,
                "max_steps": 20,
                "latest_plan": [{"task": "检索历史", "completed": False, "tools": ["history"]}],
                "current_plan_index": 0,
                "completed_subtasks": [{"item": "澄清目标", "result": "用户已确认"}],
                "user_profile": "偏好：先给结论",
                "time": "2026-03-09 14:20",
            }
        )

        self.assertEqual(payload.latest_plan[0].task, "检索历史")
        self.assertEqual(payload.completed_subtasks[0].item, "澄清目标")

    def test_thought_prompt_payload_normalizes_current_subtask_tools(self) -> None:
        payload = ThoughtPromptPayload.model_validate(
            {
                "phase": "thought",
                "current_plan_item": "执行搜索",
                "clarification_history": [],
                "step_count": 2,
                "max_steps": 20,
                "current_subtask": {
                    "item": "执行搜索",
                    "index": 1,
                    "total": 2,
                    "tools": [" schedule_list ", "done", "done"],
                },
                "completed_subtasks": [],
                "current_subtask_observations": [
                    {"tool": "history_list", "input": "{\"limit\": 5}", "ok": True, "result": "[]"}
                ],
                "user_profile": None,
                "time": "2026-03-09 14:20",
            }
        )

        self.assertEqual(payload.current_subtask.tools, ["schedule_list", "done"])
        self.assertEqual(payload.current_subtask_observations[0].tool, "history_list")

    def test_thought_message_payloads_accept_typed_content(self) -> None:
        decision_payload = ThoughtDecisionMessagePayload.model_validate(
            {
                "phase": "thought_decision",
                "decision": {
                    "status": "done",
                    "current_step": "总结",
                    "response": "已完成。",
                },
            }
        )
        observation_payload = ThoughtObservationMessagePayload.model_validate(
            {
                "phase": "thought_observation",
                "observation": {
                    "tool": "schedule_list",
                    "input": "{}",
                    "ok": True,
                    "result": "[]",
                },
            }
        )

        self.assertEqual(decision_payload.decision.status, "done")
        self.assertIsInstance(observation_payload.observation, ObservationPayload)

    def test_replan_response_payload_accepts_done_union(self) -> None:
        payload = ReplanResponsePayload.model_validate(
            {
                "decision": {
                    "status": "done",
                    "response": "已完成。",
                },
                "raw_response": '{"status":"done"}',
            }
        )

        self.assertIsInstance(payload.decision, ReplanDoneDecision)
        self.assertEqual(payload.decision.response, "已完成。")

    def test_thought_response_payload_requires_assistant_message_for_tool_call_id(self) -> None:
        with self.assertRaises(ValidationError):
            ThoughtResponsePayload.model_validate(
                {
                    "decision": {
                        "status": "continue",
                        "current_step": "检索历史",
                        "next_action": {"tool": "history", "input": "/history list"},
                        "question": None,
                        "response": None,
                    },
                    "tool_call_id": "call_1",
                }
            )

    def test_normalize_plan_decision_ignores_extra_fields_for_compatibility(self) -> None:
        decision = normalize_plan_decision(
            {
                "status": "planned",
                "goal": "查询最近历史",
                "plan": [{"task": "检索历史", "completed": False, "tools": ["history"]}],
                "unexpected": "value",
            }
        )

        self.assertIsInstance(decision, PlannedDecision)

    def test_normalize_replan_decision_rejects_completed_only_plan(self) -> None:
        decision = normalize_replan_decision(
            {
                "status": "replanned",
                "plan": [{"task": "检索历史", "completed": True, "tools": ["history"]}],
            }
        )

        self.assertIsNone(decision)

    def test_normalize_replan_decision_ignores_extra_fields_for_done_compatibility(self) -> None:
        decision = normalize_replan_decision(
            {
                "status": "DONE",
                "response": "已完成。",
                "plan": [{"task": "旧步骤", "completed": False, "tools": ["history"]}],
                "unexpected": "value",
            }
        )

        self.assertIsInstance(decision, ReplanDoneDecision)

    def test_normalize_thought_decision_uses_first_plan_item_as_current_step(self) -> None:
        decision = normalize_thought_decision(
            {
                "status": "continue",
                "plan": [{"task": "检索历史", "completed": False, "tools": ["history"]}],
                "next_action": {"tool": "history", "input": "/history list"},
                "question": None,
                "response": None,
            }
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.current_step, "检索历史")

    def test_normalize_thought_decision_ignores_extra_fields_for_compatibility(self) -> None:
        decision = normalize_thought_decision(
            {
                "status": "ask_user",
                "current_step": "补充信息",
                "question": "请确认标签",
                "next_action": None,
                "response": None,
                "unexpected": "value",
            }
        )

        self.assertIsNotNone(decision)

    def test_normalize_thought_decision_rejects_continue_with_response_text(self) -> None:
        decision = normalize_thought_decision(
            {
                "status": "continue",
                "current_step": "检索历史",
                "next_action": {"tool": "history", "input": "/history list"},
                "question": None,
                "response": "不应携带最终响应",
            }
        )

        self.assertIsNone(decision)


if __name__ == "__main__":
    unittest.main()
