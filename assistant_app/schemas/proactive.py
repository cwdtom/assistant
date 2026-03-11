from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import ChatTurn, ScheduleItem, SearchResult
from assistant_app.schemas.normalization import (
    EVENT_TIME_FORMAT,
    TIMESTAMP_FORMAT,
    normalize_datetime_text,
    normalize_optional_datetime_text,
    normalize_repeat_times_value,
    validate_datetime_text,
)
from assistant_app.schemas.planner import ProactiveDoneArguments
from assistant_app.schemas.values import HistoryListLimitValue, ScheduleViewAnchorValue


class ProactiveDecision(ProactiveDoneArguments):
    pass


class ProactiveScheduleContextItem(FrozenModel):
    id: int = Field(ge=1)
    title: str = Field(min_length=1)
    tag: str = "default"
    event_time: str
    duration_minutes: int = Field(ge=1)
    remind_at: str | None = None
    repeat_interval_minutes: int | None = Field(default=None, ge=1)
    repeat_times: int | None = None
    repeat_enabled: bool | None = None

    @classmethod
    def from_schedule_item(cls, item: ScheduleItem) -> ProactiveScheduleContextItem:
        return cls.model_validate(
            {
                "id": item.id,
                "title": item.title,
                "tag": item.tag,
                "event_time": item.event_time,
                "duration_minutes": item.duration_minutes,
                "remind_at": item.remind_at,
                "repeat_interval_minutes": item.repeat_interval_minutes,
                "repeat_times": item.repeat_times,
                "repeat_enabled": item.repeat_enabled,
            }
        )

    @field_validator("event_time", mode="before")
    @classmethod
    def normalize_event_time(cls, value: object) -> str:
        return normalize_datetime_text(value, field_name="event_time", formats=(EVENT_TIME_FORMAT,))

    @field_validator("remind_at", mode="before")
    @classmethod
    def normalize_remind_at(cls, value: object) -> str | None:
        return normalize_optional_datetime_text(value, field_name="remind_at", formats=(EVENT_TIME_FORMAT,))

    @field_validator("repeat_times", mode="before")
    @classmethod
    def normalize_repeat_times(cls, value: object) -> int | None:
        if value is None:
            return None
        return normalize_repeat_times_value(value, field_name="repeat_times")


class ProactiveChatTurnContextItem(FrozenModel):
    created_at: str
    user_content: str
    assistant_content: str

    @classmethod
    def from_chat_turn(cls, item: ChatTurn) -> ProactiveChatTurnContextItem:
        return cls.model_validate(
            {
                "created_at": item.created_at,
                "user_content": item.user_content,
                "assistant_content": item.assistant_content,
            }
        )

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: object) -> str:
        return normalize_datetime_text(value, field_name="created_at", formats=(TIMESTAMP_FORMAT,))


class ProactivePromptPolicy(FrozenModel):
    channel: str = "feishu"
    target_type: str = "fixed_open_id"
    night_quiet_hint: str = Field(default="23:00-08:00", min_length=1)
    max_steps: int = Field(default=1, ge=1)
    internet_search_allowed: bool = False


class ProactiveContextWindow(FrozenModel):
    schedule_forward_hours: int = Field(default=24, ge=1)
    chat_history_backward_hours: int = Field(default=24, ge=1)


class ProactiveUserProfilePayload(FrozenModel):
    path: str = ""
    loaded: bool = False
    content: str = ""


class ProactiveInternalContext(FrozenModel):
    schedules: list[ProactiveScheduleContextItem] = Field(default_factory=list)
    recent_chat_turns: list[ProactiveChatTurnContextItem] = Field(default_factory=list)


class ProactiveDoneSchemaContract(FrozenModel):
    should_send: str = "boolean"
    message: str = "string"


class ProactiveOutputContract(FrozenModel):
    terminal_action: str = "done"
    done_schema: ProactiveDoneSchemaContract = Field(default_factory=ProactiveDoneSchemaContract)


class ProactiveExecutionResult(FrozenModel):
    should_send: bool
    message: str = ""


class ProactivePromptPayload(FrozenModel):
    task: str = "proactive_reminder_decision"
    now: str = ""
    timezone: str = Field(default="local", min_length=1)
    policy: ProactivePromptPolicy = Field(default_factory=ProactivePromptPolicy)
    context_window: ProactiveContextWindow = Field(default_factory=ProactiveContextWindow)
    user_profile: ProactiveUserProfilePayload = Field(default_factory=ProactiveUserProfilePayload)
    internal_context: ProactiveInternalContext = Field(default_factory=ProactiveInternalContext)
    output_contract: ProactiveOutputContract = Field(default_factory=ProactiveOutputContract)

    @field_validator("now")
    @classmethod
    def validate_now(cls, value: str) -> str:
        if not value:
            return value
        return validate_datetime_text(value, field_name="now", formats=(EVENT_TIME_FORMAT,))


class ProactiveContextSnapshot(FrozenModel):
    now: datetime
    lookahead_hours: int = Field(ge=1)
    chat_lookback_hours: int = Field(ge=1)
    schedules: list[ScheduleItem] = Field(default_factory=list)
    turns: list[ChatTurn] = Field(default_factory=list)
    user_profile_path: str = ""
    user_profile_content: str | None = None

    @property
    def has_user_profile(self) -> bool:
        return bool(self.user_profile_content)

    def to_prompt_payload(
        self,
        *,
        night_quiet_hint: str,
        max_steps: int,
        internet_search_allowed: bool,
    ) -> ProactivePromptPayload:
        return ProactivePromptPayload(
            now=self.now.strftime("%Y-%m-%d %H:%M"),
            timezone=_local_timezone_name(self.now),
            policy=ProactivePromptPolicy(
                night_quiet_hint=night_quiet_hint,
                max_steps=max_steps,
                internet_search_allowed=internet_search_allowed,
            ),
            context_window=ProactiveContextWindow(
                schedule_forward_hours=self.lookahead_hours,
                chat_history_backward_hours=self.chat_lookback_hours,
            ),
            user_profile=ProactiveUserProfilePayload(
                path=self.user_profile_path,
                loaded=self.has_user_profile,
                content=self.user_profile_content or "",
            ),
            internal_context=ProactiveInternalContext(
                schedules=[ProactiveScheduleContextItem.from_schedule_item(item) for item in self.schedules],
                recent_chat_turns=[ProactiveChatTurnContextItem.from_chat_turn(item) for item in self.turns],
            ),
        )


class ProactiveScheduleListToolResult(FrozenModel):
    count: int = Field(ge=0)
    items: list[ProactiveScheduleContextItem] = Field(default_factory=list)


class ProactiveScheduleViewToolResult(ProactiveScheduleListToolResult):
    view: str = Field(min_length=1)
    anchor: str = ""

    @field_validator("view", mode="before")
    @classmethod
    def normalize_view(cls, value: object) -> str:
        return str(value or "").strip().lower()

    @model_validator(mode="after")
    def validate_anchor(self) -> ProactiveScheduleViewToolResult:
        if not self.anchor:
            return self
        normalized = ScheduleViewAnchorValue.model_validate({"view": self.view, "anchor": self.anchor}).anchor
        object.__setattr__(self, "anchor", normalized or "")
        return self


class ProactiveScheduleGetToolResult(FrozenModel):
    found: bool
    id: int | None = Field(default=None, ge=1)
    item: ProactiveScheduleContextItem | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ProactiveScheduleGetToolResult:
        if self.found and self.item is None:
            raise ValueError("item is required when found is true")
        if not self.found and self.id is None:
            raise ValueError("id is required when found is false")
        return self


class ProactiveHistoryListToolResult(FrozenModel):
    limit: int = Field(ge=1, le=200)
    count: int = Field(ge=0)
    items: list[ProactiveChatTurnContextItem] = Field(default_factory=list)

    @field_validator("limit", mode="before")
    @classmethod
    def normalize_limit(cls, value: object) -> int:
        return HistoryListLimitValue.model_validate({"limit": value}).limit


class ProactiveHistorySearchToolResult(FrozenModel):
    keyword: str = Field(min_length=1)
    count: int = Field(ge=0)
    items: list[ProactiveChatTurnContextItem] = Field(default_factory=list)


class ProactiveInternetSearchToolResult(FrozenModel):
    query: str = Field(min_length=1)
    count: int = Field(ge=0)
    items: list[SearchResult] = Field(default_factory=list)


def _local_timezone_name(now: datetime) -> str:
    tzinfo = now.astimezone().tzinfo
    if tzinfo is None:
        return "local"
    tz_name = tzinfo.tzname(now)
    if not tz_name:
        return "local"
    return tz_name
