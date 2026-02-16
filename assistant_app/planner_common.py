from __future__ import annotations

from typing import Any


def normalize_plan_items(payload: dict[str, Any]) -> list[str]:
    raw_plan = payload.get("plan")
    plan_items: list[str] = []
    if isinstance(raw_plan, list):
        for item in raw_plan:
            text = str(item).strip()
            if text:
                plan_items.append(text)
    return plan_items
