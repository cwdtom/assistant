from __future__ import annotations

from typing import Any

from pydantic import ConfigDict, Field

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


class BochaWebPageItem(HttpUrlValue):
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True, strict=True)

    name: str = Field(min_length=1)
    summary: str | list[str | dict[str, Any]] | None = None
    snippet: str = ""


class BochaWebPagesPayload(FrozenModel):
    model_config = ConfigDict(extra="ignore", frozen=True, str_strip_whitespace=True, strict=True)

    value: list[object] = Field(default_factory=list)


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


__all__ = [
    "BochaSearchRequestPayload",
    "BochaSearchRequestReranker",
    "BochaSearchResponsePayload",
    "BochaSearchResponseData",
    "BochaWebPageItem",
    "BochaWebPagesPayload",
]
