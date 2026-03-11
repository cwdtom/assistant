from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.render_helpers import _truncate_text
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import (
    UserProfileGetArgs,
    UserProfileOverwriteArgs,
    coerce_user_profile_action_payload,
)
from assistant_app.schemas.validation_errors import first_validation_issue


def execute_user_profile_system_action(
    agent: Any,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    *,
    raw_input: str,
    source: str = "planner",
) -> PlannerObservation:
    tool_name = _payload_tool_name(payload)
    resolved_path = _resolved_user_profile_path(agent)
    log_context = {
        "tool_name": tool_name,
        "source": source,
        "path": str(resolved_path) if resolved_path is not None else "",
        "raw_input_preview": _truncate_text(raw_input, 120),
    }
    agent._app_logger.info(
        "planner_tool_user_profile_start",
        extra={"event": "planner_tool_user_profile_start", "context": log_context},
    )
    try:
        runtime_payload = _coerce_user_profile_runtime_payload(payload)
        if runtime_payload is None:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                path=resolved_path,
                result="user_profile.action 非法。",
                ok=False,
            )
        typed_observation = _execute_typed_user_profile_action(
            agent=agent,
            payload=runtime_payload,
            raw_input=raw_input,
            source=source,
        )
        if typed_observation is not None:
            return typed_observation
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            path=resolved_path,
            result="user_profile.action 非法。",
            ok=False,
        )
    except ValidationError as exc:
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            path=resolved_path,
            result=_user_profile_validation_error_text(exc),
            ok=False,
        )
    except ValueError as exc:
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            path=resolved_path,
            result=str(exc).strip() or "user_profile.action 非法。",
            ok=False,
        )
    except Exception as exc:  # noqa: BLE001
        agent._app_logger.warning(
            "planner_tool_user_profile_failed",
            extra={
                "event": "planner_tool_user_profile_failed",
                "context": {**log_context, "error": repr(exc)},
            },
        )
        return PlannerObservation(
            tool="user_profile",
            input_text=raw_input,
            ok=False,
            result=f"user_profile 工具执行失败: {exc}",
        )


def _coerce_user_profile_runtime_payload(
    payload: dict[str, Any] | RuntimePlannerActionPayload,
) -> RuntimePlannerActionPayload | None:
    if isinstance(payload, RuntimePlannerActionPayload):
        return payload
    return coerce_user_profile_action_payload(payload)


def _execute_typed_user_profile_action(
    *,
    agent: Any,
    payload: RuntimePlannerActionPayload,
    raw_input: str,
    source: str,
) -> PlannerObservation | None:
    path = _required_user_profile_path(agent)
    tool_name = payload.tool_name
    arguments = payload.arguments

    if tool_name == "user_profile_get" and isinstance(arguments, UserProfileGetArgs):
        content = _read_user_profile_text(path)
        if content is None:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                path=path,
                result="当前 user_profile 为空。",
                ok=True,
                content_length=0,
            )
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            path=path,
            result=f"当前 user_profile 内容:\n{content}",
            ok=True,
            content_length=len(content),
        )

    if tool_name == "user_profile_overwrite" and isinstance(arguments, UserProfileOverwriteArgs):
        _validate_user_profile_content_length(agent=agent, content=arguments.content)
        created_file = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments.content, encoding="utf-8")
        reloaded = agent.reload_user_profile()
        if arguments.content.strip() and not reloaded:
            raise RuntimeError("user_profile 写入后重新加载失败。")
        result = (
            "已清空 user_profile。"
            if not arguments.content
            else f"已覆盖 user_profile，共 {len(arguments.content)} 字符。"
        )
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            path=path,
            result=result,
            ok=True,
            content_length=len(arguments.content),
            created_file=created_file,
        )

    return None


def _resolved_user_profile_path(agent: Any) -> Path | None:
    raw_path = str(getattr(agent._planner_session, "_user_profile_path", "") or "").strip()
    if not raw_path:
        return None
    return Path(raw_path)


def _required_user_profile_path(agent: Any) -> Path:
    path = _resolved_user_profile_path(agent)
    if path is None:
        raise ValueError("user_profile.path 未配置。")
    return path


def _read_user_profile_text(path: Path) -> str | None:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"读取 user_profile 失败: {exc}") from exc
    if not content:
        return None
    return content


def _validate_user_profile_content_length(*, agent: Any, content: str) -> None:
    normalized = content.strip()
    max_chars = int(getattr(agent._planner_session, "_user_profile_max_chars", 1) or 1)
    if len(normalized) > max_chars:
        raise ValueError(
            "USER_PROFILE_PATH 对应文件内容超长："
            f"{len(normalized)} 字符，最大允许 {max_chars} 字符。"
        )


def _done_observation(
    *,
    agent: Any,
    tool_name: str,
    source: str,
    raw_input: str,
    path: Path | None,
    result: str,
    ok: bool,
    content_length: int | None = None,
    created_file: bool | None = None,
) -> PlannerObservation:
    observation = PlannerObservation(
        tool="user_profile",
        input_text=raw_input,
        ok=ok,
        result=result,
    )
    log_context: dict[str, Any] = {
        "tool_name": tool_name,
        "source": source,
        "path": str(path) if path is not None else "",
        "ok": observation.ok,
    }
    if content_length is not None:
        log_context["content_length"] = content_length
    if created_file is not None:
        log_context["created_file"] = created_file
    agent._app_logger.info(
        "planner_tool_user_profile_done",
        extra={"event": "planner_tool_user_profile_done", "context": log_context},
    )
    return observation


def _payload_tool_name(payload: dict[str, Any] | RuntimePlannerActionPayload) -> str:
    if isinstance(payload, RuntimePlannerActionPayload):
        return payload.tool_name
    action = str(payload.get("action") or "").strip().lower()
    return {
        "get": "user_profile_get",
        "overwrite": "user_profile_overwrite",
    }.get(action, "user_profile")


def _user_profile_validation_error_text(exc: ValidationError) -> str:
    issue = first_validation_issue(exc)
    if issue.field == "content":
        return "user_profile.overwrite content 缺失。"
    return issue.message or "user_profile 工具参数无效。"
