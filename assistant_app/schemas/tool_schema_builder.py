from __future__ import annotations

from copy import deepcopy
from typing import Any

from assistant_app.schemas.base import FrozenModel


def _normalize_nullable_schema(schema: dict[str, Any]) -> dict[str, Any]:
    any_of = schema.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2:
        return schema
    null_schema = next(
        (item for item in any_of if isinstance(item, dict) and item.get("type") == "null" and len(item) == 1),
        None,
    )
    value_schema = next((item for item in any_of if item is not null_schema and isinstance(item, dict)), None)
    if null_schema is None or value_schema is None:
        return schema
    value_type = value_schema.get("type")
    if not isinstance(value_type, str):
        return schema
    normalized = deepcopy(value_schema)
    normalized["type"] = [value_type, "null"]
    return normalized


def _cleanup_json_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_cleanup_json_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {"title", "default"}:
            continue
        cleaned[key] = _cleanup_json_schema(value)
    return _normalize_nullable_schema(cleaned)


def build_function_tool_schema(
    *,
    name: str,
    description: str,
    arguments_model: type[FrozenModel],
    exclude_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema = _cleanup_json_schema(arguments_model.model_json_schema())
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    else:
        properties = deepcopy(properties)
    required = schema.get("required")
    required_fields = [item for item in required if isinstance(item, str)] if isinstance(required, list) else []
    for field_name in exclude_fields:
        properties.pop(field_name, None)
        required_fields = [item for item in required_fields if item != field_name]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required_fields,
                "additionalProperties": False,
            },
        },
    }


__all__ = ["build_function_tool_schema"]
