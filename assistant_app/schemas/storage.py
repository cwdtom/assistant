from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from assistant_app.schemas.base import FrozenModel


class NormalizedTagValue(FrozenModel):
    tag: str = "default"

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: object) -> str:
        if value is None:
            return "default"
        if not isinstance(value, str):
            raise TypeError("tag must be a string")
        normalized = value.strip().lower()
        if not normalized:
            return "default"
        return normalized


class ScheduleDurationValue(FrozenModel):
    duration_minutes: int = Field(ge=1)


class ThoughtContentValue(FrozenModel):
    content: str = Field(min_length=1)


class ThoughtStatusValue(FrozenModel):
    status: Literal["未完成", "完成", "删除"]


__all__ = [
    "NormalizedTagValue",
    "ScheduleDurationValue",
    "ThoughtContentValue",
    "ThoughtStatusValue",
]
