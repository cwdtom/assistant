from __future__ import annotations

import json
import threading
import unittest

from assistant_app.persona import PersonaRewriter


class _FakeLLMClient:
    def __init__(self, response: str = "") -> None:
        self.response = response
        self.calls: list[list[dict[str, str]]] = []

    def reply(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.response


class PersonaRewriterTest(unittest.TestCase):
    def test_rewrite_final_response_returns_original_when_disabled(self) -> None:
        llm = _FakeLLMClient(response="改写结果")
        rewriter = PersonaRewriter(llm_client=llm, persona="温柔助手", enabled=False)

        result = rewriter.rewrite_final_response("原始回答")

        self.assertEqual(result, "原始回答")
        self.assertEqual(llm.calls, [])

    def test_rewrite_reminder_content_uses_llm_when_enabled(self) -> None:
        llm = _FakeLLMClient(response="提醒：今天 10:00 开会，别迟到哦。")
        rewriter = PersonaRewriter(llm_client=llm, persona="贴心管家", enabled=True)

        result = rewriter.rewrite_reminder_content("日程提醒 #1: 开会（提醒时间 2026-02-24 10:00）")

        self.assertEqual(result, "提醒：今天 10:00 开会，别迟到哦。")
        self.assertEqual(len(llm.calls), 1)

    def test_rewrite_falls_back_to_original_on_exception(self) -> None:
        class _ErrorLLMClient:
            def reply(self, messages: list[dict[str, str]]) -> str:  # noqa: ARG002
                raise RuntimeError("boom")

        rewriter = PersonaRewriter(llm_client=_ErrorLLMClient(), persona="严谨项目经理", enabled=True)
        original = "已列出所有待办事项。"

        result = rewriter.rewrite_final_response(original)

        self.assertEqual(result, original)

    def test_rewrite_final_response_adds_human_and_multi_message_guidance(self) -> None:
        llm = _FakeLLMClient(response="已完成。")
        rewriter = PersonaRewriter(llm_client=llm, persona="可靠同事", enabled=True)

        rewriter.rewrite_final_response("任务完成，共 3 项。")

        self.assertEqual(len(llm.calls), 1)
        payload = json.loads(llm.calls[0][1]["content"])
        self.assertIn("语气更像真人同步结果：先说结论，再补充关键细节", payload["requirements"])
        self.assertIn("由你判断是否拆成多条发送；若拆分，请用空行分隔每条内容", payload["requirements"])

    def test_rewrite_reminder_does_not_add_multi_message_guidance(self) -> None:
        llm = _FakeLLMClient(response="提醒：10:00 开会。")
        rewriter = PersonaRewriter(llm_client=llm, persona="可靠同事", enabled=True)

        rewriter.rewrite_reminder_content("日程提醒 #1: 10:00 开会")

        self.assertEqual(len(llm.calls), 1)
        payload = json.loads(llm.calls[0][1]["content"])
        self.assertNotIn("由你判断是否拆成多条发送；若拆分，请用空行分隔每条内容", payload["requirements"])

    def test_rewrite_progress_update_adds_result_only_guidance(self) -> None:
        llm = _FakeLLMClient(response="已完成待办创建。")
        rewriter = PersonaRewriter(llm_client=llm, persona="可靠同事", enabled=True)

        rewriter.rewrite_progress_update("已完成待办创建。")

        self.assertEqual(len(llm.calls), 1)
        payload = json.loads(llm.calls[0][1]["content"])
        self.assertIn("只输出子任务完成文本本体，不添加任何解释、前后缀或额外包装文案", payload["requirements"])
        self.assertIn("可润色语气，但必须完整保留原始子任务名称与完成状态事实", payload["requirements"])

    def test_rewrite_progress_update_does_not_block_on_shared_lock(self) -> None:
        llm = _FakeLLMClient(response="进度消息")
        rewriter = PersonaRewriter(llm_client=llm, persona="可靠同事", enabled=True)
        holder: dict[str, str] = {}

        def _run() -> None:
            holder["result"] = rewriter.rewrite_progress_update("原始进度")

        with rewriter._lock:
            worker = threading.Thread(target=_run)
            worker.start()
            worker.join(timeout=0.3)
            finished_while_locked = not worker.is_alive()
        worker.join(timeout=1.0)

        self.assertTrue(finished_while_locked)
        self.assertEqual(holder.get("result"), "进度消息")
        self.assertEqual(len(llm.calls), 1)


if __name__ == "__main__":
    unittest.main()
