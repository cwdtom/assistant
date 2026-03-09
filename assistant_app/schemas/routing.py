from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import ConfigDict, Field, field_validator

from assistant_app.schemas.base import FrozenModel


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
    legacy_command_prefix: str | None = None
    compat_action: str | None = None

    @field_validator("legacy_command_prefix", "compat_action")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value or None


__all__ = ["JsonPlannerToolRoute"]
