from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from assistant_app.schemas.base import FrozenModel


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

__all__ = [
    "PersonaRewriteRequestPayload",
]
