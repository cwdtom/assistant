from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import ChatTurn, EVENT_TIME_FORMAT, _validate_datetime_text


class PersonaRewriteRequestPayload(FrozenModel):
    scene: Literal["final_response", "reminder", "progress_update"]
    persona: str = Field(min_length=1)
    text: str = Field(min_length=1)
    requirements: list[str] = Field(min_length=1)

    @field_validator("requirements")
    @classmethod
    def validate_requirements(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("requirements must contain at least one item")
        return normalized


class UserProfileRefreshPromptTime(FrozenModel):
    now: str
    window_days: int = Field(ge=1)

    @field_validator("now")
    @classmethod
    def validate_now(cls, value: str) -> str:
        return _validate_datetime_text(value, field_name="now", formats=(EVENT_TIME_FORMAT,))


class UserProfileRefreshPromptLimits(FrozenModel):
    max_turns: int = Field(ge=1)
    actual_turns: int = Field(ge=1)


class UserProfileRefreshPromptPayload(FrozenModel):
    task: Literal["refresh_user_profile"]
    time: UserProfileRefreshPromptTime
    limits: UserProfileRefreshPromptLimits
    current_user_profile: str
    chat_turns: list[ChatTurn] = Field(min_length=1)
    output_requirements: list[str] = Field(min_length=1)

    @field_validator("output_requirements")
    @classmethod
    def validate_output_requirements(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("output_requirements must contain at least one item")
        return normalized


__all__ = [
    "PersonaRewriteRequestPayload",
    "UserProfileRefreshPromptLimits",
    "UserProfileRefreshPromptPayload",
    "UserProfileRefreshPromptTime",
]
