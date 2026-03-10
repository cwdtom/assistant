from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from assistant_app.schemas.base import FrozenModel

_DEFAULT_VALIDATION_CODE = "value_error"
_DEFAULT_VALIDATION_MESSAGE = "validation error"


class ValidationIssue(FrozenModel):
    code: str
    field: str | None = None
    message: str


def first_validation_issue(exc: ValidationError) -> ValidationIssue:
    errors = exc.errors(include_url=False)
    if not errors:
        return ValidationIssue(code=_DEFAULT_VALIDATION_CODE, message=_DEFAULT_VALIDATION_MESSAGE)

    first_error = errors[0]
    return ValidationIssue(
        code=_normalize_issue_code(first_error.get("type")),
        field=_normalize_issue_field(first_error.get("loc")),
        message=_normalize_issue_message(first_error.get("msg")),
    )


def _normalize_issue_code(value: object) -> str:
    text = str(value or _DEFAULT_VALIDATION_CODE).strip()
    return text or _DEFAULT_VALIDATION_CODE


def _normalize_issue_field(value: object) -> str | None:
    if not isinstance(value, (list, tuple)):
        return None
    field_name: str | None = None
    for item in value:
        text = str(item).strip()
        if text and text != "value":
            field_name = text
    return field_name


def _normalize_issue_message(value: Any) -> str:
    text = str(value or _DEFAULT_VALIDATION_MESSAGE).strip()
    if text.startswith("Value error, "):
        text = text.removeprefix("Value error, ").strip()
    return text or _DEFAULT_VALIDATION_MESSAGE


__all__ = ["ValidationIssue", "first_validation_issue"]
