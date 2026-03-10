from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import ChatTurn


class UserProfileRefreshResult(FrozenModel):
    ok: bool
    reason: str = Field(min_length=1)
    profile_content: str | None = None
    used_turns: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_success_payload(self) -> UserProfileRefreshResult:
        if self.ok and self.profile_content is None:
            raise ValueError("profile_content is required when refresh succeeds")
        if self.ok and self.used_turns < 1:
            raise ValueError("used_turns must be positive when refresh succeeds")
        return self


class UserProfileRefreshPreparation(FrozenModel):
    profile_path: Path
    current_profile: str
    turns: list[ChatTurn] = Field(min_length=1)
