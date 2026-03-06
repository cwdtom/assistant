from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, strict=True)


class FrozenModel(StrictModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True, strict=True)
