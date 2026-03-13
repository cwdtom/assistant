from __future__ import annotations

import unittest

from assistant_app.schemas.storage import ScheduleBatchCreateInput
from assistant_app.schemas.tool_args import ScheduleViewArgs, ThoughtsUpdateArgs
from assistant_app.schemas.validation_errors import first_validation_issue
from pydantic import ValidationError


class ValidationIssueTest(unittest.TestCase):
    def test_first_validation_issue_extracts_field_and_message(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            ThoughtsUpdateArgs.model_validate({"id": 3, "content": "更新", "status": None})

        issue = first_validation_issue(ctx.exception)

        self.assertEqual(issue.code, "value_error")
        self.assertEqual(issue.field, "status")
        self.assertEqual(issue.message, "status must be one of pending, completed, deleted")

    def test_first_validation_issue_supports_model_level_errors(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            ScheduleViewArgs.model_validate({"view": "day", "anchor": "2026-03"})

        issue = first_validation_issue(ctx.exception)

        self.assertEqual(issue.code, "value_error")
        self.assertIsNone(issue.field)
        self.assertEqual(issue.message, "anchor must match view")

    def test_first_validation_issue_uses_top_level_business_field(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            ScheduleBatchCreateInput.model_validate(
                {
                    "title": "晨会",
                    "event_times": ["2026-03-10 09:00", "bad-time"],
                }
            )

        issue = first_validation_issue(ctx.exception)

        self.assertEqual(issue.field, "event_times")
        self.assertEqual(issue.message, "event_times must match %Y-%m-%d %H:%M")


if __name__ == "__main__":
    unittest.main()
