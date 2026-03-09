from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ConfigDict, Field, field_validator

from assistant_app.schemas.base import FrozenModel


class RuntimePlannerActionPayload(FrozenModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        strict=True,
        arbitrary_types_allowed=True,
    )

    tool_name: str = Field(min_length=1)
    arguments: Any

    @field_validator("tool_name")
    @classmethod
    def normalize_tool_name(cls, value: str) -> str:
        return value.strip().lower()


class JsonPlannerToolRoute(FrozenModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        strict=True,
        arbitrary_types_allowed=True,
    )

    tool: str = Field(min_length=1)
    invalid_json_result: str = Field(min_length=1)
    payload_executor: Callable[[dict[str, Any], str], Any]
    typed_payload_executor: Callable[[RuntimePlannerActionPayload, str], Any] | None = None
    legacy_command_prefix: str | None = None
    compat_action: str | None = None

    @field_validator("legacy_command_prefix", "compat_action")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value or None


__all__ = ["JsonPlannerToolRoute", "RuntimePlannerActionPayload"]
