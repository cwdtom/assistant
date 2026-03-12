from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.parsing_utils import _is_direct_http_url
from assistant_app.agent_components.render_helpers import (
    _format_search_results,
    _truncate_text,
    _try_parse_json,
)
from assistant_app.schemas.domain import HttpUrlValue
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import (
    InternetSearchArgs,
    InternetSearchFetchUrlArgs,
    coerce_internet_search_action_payload,
)
from assistant_app.schemas.validation_errors import first_validation_issue


def execute_internet_search_planner_action(
    agent: Any,
    *,
    action_input: str,
    action_payload: RuntimePlannerActionPayload | None = None,
    fetch_main_text: Callable[[str], Any],
) -> PlannerObservation:
    normalized_input = action_input.strip()
    if action_payload is not None:
        typed_observation = _execute_typed_internet_search_action(
            agent,
            action_payload=action_payload,
            raw_input=normalized_input,
            fetch_main_text=fetch_main_text,
        )
        if typed_observation is not None:
            return typed_observation
    payload = _try_parse_json(normalized_input)
    if isinstance(payload, dict):
        try:
            runtime_payload = coerce_internet_search_action_payload(payload)
        except ValidationError as exc:
            return PlannerObservation(
                tool="internet_search",
                input_text=action_input,
                ok=False,
                result=_internet_search_validation_error_text(payload=payload, exc=exc),
            )
        except ValueError as exc:
            return PlannerObservation(
                tool="internet_search",
                input_text=action_input,
                ok=False,
                result=str(exc).strip() or "internet_search.action 非法。",
            )
        typed_observation = _execute_typed_internet_search_action(
            agent,
            action_payload=runtime_payload,
            raw_input=normalized_input,
            fetch_main_text=fetch_main_text,
        )
        if typed_observation is not None:
            return typed_observation
        return PlannerObservation(
            tool="internet_search",
            input_text=action_input,
            ok=False,
            result="internet_search.action 非法。",
        )
    elif _is_direct_http_url(normalized_input):
        try:
            runtime_payload = RuntimePlannerActionPayload(
                tool_name="internet_search_fetch_url",
                arguments=InternetSearchFetchUrlArgs(url=normalized_input),
            )
        except ValidationError:
            return PlannerObservation(
                tool="internet_search",
                input_text=normalized_input,
                ok=False,
                result="internet_search.fetch_url url 非法，需为 http:// 或 https:// 开头。",
            )
        typed_observation = _execute_typed_internet_search_action(
            agent,
            action_payload=runtime_payload,
            raw_input=normalized_input,
            fetch_main_text=fetch_main_text,
        )
        if typed_observation is not None:
            return typed_observation

    return _execute_internet_search_query_action(
        agent,
        query=normalized_input,
        raw_input=action_input,
    )


def _execute_typed_internet_search_action(
    agent: Any,
    *,
    action_payload: RuntimePlannerActionPayload,
    raw_input: str,
    fetch_main_text: Callable[[str], Any],
) -> PlannerObservation | None:
    tool_name = action_payload.tool_name
    arguments = action_payload.arguments
    if tool_name == "internet_search_tool" and isinstance(arguments, InternetSearchArgs):
        return _execute_internet_search_query_action(
            agent,
            query=arguments.query,
            freshness=arguments.freshness,
            raw_input=raw_input,
        )
    if tool_name == "internet_search_fetch_url" and isinstance(arguments, InternetSearchFetchUrlArgs):
        return _execute_internet_search_fetch_url(
            agent,
            url=arguments.url,
            raw_input=raw_input,
            fetch_main_text=fetch_main_text,
        )
    return None


def _execute_internet_search_query_action(
    agent: Any,
    *,
    query: str,
    freshness: str | None = None,
    raw_input: str,
) -> PlannerObservation:
    normalized_query = query.strip()
    if not normalized_query:
        return PlannerObservation(
            tool="internet_search",
            input_text=raw_input,
            ok=False,
            result="internet_search 缺少查询词。",
        )
    log_context = {
        "query_preview": _truncate_text(normalized_query, 120),
        "query_length": len(normalized_query),
        "top_k": agent._internet_search_top_k,
        "freshness": freshness,
    }
    agent._app_logger.info(
        "planner_tool_internet_search_start",
        extra={"event": "planner_tool_internet_search_start", "context": log_context},
    )
    try:
        search_results = agent.search_provider.search(
            normalized_query,
            top_k=agent._internet_search_top_k,
            freshness=freshness,
        )
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
            input_text=normalized_query,
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
            input_text=normalized_query,
            ok=True,
            result=f"未搜索到与“{normalized_query}”相关的结果。",
        )
    formatted = _format_search_results(search_results, top_k=agent._internet_search_top_k)
    agent._app_logger.info(
        "planner_tool_internet_search_done",
        extra={
            "event": "planner_tool_internet_search_done",
            "context": {**log_context, "result_count": len(search_results)},
        },
    )
    return PlannerObservation(tool="internet_search", input_text=normalized_query, ok=True, result=formatted)


def _execute_internet_search_fetch_url(
    agent: Any,
    *,
    url: str,
    raw_input: str,
    fetch_main_text: Callable[[str], Any],
) -> PlannerObservation:
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


def _internet_search_validation_error_text(*, payload: dict[str, Any], exc: ValidationError) -> str:
    action = str(payload.get("action") or "").strip().lower()
    issue = first_validation_issue(exc)
    if action == "search":
        if issue.field == "query":
            return "internet_search.search query 不能为空。"
        if issue.field == "freshness":
            return (
                "internet_search.search freshness 非法。支持 "
                "noLimit|oneYear|oneMonth|oneWeek|oneDay|YYYY-MM-DD|YYYY-MM-DD..YYYY-MM-DD。"
            )
        return "internet_search.action 非法。"
    if action == "fetch_url":
        if issue.field == "url":
            raw_url = str(payload.get("url") or "").strip()
            if not raw_url:
                return "internet_search.fetch_url 缺少 url。"
            try:
                HttpUrlValue.model_validate({"url": raw_url})
            except ValidationError:
                return "internet_search.fetch_url url 非法，需为 http:// 或 https:// 开头。"
            return "internet_search.fetch_url url 非法，需为 http:// 或 https:// 开头。"
        return "internet_search.action 非法。"
    return "internet_search.action 非法。"
