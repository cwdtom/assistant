from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import HttpUrlValue

_BOCHA_FRESHNESS_PRESET_BY_KEY: dict[str, str] = {
    "nolimit": "noLimit",
    "oneyear": "oneYear",
    "onemonth": "oneMonth",
    "oneweek": "oneWeek",
    "oneday": "oneDay",
}
_BOCHA_FRESHNESS_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BOCHA_FRESHNESS_DATE_RANGE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})$")
_BOCHA_FRESHNESS_ERROR = (
    "freshness 非法。支持 noLimit|oneYear|oneMonth|oneWeek|oneDay|YYYY-MM-DD|YYYY-MM-DD..YYYY-MM-DD。"
)


def normalize_bocha_freshness(value: Any, *, field_name: str = "freshness") -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field_name} 不能为空。")
    normalized_preset = _BOCHA_FRESHNESS_PRESET_BY_KEY.get(text.lower())
    if normalized_preset is not None:
        return normalized_preset
    if _BOCHA_FRESHNESS_DATE_PATTERN.fullmatch(text):
        _validate_ymd_date(text)
        return text
    match = _BOCHA_FRESHNESS_DATE_RANGE_PATTERN.fullmatch(text)
    if match is not None:
        start_raw = match.group(1)
        end_raw = match.group(2)
        start = _validate_ymd_date(start_raw)
        end = _validate_ymd_date(end_raw)
        if start > end:
            raise ValueError("freshness 日期区间非法，开始日期不能晚于结束日期。")
        return f"{start_raw}..{end_raw}"
    raise ValueError(_BOCHA_FRESHNESS_ERROR)


def _validate_ymd_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(_BOCHA_FRESHNESS_ERROR) from exc


class BochaSearchRequestReranker(FrozenModel):
    enable: bool
    apiKey: str = Field(min_length=1)
    rerankTopK: int = Field(ge=1)
    rerankModel: str = Field(min_length=1)


class BochaSearchRequestPayload(FrozenModel):
    query: str = Field(min_length=1)
    summary: bool
    count: int = Field(ge=1)
    freshness: str | None = None
    reranker: BochaSearchRequestReranker | None = None

    @field_validator("freshness", mode="before")
    @classmethod
    def normalize_freshness(cls, value: Any) -> str | None:
        return normalize_bocha_freshness(value, field_name="freshness")


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
    "normalize_bocha_freshness",
]
