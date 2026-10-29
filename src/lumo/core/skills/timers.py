from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from lumo.core.skill_registry import SkillContext, skill
from lumo.core.skills._format import human_datetime

MAX_TIMER_SECONDS = 24 * 3600


class TimerParams(BaseModel):
    duration_seconds: int = Field(
        ge=1,
        le=MAX_TIMER_SECONDS,
        description="Длительность в секундах, от 1 до 86400 (24 часа). "
        "Для более долгих сроков предложи напоминание (set_reminder).",
    )
    label: str = Field(
        "", max_length=100, description="Подпись, например «паста». Может быть пустой."
    )


class ReminderParams(BaseModel):
    when: datetime = Field(
        description="Когда напомнить: ISO 8601 с часовым поясом пользователя, "
        "например 2026-10-30T09:00:00+03:00."
    )
    text: str = Field(min_length=1, max_length=300, description="О чём напомнить.")


class CancelParams(BaseModel):
    timer_id: int | None = Field(None, description="Номер таймера или напоминания из list_timers.")
    label: str | None = Field(
        None, description="Подпись или текст для поиска, если номер неизвестен."
    )


@skill("set_timer", "Поставить таймер на заданную длительность.", TimerParams, reply="short")
async def set_timer(params: TimerParams, ctx: SkillContext) -> dict:
    now = ctx.now()
    fire_at = now + timedelta(seconds=params.duration_seconds)
    timer = ctx.scheduler.add(ctx.user_id, "timer", params.label.strip(), fire_at)
    return {"ok": True, "id": timer.id, "fires_at": human_datetime(fire_at, now)}


@skill(
    "set_reminder",
    "Поставить напоминание на конкретные дату и время. Относительные выражения "
    "(«завтра в 9», «через час») переводи в точное время сам, исходя из текущего времени.",
    ReminderParams,
)
async def set_reminder(params: ReminderParams, ctx: SkillContext) -> dict | str:
    now = ctx.now()
    when = params.when if params.when.tzinfo else params.when.replace(tzinfo=now.tzinfo)
    if when <= now:
        return "Ошибка: это время уже прошло. Уточни у пользователя, когда напомнить."
    timer = ctx.scheduler.add(ctx.user_id, "reminder", params.text.strip(), when)
    return {
        "ok": True,
        "id": timer.id,
        "fires_at": human_datetime(when.astimezone(now.tzinfo), now),
    }


@skill("list_timers", "Перечислить активные таймеры и напоминания пользователя.")
async def list_timers(params, ctx: SkillContext) -> dict:
    now = ctx.now()
    items = [
        {
            "id": t.id,
            "kind": "напоминание" if t.kind == "reminder" else "таймер",
            "label": t.label,
            "fires_at": human_datetime(t.fire_at.astimezone(now.tzinfo), now),
        }
        for t in ctx.storage.active_timers(ctx.user_id)
    ]
    return {"count": len(items), "items": items}


@skill(
    "cancel_timer",
    "Отменить таймер или напоминание по номеру или по подписи.",
    CancelParams,
)
async def cancel_timer(params: CancelParams, ctx: SkillContext) -> str:
    active = ctx.storage.active_timers(ctx.user_id)
    if params.timer_id is not None:
        matches = [t for t in active if t.id == params.timer_id]
    elif params.label:
        needle = params.label.lower().strip()
        matches = [t for t in active if needle in t.label.lower()]
    else:
        return "Ошибка: укажи номер или подпись таймера."
    if not matches:
        return "Ошибка: такого активного таймера или напоминания нет."
    if len(matches) > 1:
        labels = ", ".join(f"{t.id}: {t.label or 'без подписи'}" for t in matches)
        return f"Ошибка: подходит несколько ({labels}). Уточни номер."
    ctx.scheduler.cancel(matches[0].id)
    return f"Отменено: {matches[0].label or 'таймер'}."
