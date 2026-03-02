from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.parsing_utils import _is_direct_http_url
from assistant_app.agent_components.render_helpers import (
    _format_search_results,
    _truncate_text,
    _try_parse_json,
)


def execute_internet_search_planner_action(
    agent: Any,
    *,
    action_input: str,
    fetch_main_text: Callable[[str], Any],
) -> PlannerObservation:
    normalized_input = action_input.strip()
    payload = _try_parse_json(normalized_input)
    if isinstance(payload, dict):
        action = str(payload.get("action") or "").strip().lower()
        if action == "fetch_url":
            return _execute_internet_search_fetch_url_action(
                agent,
                payload,
                raw_input=normalized_input,
                fetch_main_text=fetch_main_text,
            )
        if action:
            return PlannerObservation(
                tool="internet_search",
                input_text=action_input,
                ok=False,
                result="internet_search.action 非法。",
            )
    elif _is_direct_http_url(normalized_input):
        return _execute_internet_search_fetch_url_action(
            agent,
            {"action": "fetch_url", "url": normalized_input},
            raw_input=normalized_input,
            fetch_main_text=fetch_main_text,
        )

    query = normalized_input
    if not query:
        return PlannerObservation(
            tool="internet_search",
            input_text=action_input,
            ok=False,
            result="internet_search 缺少查询词。",
        )
    log_context = {
        "query_preview": _truncate_text(query, 120),
        "query_length": len(query),
        "top_k": agent._internet_search_top_k,
    }
    agent._app_logger.info(
        "planner_tool_internet_search_start",
        extra={"event": "planner_tool_internet_search_start", "context": log_context},
    )
    try:
        search_results = agent.search_provider.search(query, top_k=agent._internet_search_top_k)
    except Exception as exc:  # noqa: BLE001
        agent._app_logger.warning(
            "planner_tool_internet_search_failed",
            extra={
                "event": "planner_tool_internet_search_failed",
                "context": {**log_context, "error": repr(exc)},
            },
        )
        return PlannerObservation(
            tool="internet_search",
            input_text=query,
            ok=False,
            result=f"搜索失败: {exc}",
        )
    if not search_results:
        agent._app_logger.info(
            "planner_tool_internet_search_no_results",
            extra={"event": "planner_tool_internet_search_no_results", "context": log_context},
        )
        return PlannerObservation(
            tool="internet_search",
            input_text=query,
            ok=False,
            result=f"未搜索到与“{query}”相关的结果。",
        )
    formatted = _format_search_results(search_results, top_k=agent._internet_search_top_k)
    agent._app_logger.info(
        "planner_tool_internet_search_done",
        extra={
            "event": "planner_tool_internet_search_done",
            "context": {**log_context, "result_count": len(search_results)},
        },
    )
    return PlannerObservation(tool="internet_search", input_text=query, ok=True, result=formatted)


def _execute_internet_search_fetch_url_action(
    agent: Any,
    payload: dict[str, Any],
    *,
    raw_input: str,
    fetch_main_text: Callable[[str], Any],
) -> PlannerObservation:
    url = str(payload.get("url") or "").strip()
    if not url:
        return PlannerObservation(
            tool="internet_search",
            input_text=raw_input,
            ok=False,
            result="internet_search.fetch_url 缺少 url。",
        )
    if not url.lower().startswith(("http://", "https://")):
        return PlannerObservation(
            tool="internet_search",
            input_text=raw_input,
            ok=False,
            result="internet_search.fetch_url url 非法，需为 http:// 或 https:// 开头。",
        )
    log_context = {
        "url_preview": _truncate_text(url, 120),
        "url_length": len(url),
    }
    agent._app_logger.info(
        "planner_tool_internet_search_fetch_url_start",
        extra={"event": "planner_tool_internet_search_fetch_url_start", "context": log_context},
    )
    try:
        fetch_result = fetch_main_text(url)
    except Exception as exc:  # noqa: BLE001
        agent._app_logger.warning(
            "planner_tool_internet_search_fetch_url_failed",
            extra={
                "event": "planner_tool_internet_search_fetch_url_failed",
                "context": {**log_context, "error": repr(exc)},
            },
        )
        return PlannerObservation(
            tool="internet_search",
            input_text=raw_input,
            ok=False,
            result=f"网页抓取失败: {exc}",
        )

    if not fetch_result.main_text:
        agent._app_logger.warning(
            "planner_tool_internet_search_fetch_url_failed",
            extra={
                "event": "planner_tool_internet_search_fetch_url_failed",
                "context": {**log_context, "error": "empty_main_text"},
            },
        )
        return PlannerObservation(
            tool="internet_search",
            input_text=raw_input,
            ok=False,
            result=f"网页抓取失败: 未提取到正文文本（{fetch_result.url}）。",
        )
    agent._app_logger.info(
        "planner_tool_internet_search_fetch_url_done",
        extra={
            "event": "planner_tool_internet_search_fetch_url_done",
            "context": {
                **log_context,
                "main_text_length": len(fetch_result.main_text),
                "result_url": fetch_result.url,
            },
        },
    )
    result_payload = {
        "url": fetch_result.url,
        "main_text": fetch_result.main_text,
    }
    return PlannerObservation(
        tool="internet_search",
        input_text=raw_input,
        ok=True,
        result=json.dumps(result_payload, ensure_ascii=False, separators=(",", ":")),
    )
