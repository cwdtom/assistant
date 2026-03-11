from __future__ import annotations

import unittest

from assistant_app.schemas.llm_payloads import PersonaRewriteRequestPayload
from pydantic import ValidationError


class LLMPayloadSchemaTest(unittest.TestCase):
    def test_persona_rewrite_request_requires_non_empty_requirements(self) -> None:
        with self.assertRaises(ValidationError):
            PersonaRewriteRequestPayload.model_validate(
                {
                    "scene": "final_response",
                    "persona": "可靠同事",
                    "text": "任务完成",
                    "requirements": ["", "   "],
                }
            )

if __name__ == "__main__":
    unittest.main()
