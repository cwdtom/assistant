from __future__ import annotations

import unittest
from typing import Any

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.tools.planner_tool_routing import (
    JsonPlannerToolRoute,
    build_json_planner_tool_executor,
)
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from pydantic import ValidationError


class PlannerToolRoutingTest(unittest.TestCase):
    def test_json_route_executes_legacy_command_when_prefix_matches(self) -> None:
        route = JsonPlannerToolRoute(
            tool="history",
            invalid_json_result="history 工具参数无效：需要 JSON 对象。",
            legacy_command_prefix="/history",
            payload_executor=lambda _payload, _raw_input: self.fail("payload executor should not run"),
        )
        executor = build_json_planner_tool_executor(
            route=route,
            command_executor=lambda _command: "历史会话(最近 1 轮):\n1. user: 你好",
        )

        observation = executor("/history list --limit 1")

        self.assertTrue(observation.ok)
        self.assertEqual(observation.tool, "history")
        self.assertIn("历史会话", observation.result)

    def test_json_route_treats_invalid_json_as_empty_object_payload(self) -> None:
        captured: dict[str, Any] = {}

        def _payload_executor(payload: dict[str, Any], raw_input: str) -> PlannerObservation:
            captured["payload"] = dict(payload)
            captured["raw_input"] = raw_input
            return PlannerObservation(tool="schedule", input_text=raw_input, ok=False, result="schedule.action 非法。")

        route = JsonPlannerToolRoute(
            tool="schedule",
            invalid_json_result="schedule 工具参数无效：需要 JSON 对象。",
            payload_executor=_payload_executor,
        )
        executor = build_json_planner_tool_executor(
            route=route,
            command_executor=lambda _command: "",
        )

        observation = executor("not-json")

        self.assertFalse(observation.ok)
        self.assertEqual(observation.result, "schedule.action 非法。")
        self.assertEqual(captured["payload"], {})
        self.assertEqual(captured["raw_input"], "not-json")

    def test_json_route_overrides_action_with_compat_action(self) -> None:
        captured: dict[str, Any] = {}

        def _payload_executor(payload: dict[str, Any], raw_input: str):
            captured["payload"] = dict(payload)
            captured["raw_input"] = raw_input
            return PlannerObservation(tool="history_search", input_text=raw_input, ok=True, result="ok")

        route = JsonPlannerToolRoute(
            tool="history_search",
            invalid_json_result="history_search 工具参数无效：需要 JSON 对象。",
            compat_action="search",
            payload_executor=_payload_executor,
        )
        executor = build_json_planner_tool_executor(
            route=route,
            command_executor=lambda _command: "",
        )

        observation = executor('{"action":"list","keyword":"牛奶","limit":5}')

        self.assertTrue(observation.ok)
        self.assertEqual(captured["payload"]["action"], "search")
        self.assertEqual(captured["payload"]["keyword"], "牛奶")
        self.assertEqual(captured["raw_input"], '{"action":"list","keyword":"牛奶","limit":5}')

    def test_json_route_thoughts_supports_legacy_command_prefix(self) -> None:
        route = JsonPlannerToolRoute(
            tool="thoughts",
            invalid_json_result="thoughts 工具参数无效：需要 JSON 对象。",
            legacy_command_prefix="/thoughts",
            payload_executor=lambda _payload, _raw_input: self.fail("payload executor should not run"),
        )
        executor = build_json_planner_tool_executor(
            route=route,
            command_executor=lambda _command: "想法列表(状态: pending|completed):\n| ID | 内容 |",
        )

        observation = executor("/thoughts list")

        self.assertTrue(observation.ok)
        self.assertEqual(observation.tool, "thoughts")
        self.assertIn("想法列表", observation.result)

    def test_json_route_uses_typed_payload_executor_when_runtime_payload_provided(self) -> None:
        captured: dict[str, Any] = {}

        def _typed_payload_executor(payload: RuntimePlannerActionPayload, raw_input: str):
            captured["tool_name"] = payload.tool_name
            captured["raw_input"] = raw_input
            return PlannerObservation(tool="history", input_text=raw_input, ok=True, result="ok")

        route = JsonPlannerToolRoute(
            tool="history",
            invalid_json_result="history 工具参数无效：需要 JSON 对象。",
            payload_executor=lambda _payload, _raw_input: self.fail("json payload executor should not run"),
            typed_payload_executor=_typed_payload_executor,
        )
        executor = build_json_planner_tool_executor(
            route=route,
            command_executor=lambda _command: "",
        )

        observation = executor(
            "not-json",
            RuntimePlannerActionPayload(tool_name="history_search", arguments={"keyword": "牛奶", "limit": 3}),
        )

        self.assertTrue(observation.ok)
        self.assertEqual(captured["tool_name"], "history_search")
        self.assertEqual(captured["raw_input"], "not-json")

    def test_json_route_rejects_blank_tool_name(self) -> None:
        with self.assertRaises(ValidationError):
            JsonPlannerToolRoute(
                tool=" ",
                invalid_json_result="invalid",
                payload_executor=lambda _payload, _raw_input: PlannerObservation(
                    tool="history",
                    input_text="",
                    ok=True,
                    result="ok",
                ),
            )


if __name__ == "__main__":
    unittest.main()
