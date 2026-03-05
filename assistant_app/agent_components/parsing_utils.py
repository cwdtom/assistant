from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

SCHEDULE_EVENT_PREFIX_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+(.+)$")
SCHEDULE_INTERVAL_OPTION_PATTERN = re.compile(r"(^|\s)--interval\s+(\d+)")
SCHEDULE_TIMES_OPTION_PATTERN = re.compile(r"(^|\s)--times\s+(-?\d+)")
SCHEDULE_DURATION_OPTION_PATTERN = re.compile(r"(^|\s)--duration\s+(\d+)")
SCHEDULE_REMIND_OPTION_PATTERN = re.compile(r"(^|\s)--remind\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})")
SCHEDULE_REMIND_START_OPTION_PATTERN = re.compile(
    r"(^|\s)--remind-start\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})"
)
TAG_OPTION_PATTERN = re.compile(r"(^|\s)--tag\s+(\S+)")
HISTORY_LIMIT_OPTION_PATTERN = re.compile(r"(^|\s)--limit\s+(\d+)")
THOUGHTS_STATUS_OPTION_PATTERN = re.compile(r"(^|\s)--status\s+(\S+)")
SCHEDULE_VIEW_NAMES = ("day", "week", "month")
THOUGHT_STATUS_VALUES = ("未完成", "完成", "删除")
DEFAULT_HISTORY_LIST_LIMIT = 20
MAX_HISTORY_LIST_LIMIT = 200
DEFAULT_SCHEDULE_MAX_WINDOW_DAYS = 31
_INVALID_OPTION_VALUE = object()


def _parse_positive_int(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    value = int(raw)
    if value <= 0:
        return None
    return value


def _parse_history_list_limit(command: str) -> int | None:
    if command == "/history list":
        return DEFAULT_HISTORY_LIST_LIMIT
    raw = command.removeprefix("/history list").strip()
    if not raw:
        return DEFAULT_HISTORY_LIST_LIMIT
    parts = raw.split()
    if len(parts) != 2 or parts[0] != "--limit":
        return None
    parsed = _parse_positive_int(parts[1])
    if parsed is None:
        return None
    return min(parsed, MAX_HISTORY_LIST_LIMIT)


def _parse_history_search_input(raw: str) -> tuple[str, int] | None:
    text = raw.strip()
    if not text:
        return None
    working = text
    limit = DEFAULT_HISTORY_LIST_LIMIT
    limit_match = HISTORY_LIMIT_OPTION_PATTERN.search(working)
    if limit_match:
        parsed_limit = _parse_positive_int(limit_match.group(2))
        if parsed_limit is None:
            return None
        limit = min(parsed_limit, MAX_HISTORY_LIST_LIMIT)
        working = _remove_option_span(working, limit_match.span())
    keyword = re.sub(r"\s+", " ", working).strip()
    if not keyword:
        return None
    return keyword, limit


def _parse_schedule_add_input(
    raw: str,
) -> tuple[str, str, str, int, str | None, int | None, int, str | None] | None:
    parsed = _parse_schedule_input(raw, default_tag="default", default_duration_minutes=60)
    if parsed is None:
        return None
    (
        event_time,
        title,
        tag,
        _has_tag,
        duration_minutes,
        remind_at,
        _has_remind,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
        has_repeat_remind_start_time,
    ) = parsed
    if duration_minutes is None:
        return None
    if repeat_interval_minutes is None and repeat_times != 1:
        return None
    if repeat_interval_minutes is not None and repeat_times == 1:
        return None
    if has_repeat_remind_start_time and repeat_interval_minutes is None:
        return None
    final_tag = tag or "default"
    return (
        event_time,
        title,
        final_tag,
        duration_minutes,
        remind_at,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
    )


def _parse_schedule_update_input(
    raw: str,
) -> tuple[int, str, str, str | None, bool, int | None, str | None, bool, int | None, int, str | None, bool] | None:
    parts = raw.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    schedule_id = _parse_positive_int(parts[0])
    if schedule_id is None:
        return None
    parsed = _parse_schedule_input(parts[1], default_tag=None, default_duration_minutes=None)
    if parsed is None:
        return None
    (
        event_time,
        title,
        tag,
        has_tag,
        duration_minutes,
        remind_at,
        has_remind,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
        has_repeat_remind_start_time,
    ) = parsed
    if repeat_interval_minutes is None and repeat_times != 1:
        return None
    if repeat_interval_minutes is not None and repeat_times == 1:
        return None
    if has_repeat_remind_start_time and repeat_interval_minutes is None:
        return None
    return (
        schedule_id,
        event_time,
        title,
        tag,
        has_tag,
        duration_minutes,
        remind_at,
        has_remind,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
        has_repeat_remind_start_time,
    )


def _parse_schedule_input(
    raw: str,
    *,
    default_tag: str | None,
    default_duration_minutes: int | None,
) -> tuple[str, str, str | None, bool, int | None, str | None, bool, int | None, int, str | None, bool] | None:
    text = raw.strip()
    if not text:
        return None
    matched = SCHEDULE_EVENT_PREFIX_PATTERN.match(text)
    if matched is None:
        return None
    event_time = matched.group(1)
    if not _is_valid_datetime_text(event_time):
        return None

    working = matched.group(2).strip()
    if not working:
        return None

    duration_minutes: int | None = default_duration_minutes
    tag: str | None = default_tag
    has_tag = False
    remind_at: str | None = None
    has_remind = False
    repeat_interval_minutes: int | None = None
    repeat_times = 1
    has_repeat_times = False
    repeat_remind_start_time: str | None = None
    has_repeat_remind_start_time = False

    tag_match = TAG_OPTION_PATTERN.search(working)
    if tag_match:
        parsed_tag = _sanitize_tag(tag_match.group(2))
        if not parsed_tag:
            return None
        tag = parsed_tag
        has_tag = True
        working = _remove_option_span(working, tag_match.span())

    interval_match = SCHEDULE_INTERVAL_OPTION_PATTERN.search(working)
    if interval_match:
        parsed_interval = _normalize_schedule_interval_minutes_value(interval_match.group(2))
        if parsed_interval is None:
            return None
        repeat_interval_minutes = parsed_interval
        working = _remove_option_span(working, interval_match.span())

    duration_match = SCHEDULE_DURATION_OPTION_PATTERN.search(working)
    if duration_match:
        parsed_duration = _normalize_schedule_duration_minutes_value(duration_match.group(2))
        if parsed_duration is None:
            return None
        duration_minutes = parsed_duration
        working = _remove_option_span(working, duration_match.span())

    remind_match = SCHEDULE_REMIND_OPTION_PATTERN.search(working)
    if remind_match:
        parsed_remind = _normalize_datetime_text(remind_match.group(2))
        if not parsed_remind:
            return None
        remind_at = parsed_remind
        has_remind = True
        working = _remove_option_span(working, remind_match.span())

    times_match = SCHEDULE_TIMES_OPTION_PATTERN.search(working)
    if times_match:
        parsed_times = _normalize_schedule_repeat_times_value(times_match.group(2))
        if parsed_times is None:
            return None
        repeat_times = parsed_times
        has_repeat_times = True
        working = _remove_option_span(working, times_match.span())

    remind_start_match = SCHEDULE_REMIND_START_OPTION_PATTERN.search(working)
    if remind_start_match:
        parsed_remind_start = _normalize_datetime_text(remind_start_match.group(2))
        if not parsed_remind_start:
            return None
        repeat_remind_start_time = parsed_remind_start
        has_repeat_remind_start_time = True
        working = _remove_option_span(working, remind_start_match.span())

    if repeat_interval_minutes is not None and not has_repeat_times:
        repeat_times = -1

    title = re.sub(r"\s+", " ", working).strip()
    if not title:
        return None
    if re.search(r"(^|\s)--(tag|duration|interval|times|remind|remind-start)\b", title):
        return None
    return (
        event_time,
        title,
        tag,
        has_tag,
        duration_minutes,
        remind_at,
        has_remind,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
        has_repeat_remind_start_time,
    )


def _parse_schedule_view_input(raw: str) -> tuple[str, str | None] | None:
    text = raw.strip()
    if not text:
        return None
    parts = text.split(maxsplit=1)
    view_name = _normalize_schedule_view_value(parts[0])
    if view_name is None:
        return None
    if len(parts) == 1:
        return view_name, None
    anchor = _normalize_schedule_view_anchor(view_name=view_name, value=parts[1].strip())
    if anchor is None:
        return None
    return view_name, anchor


def _parse_schedule_list_tag_input(raw: str) -> str | None | object:
    text = raw.strip()
    if not text:
        return None
    option_match = re.fullmatch(r"--tag\s+(\S+)", text)
    if option_match is None:
        return _INVALID_OPTION_VALUE
    tag = _sanitize_tag(option_match.group(1))
    if tag is None:
        return _INVALID_OPTION_VALUE
    return tag


def _parse_schedule_view_command_input(raw: str) -> tuple[str, str | None, str | None] | None:
    text = raw.strip()
    if not text:
        return None

    working = text
    tag: str | None = None

    tag_match = TAG_OPTION_PATTERN.search(working)
    if tag_match:
        parsed_tag = _sanitize_tag(tag_match.group(2))
        if not parsed_tag:
            return None
        tag = parsed_tag
        working = _remove_option_span(working, tag_match.span())

    if re.search(r"(^|\s)--tag\b", working):
        return None

    view_parsed = _parse_schedule_view_input(working.strip())
    if view_parsed is None:
        return None
    view_name, anchor = view_parsed
    return view_name, anchor, tag


def _parse_schedule_repeat_toggle_input(raw: str) -> tuple[int, bool] | None:
    parts = raw.strip().split()
    if len(parts) != 2:
        return None
    schedule_id = _parse_positive_int(parts[0])
    if schedule_id is None:
        return None
    status = parts[1].strip().lower()
    if status == "on":
        return schedule_id, True
    if status == "off":
        return schedule_id, False
    return None


def _parse_thoughts_list_status_input(raw: str) -> str | None | object:
    text = raw.strip()
    if not text:
        return None
    option_match = re.fullmatch(r"--status\s+(\S+)", text)
    if option_match is None:
        return _INVALID_OPTION_VALUE
    status = _normalize_thought_status_value(option_match.group(1))
    if status is None:
        return _INVALID_OPTION_VALUE
    return status


def _parse_thoughts_update_input(raw: str) -> tuple[int, str, str | None, bool] | None:
    parts = raw.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    thought_id = _parse_positive_int(parts[0])
    if thought_id is None:
        return None

    working = parts[1].strip()
    if not working:
        return None

    has_status = False
    status: str | None = None
    status_match = THOUGHTS_STATUS_OPTION_PATTERN.search(working)
    if status_match:
        has_status = True
        status = _normalize_thought_status_value(status_match.group(2))
        if status is None:
            return None
        working = _remove_option_span(working, status_match.span())
    if re.search(r"(^|\s)--status\b", working):
        return None

    content = re.sub(r"\s+", " ", working).strip()
    if not content:
        return None
    return thought_id, content, status, has_status


def _sanitize_tag(tag: str | None) -> str | None:
    if tag is None:
        return None
    normalized = tag.strip().lower()
    if not normalized:
        return None
    normalized = normalized.lstrip("#")
    if not normalized:
        return None
    return re.sub(r"\s+", "-", normalized)


def _normalize_optional_datetime_value(value: Any, *, key_present: bool) -> str | None | object:
    if not key_present:
        return None
    if value is None:
        return None
    normalized = _normalize_datetime_text(str(value))
    if normalized is None:
        return _INVALID_OPTION_VALUE
    return normalized


def _normalize_positive_int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    if value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    if parsed <= 0:
        return None
    return parsed


def _normalize_schedule_tag_value(value: Any) -> str | None:
    if value is None:
        return None
    return _sanitize_tag(str(value))


def _normalize_schedule_interval_minutes_value(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        return parsed if parsed >= 1 else None
    text = str(value).strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    if parsed < 1:
        return None
    return parsed


def _normalize_schedule_view_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in SCHEDULE_VIEW_NAMES:
        return text
    return None


def _normalize_schedule_repeat_times_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value == -1:
            return -1
        return value if value >= 2 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        if parsed == -1:
            return -1
        return parsed if parsed >= 2 else None
    text = str(value).strip()
    if not re.fullmatch(r"-?\d+", text):
        return None
    parsed = int(text)
    if parsed == -1:
        return -1
    if parsed < 2:
        return None
    return parsed


def _normalize_schedule_duration_minutes_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        return parsed if parsed >= 1 else None
    text = str(value).strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    if parsed < 1:
        return None
    return parsed


def _normalize_thought_status_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in THOUGHT_STATUS_VALUES:
        return text
    return None


def _normalize_schedule_view_anchor(*, view_name: str, value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if view_name in {"day", "week"}:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return parsed.strftime("%Y-%m-%d")
    if view_name == "month":
        try:
            parsed = datetime.strptime(text, "%Y-%m")
        except ValueError:
            return None
        return parsed.strftime("%Y-%m")
    return None


def _normalize_datetime_text(value: str) -> str | None:
    text = re.sub(r"\s+", " ", value).strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d %H:%M")


def _filter_schedules_by_calendar_view(
    schedules: list[Any],
    *,
    view_name: str,
    anchor: str | None,
    now: datetime | None = None,
) -> list[Any]:
    if view_name not in SCHEDULE_VIEW_NAMES:
        return schedules

    start, end = _resolve_schedule_view_window(view_name=view_name, anchor=anchor, now=now)

    filtered: list[Any] = []
    for item in schedules:
        event_time = _parse_due_datetime(item.event_time)
        if event_time is None:
            continue
        if start <= event_time < end:
            filtered.append(item)
    return filtered


def _parse_due_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _default_schedule_list_window(
    now: datetime | None = None,
    *,
    window_days: int = DEFAULT_SCHEDULE_MAX_WINDOW_DAYS,
) -> tuple[datetime, datetime]:
    current = now or datetime.now()
    start = datetime.combine(current.date() - timedelta(days=2), datetime.min.time())
    end = start + timedelta(days=max(window_days, 1))
    return start, end


def _resolve_schedule_view_window(
    *,
    view_name: str,
    anchor: str | None,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    current = now or datetime.now()
    if anchor:
        if view_name == "month":
            anchor_time = datetime.strptime(anchor, "%Y-%m")
        else:
            anchor_time = datetime.strptime(anchor, "%Y-%m-%d")
    else:
        anchor_time = current

    if view_name == "day":
        start = datetime.combine(anchor_time.date(), datetime.min.time())
        end = start + timedelta(days=1)
        return start, end
    if view_name == "week":
        week_start_date = anchor_time.date() - timedelta(days=anchor_time.weekday())
        start = datetime.combine(week_start_date, datetime.min.time())
        end = start + timedelta(days=7)
        return start, end
    month_start = datetime(anchor_time.year, anchor_time.month, 1)
    if anchor_time.month == 12:
        month_end = datetime(anchor_time.year + 1, 1, 1)
    else:
        month_end = datetime(anchor_time.year, anchor_time.month + 1, 1)
    return month_start, month_end


def _now_time_text() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _is_valid_datetime_text(value: str) -> bool:
    return _normalize_datetime_text(value) is not None


def _remove_option_span(text: str, span: tuple[int, int]) -> str:
    start, end = span
    return (text[:start] + " " + text[end:]).strip()


def _is_direct_http_url(text: str) -> bool:
    candidate = text.strip()
    if not candidate:
        return False
    if " " in candidate:
        return False
    return candidate.lower().startswith(("http://", "https://"))


def _is_same_question_text(previous: str | None, current: str) -> bool:
    if previous is None:
        return False
    a = _normalize_question_text(previous)
    b = _normalize_question_text(current)
    if not a or not b:
        return False
    if a == b:
        return True
    return a in b or b in a


def _normalize_question_text(text: str) -> str:
    normalized = text.strip().lower()
    normalized = re.sub(r"[\s，。！？；：、,.!?;:]+", "", normalized)
    return normalized
