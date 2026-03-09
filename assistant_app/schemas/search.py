from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import HttpUrlValue


class BochaSearchRequestReranker(FrozenModel):
    enable: bool
    apiKey: str = Field(min_length=1)
    rerankTopK: int = Field(ge=1)
    rerankModel: str = Field(min_length=1)


class BochaSearchRequestPayload(FrozenModel):
    query: str = Field(min_length=1)
    summary: bool
    count: int = Field(ge=1)
    reranker: BochaSearchRequestReranker | None = None


class BochaSummaryTextPart(FrozenModel):
    model_config = ConfigDict(extra="ignore", frozen=True, from_attributes=True, str_strip_whitespace=True, strict=True)

    text: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_payload(cls, value: Any) -> dict[str, str]:
        if isinstance(value, str):
            return {"text": value}
        if isinstance(value, dict):
            return {"text": str(value.get("text") or "")}
        text = getattr(value, "text", None)
        if text is None:
            return {"text": ""}
        return {"text": str(text)}


class BochaWebPageItem(HttpUrlValue):
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True, strict=True)

    name: str = Field(min_length=1)
    summary_segments: tuple[str, ...] = Field(default_factory=tuple, alias="summary")
    snippet: str = ""

    @field_validator("summary_segments", mode="before")
    @classmethod
    def normalize_summary_segments(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            text = value.strip()
            return (text,) if text else ()
        if not isinstance(value, list):
            return ()
        parts: list[str] = []
        for raw_part in value:
            try:
                part = BochaSummaryTextPart.model_validate(raw_part)
            except ValidationError:
                continue
            if part.text:
                parts.append(part.text)
        return tuple(parts)

    @field_validator("snippet", mode="before")
    @classmethod
    def normalize_snippet(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    def result_snippet(self) -> str:
        summary_text = " ".join(self.summary_segments).strip()
        if summary_text:
            return summary_text
        return self.snippet.strip()


class BochaWebPagesPayload(FrozenModel):
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True, strict=True)

    value: list[object] = Field(default_factory=list)

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> list[object]:
        if isinstance(value, list):
            return value
        return []


class BochaSearchResponseData(FrozenModel):
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True, strict=True)

    webPages: BochaWebPagesPayload | None = None


class BochaSearchResponsePayload(FrozenModel):
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True, strict=True)

    data: BochaSearchResponseData | None = None

    def raw_items(self) -> list[object]:
        if self.data is None or self.data.webPages is None:
            return []
        return list(self.data.webPages.value)

    def items(self) -> list[BochaWebPageItem]:
        items: list[BochaWebPageItem] = []
        for raw_item in self.raw_items():
            try:
                items.append(BochaWebPageItem.model_validate(raw_item))
            except ValidationError:
                continue
        return items


__all__ = [
    "BochaSearchRequestPayload",
    "BochaSearchRequestReranker",
    "BochaSearchResponsePayload",
    "BochaSearchResponseData",
    "BochaSummaryTextPart",
    "BochaWebPageItem",
    "BochaWebPagesPayload",
]
