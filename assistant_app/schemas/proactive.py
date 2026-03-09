from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from assistant_app.config import DEFAULT_PROACTIVE_REMINDER_SCORE_THRESHOLD
from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import (
    EVENT_TIME_FORMAT,
    TIMESTAMP_FORMAT,
    ChatTurn,
    ScheduleItem,
    _validate_datetime_text,
)
from assistant_app.schemas.planner import ProactiveDoneArguments


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

    @field_validator("event_time")
    @classmethod
    def validate_event_time(cls, value: str) -> str:
        return _validate_datetime_text(value, field_name="event_time", formats=(EVENT_TIME_FORMAT,))

    @field_validator("remind_at")
    @classmethod
    def validate_optional_remind_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_datetime_text(value, field_name="remind_at", formats=(EVENT_TIME_FORMAT,))

    @field_validator("repeat_times")
    @classmethod
    def validate_repeat_times(cls, value: int | None) -> int | None:
        if value is None or value == -1 or value >= 2:
            return value
        raise ValueError("repeat_times must be -1 or >= 2")


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

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _validate_datetime_text(value, field_name="created_at", formats=(TIMESTAMP_FORMAT,))


class ProactivePromptPolicy(FrozenModel):
    channel: str = "feishu"
    target_type: str = "fixed_open_id"
    night_quiet_hint: str = Field(default="23:00-08:00", min_length=1)
    score_threshold: int = DEFAULT_PROACTIVE_REMINDER_SCORE_THRESHOLD
    max_steps: int = Field(default=1, ge=1)
    internet_search_allowed: bool = False

    @field_validator("score_threshold", mode="before")
    @classmethod
    def normalize_score_threshold(cls, value: object) -> int:
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100:
            return value
        return DEFAULT_PROACTIVE_REMINDER_SCORE_THRESHOLD


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
    score: str = "integer (0~100)"
    message: str = "string"
    reason: str = "string"


class ProactiveOutputContract(FrozenModel):
    terminal_action: str = "done"
    done_schema: ProactiveDoneSchemaContract = Field(default_factory=ProactiveDoneSchemaContract)


class ProactivePromptPayload(FrozenModel):
    task: str = "proactive_reminder_decision"
    now: str = ""
    timezone: str = Field(default="local", min_length=1)
    policy: ProactivePromptPolicy = Field(default_factory=ProactivePromptPolicy)
    context_window: ProactiveContextWindow = Field(default_factory=ProactiveContextWindow)
    user_profile: ProactiveUserProfilePayload = Field(default_factory=ProactiveUserProfilePayload)
    internal_context: ProactiveInternalContext = Field(default_factory=ProactiveInternalContext)
    output_contract: ProactiveOutputContract = Field(default_factory=ProactiveOutputContract)

    @property
    def score_threshold(self) -> int:
        return self.policy.score_threshold

    @field_validator("now")
    @classmethod
    def validate_now(cls, value: str) -> str:
        if not value:
            return value
        return _validate_datetime_text(value, field_name="now", formats=(EVENT_TIME_FORMAT,))


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
        score_threshold: int,
        max_steps: int,
        internet_search_allowed: bool,
    ) -> ProactivePromptPayload:
        return ProactivePromptPayload(
            now=self.now.strftime("%Y-%m-%d %H:%M"),
            timezone=_local_timezone_name(self.now),
            policy=ProactivePromptPolicy(
                night_quiet_hint=night_quiet_hint,
                score_threshold=score_threshold,
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


def _local_timezone_name(now: datetime) -> str:
    tzinfo = now.astimezone().tzinfo
    if tzinfo is None:
        return "local"
    tz_name = tzinfo.tzname(now)
    if not tz_name:
        return "local"
    return tz_name
