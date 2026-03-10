from __future__ import annotations

import unittest

import assistant_app.schemas as schemas


class SchemasInitTest(unittest.TestCase):
    def test_only_lightweight_public_exports_are_reexported(self) -> None:
        self.assertEqual(
            set(schemas.__all__),
            {
                "ChatMessage",
                "ChatTurn",
                "FrozenModel",
                "HttpUrlValue",
                "RecurringScheduleRule",
                "ReminderDelivery",
                "ScheduleItem",
                "SearchResult",
                "StrictModel",
                "ThoughtItem",
                "WebPageFetchResult",
            },
        )

    def test_high_churn_payload_types_are_not_reexported(self) -> None:
        self.assertFalse(hasattr(schemas, "AskUserArgs"))
        self.assertFalse(hasattr(schemas, "PlannedDecision"))
        self.assertFalse(hasattr(schemas, "JsonPlannerToolRoute"))


if __name__ == "__main__":
    unittest.main()
