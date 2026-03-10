from __future__ import annotations

import unittest
from typing import Any

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.planner_tool_executor import PlannerToolExecutor


def _ok_observation(tool: str, raw_input: str) -> PlannerObservation:
    return PlannerObservation(tool=tool, input_text=raw_input, ok=True, result="ok")


class PlannerToolExecutorTest(unittest.TestCase):
    def test_unknown_tool_returns_failed_observation(self) -> None:
        executor = PlannerToolExecutor(
            command_executor=lambda command: command,
            schedule_executor=lambda payload, raw_input: _ok_observation("schedule", raw_input),
            history_executor=lambda payload, raw_input: _ok_observation("history", raw_input),
            history_search_executor=lambda payload, raw_input: _ok_observation("history_search", raw_input),
            thoughts_executor=lambda payload, raw_input: _ok_observation("thoughts", raw_input),
            system_executor=lambda payload, raw_input: _ok_observation("system", raw_input),
            internet_search_executor=lambda action_input, action_payload=None: _ok_observation(
                "internet_search",
                action_input,
            ),
        )

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

        executor = PlannerToolExecutor(
            command_executor=lambda command: command,
            schedule_executor=lambda payload, raw_input: _ok_observation("schedule", raw_input),
            history_executor=lambda payload, raw_input: _ok_observation("history", raw_input),
            history_search_executor=history_search_executor,
            thoughts_executor=lambda payload, raw_input: _ok_observation("thoughts", raw_input),
            system_executor=lambda payload, raw_input: _ok_observation("system", raw_input),
            internet_search_executor=lambda action_input, action_payload=None: _ok_observation(
                "internet_search",
                action_input,
            ),
        )

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

        executor = PlannerToolExecutor(
            command_executor=lambda command: captured_commands.append(command) or "2026-03-10 15:16:17",
            schedule_executor=lambda payload, raw_input: _ok_observation("schedule", raw_input),
            history_executor=lambda payload, raw_input: _ok_observation("history", raw_input),
            history_search_executor=lambda payload, raw_input: _ok_observation("history_search", raw_input),
            thoughts_executor=lambda payload, raw_input: _ok_observation("thoughts", raw_input),
            system_executor=lambda payload, raw_input: _ok_observation("system", raw_input),
            internet_search_executor=lambda action_input, action_payload=None: _ok_observation(
                "internet_search",
                action_input,
            ),
        )

        observation = executor.execute(action_tool="system", action_input="/date")

        self.assertTrue(observation.ok)
        self.assertEqual(["/date"], captured_commands)
        self.assertEqual("2026-03-10 15:16:17", observation.result)


if __name__ == "__main__":
    unittest.main()
