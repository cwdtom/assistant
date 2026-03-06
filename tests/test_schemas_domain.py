from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from assistant_app.db import AssistantDB
from assistant_app.schemas.domain import ScheduleItem, SearchResult, ThoughtItem, WebPageFetchResult
from assistant_app.search import _extract_bing_results, _extract_bocha_results
from pydantic import ValidationError


class DomainSchemaTest(unittest.TestCase):
    def test_schedule_item_normalizes_tag_and_validates_recurrence_fields(self) -> None:
        item = ScheduleItem.model_validate(
            {
                "id": 1,
                "title": "项目同步",
                "tag": " Work ",
                "event_time": "2026-03-06 10:00",
                "duration_minutes": 30,
                "created_at": "2026-03-06 09:00:00",
            }
        )
        self.assertEqual(item.tag, "work")

        with self.assertRaises(ValidationError):
            ScheduleItem.model_validate(
                {
                    "id": 1,
                    "title": "项目同步",
                    "tag": "work",
                    "event_time": "2026-03-06 10:00",
                    "duration_minutes": 30,
                    "created_at": "2026-03-06 09:00:00",
                    "repeat_interval_minutes": 60,
                }
            )

        with self.assertRaises(ValidationError):
            ScheduleItem.model_validate(
                {
                    "id": 1,
                    "title": "项目同步",
                    "tag": "work",
                    "event_time": "2026-03-06 10:00:30",
                    "duration_minutes": 30,
                    "created_at": "2026-03-06 09:00:00",
                }
            )

    def test_thought_item_rejects_invalid_status(self) -> None:
        with self.assertRaises(ValidationError):
            ThoughtItem.model_validate(
                {
                    "id": 1,
                    "content": "记得复盘",
                    "status": "处理中",
                    "created_at": "2026-03-06 09:00:00",
                    "updated_at": "2026-03-06 09:01:00",
                }
            )

    def test_search_models_reject_invalid_http_url(self) -> None:
        with self.assertRaises(ValidationError):
            SearchResult.model_validate({"title": "Title", "snippet": "Snippet", "url": "https://"})

        with self.assertRaises(ValidationError):
            WebPageFetchResult.model_validate({"url": "https://", "main_text": "body"})


class DomainRowValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "assistant_test.db")
        self.db = AssistantDB(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_base_schedules_validates_db_rows_with_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO schedules (title, tag, event_time, duration_minutes, remind_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("损坏数据", "work", "2026-03-06 10:00", 30, None, "bad-created-at"),
            )
            conn.commit()

        with self.assertRaises(ValidationError):
            self.db.list_base_schedules()


class SearchExtractionValidationTest(unittest.TestCase):
    def test_extract_bing_results_skips_schema_invalid_url(self) -> None:
        html = '''
        <li class="b_algo">
          <h2><a href="https://">Bad Result</a></h2>
          <div class="b_caption"><p>bad</p></div>
        </li>
        <li class="b_algo">
          <h2><a href="https://example.com/good">Good Result</a></h2>
          <div class="b_caption"><p>good</p></div>
        </li>
        '''

        results = _extract_bing_results(html, top_k=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://example.com/good")

    def test_extract_bocha_results_skips_schema_invalid_url(self) -> None:
        payload = {
            "data": {
                "webPages": {
                    "value": [
                        {"name": "Bad", "url": "https://", "snippet": "bad"},
                        {"name": "Good", "url": "https://example.com/good", "snippet": "good"},
                    ]
                }
            }
        }

        results = _extract_bocha_results(payload)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Good")
