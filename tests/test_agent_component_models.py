from __future__ import annotations

import unittest

from assistant_app.agent_components.models import (
    InnerReActContext,
    OuterPlanContext,
    PendingPlanTask,
    PlannerObservation,
    PlannerTextMessage,
    PlannerToolMessage,
    PlanStep,
)
from assistant_app.schemas.planner import AssistantToolMessage, ToolCallPayload
from pydantic import ValidationError


class AgentComponentModelsTest(unittest.TestCase):
    def test_plan_step_normalizes_and_deduplicates_tools(self) -> None:
        step = PlanStep(item="检索历史", completed=False, tools=["history", "history", "done"])

        self.assertEqual(step.tools, ["history", "done"])

    def test_plan_step_rejects_invalid_tool(self) -> None:
        with self.assertRaises(ValidationError):
            PlanStep(item="检索历史", completed=False, tools=["unknown"])

    def test_outer_context_defaults_are_isolated(self) -> None:
        first = OuterPlanContext(goal="任务一")
        second = OuterPlanContext(goal="任务二")

        first.latest_plan.append(PlanStep(item="步骤一", tools=["history"]))

        self.assertEqual(len(first.latest_plan), 1)
        self.assertEqual(second.latest_plan, [])

    def test_inner_context_defaults_are_isolated(self) -> None:
        first = InnerReActContext()
        second = InnerReActContext()

        first.observations.append(PlannerObservation(tool="history", input_text="list", ok=True, result="ok"))

        self.assertEqual(len(first.observations), 1)
        self.assertEqual(second.observations, [])

    def test_pending_task_requires_goal(self) -> None:
        with self.assertRaises(ValidationError):
            PendingPlanTask(goal="")

    def test_outer_context_accepts_text_message_models(self) -> None:
        outer = OuterPlanContext(
            goal="任务",
            outer_messages=[PlannerTextMessage(role="user", content="你好")],
        )

        self.assertEqual(outer.outer_messages[0].role, "user")

    def test_inner_context_accepts_assistant_and_tool_message_models(self) -> None:
        inner = InnerReActContext(
            thought_messages=[
                PlannerTextMessage(role="system", content="prompt"),
                AssistantToolMessage(
                    role="assistant",
                    content=None,
                    tool_calls=[
                        ToolCallPayload(
                            id="call_1",
                            type="function",
                            function={"name": "history_list", "arguments": "{}"},
                        )
                    ],
                ),
                PlannerToolMessage(tool_call_id="call_1", content="{}"),
            ]
        )

        self.assertEqual(len(inner.thought_messages), 3)


if __name__ == "__main__":
    unittest.main()
