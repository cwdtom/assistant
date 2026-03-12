from __future__ import annotations

import unittest
from typing import Any

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.planner_tool_executor import PlannerToolExecutor


def _ok_observation(tool: str, raw_input: str) -> PlannerObservation:
    return PlannerObservation(tool=tool, input_text=raw_input, ok=True, result="ok")


def _default_json_executor(tool: str) -> Any:
    return lambda payload, raw_input: _ok_observation(tool, raw_input)


def _build_executor(
    *,
    command_executor: Any | None = None,
    schedule_executor: Any | None = None,
    timer_executor: Any | None = None,
    history_executor: Any | None = None,
    history_search_executor: Any | None = None,
    thoughts_executor: Any | None = None,
    user_profile_executor: Any | None = None,
    system_executor: Any | None = None,
    internet_search_executor: Any | None = None,
) -> PlannerToolExecutor:
    resolved_command_executor = command_executor or (lambda command: command)
    return PlannerToolExecutor(
        command_executor=resolved_command_executor,
        schedule_executor=schedule_executor or _default_json_executor("schedule"),
        timer_executor=timer_executor or _default_json_executor("timer"),
        history_executor=history_executor or _default_json_executor("history"),
        history_search_executor=history_search_executor or _default_json_executor("history_search"),
        thoughts_executor=thoughts_executor or _default_json_executor("thoughts"),
        user_profile_executor=user_profile_executor or _default_json_executor("user_profile"),
        system_executor=system_executor or _default_json_executor("system"),
        internet_search_executor=internet_search_executor
        or (lambda action_input, action_payload=None: _ok_observation("internet_search", action_input)),
    )


class PlannerToolExecutorTest(unittest.TestCase):
    def test_unknown_tool_returns_failed_observation(self) -> None:
        executor = _build_executor()

        observation = executor.execute(action_tool="unknown_tool", action_input="payload")

        self.assertFalse(observation.ok)
        self.assertEqual("unknown_tool", observation.tool)
        self.assertEqual("未知工具: unknown_tool", observation.result)

    def test_history_search_route_adds_search_compat_action(self) -> None:
        captured_payloads: list[tuple[dict[str, Any], str]] = []

        def history_search_executor(
            payload: dict[str, Any],
            raw_input: str,
        ) -> PlannerObservation:
            captured_payloads.append((payload, raw_input))
            return _ok_observation("history_search", raw_input)

        executor = _build_executor(history_search_executor=history_search_executor)

        observation = executor.execute(
            action_tool="history_search",
            action_input='{"keyword":"咖啡","limit":2}',
        )

        self.assertTrue(observation.ok)
        self.assertEqual(1, len(captured_payloads))
        payload, raw_input = captured_payloads[0]
        self.assertEqual('{"keyword":"咖啡","limit":2}', raw_input)
        self.assertEqual("search", payload["action"])
        self.assertEqual("咖啡", payload["keyword"])

    def test_system_route_executes_legacy_date_command(self) -> None:
        captured_commands: list[str] = []

        executor = _build_executor(
            command_executor=lambda command: captured_commands.append(command) or "2026-03-10 15:16:17",
        )

        observation = executor.execute(action_tool="system", action_input="/date")

        self.assertTrue(observation.ok)
        self.assertEqual(["/date"], captured_commands)
        self.assertEqual("2026-03-10 15:16:17", observation.result)

    def test_timer_route_uses_json_payload_without_legacy_command(self) -> None:
        captured_payloads: list[tuple[dict[str, Any], str]] = []

        def timer_executor(payload: dict[str, Any], raw_input: str) -> PlannerObservation:
            captured_payloads.append((payload, raw_input))
            return _ok_observation("timer", raw_input)

        executor = _build_executor(timer_executor=timer_executor)

        observation = executor.execute(
            action_tool="timer",
            action_input='{"action":"list"}',
        )

        self.assertTrue(observation.ok)
        self.assertEqual(1, len(captured_payloads))
        payload, raw_input = captured_payloads[0]
        self.assertEqual({"action": "list"}, payload)
        self.assertEqual('{"action":"list"}', raw_input)

    def test_user_profile_route_uses_json_payload_without_legacy_command(self) -> None:
        captured_payloads: list[tuple[dict[str, Any], str]] = []

        def user_profile_executor(payload: dict[str, Any], raw_input: str) -> PlannerObservation:
            captured_payloads.append((payload, raw_input))
            return _ok_observation("user_profile", raw_input)

        executor = _build_executor(user_profile_executor=user_profile_executor)

        observation = executor.execute(
            action_tool="user_profile",
            action_input='{"action":"overwrite","content":""}',
        )

        self.assertTrue(observation.ok)
        self.assertEqual(1, len(captured_payloads))
        payload, raw_input = captured_payloads[0]
        self.assertEqual({"action": "overwrite", "content": ""}, payload)
        self.assertEqual('{"action":"overwrite","content":""}', raw_input)


if __name__ == "__main__":
    unittest.main()
