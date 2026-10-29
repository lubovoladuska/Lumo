import json
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest

from lumo.core.skill_registry import PreparedCall, SkillContext, SkillRegistry

MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture
def registry():
    reg = SkillRegistry()
    reg.discover()
    return reg


@pytest.fixture
def ctx(storage, scheduler, settings, clock):
    return SkillContext(
        "owner", "pc", storage, scheduler, settings, lambda: clock().astimezone(MSK)
    )


@pytest.fixture
async def call(registry, ctx, scheduler):
    async def run(name, **args):
        prepared = registry.prepare(name, json.dumps(args, default=str))
        if isinstance(prepared, str):
            return prepared
        result = await registry.run(prepared, ctx)
        return json.loads(result) if result.startswith("{") else result

    yield run
    await scheduler.stop()


def test_builtin_skills_registered(registry):
    names = {s.name for s in registry.enabled()}
    assert names == {
        "get_datetime",
        "remember_fact",
        "forget_fact",
        "set_timer",
        "set_reminder",
        "list_timers",
        "cancel_timer",
    }
    assert registry.get("forget_fact").confirm
    assert not registry.get("remember_fact").confirm


async def test_get_datetime_uses_timezone(call):
    result = await call("get_datetime")
    assert result["time"] == "12:00"
    assert result["weekday"] == "четверг"
    assert result["date"] == "29 октября 2026"


async def test_remember_and_forget(call, storage):
    assert "Сохранено" in await call("remember_fact", text="Любимый цвет — синий")
    [fact] = storage.list_facts("owner")
    assert fact.text == "Любимый цвет — синий"
    assert await call("forget_fact", fact_id=fact.id) == "Факт удалён."
    assert storage.list_facts("owner") == []
    assert (await call("forget_fact", fact_id=999)).startswith("Ошибка")


async def test_set_timer(call, storage, clock):
    result = await call("set_timer", duration_seconds=300, label="паста")
    assert result["fires_at"] == "сегодня в 12:05"
    [timer] = storage.active_timers("owner")
    assert timer.label == "паста"
    assert timer.fire_at == clock() + timedelta(minutes=5)


async def test_timer_too_long_rejected(call, storage):
    result = await call("set_timer", duration_seconds=3 * 86400)
    assert result.startswith("Ошибка")
    assert storage.active_timers() == []


async def test_reminder_tomorrow(call, storage):
    result = await call("set_reminder", when="2026-10-30T09:00:00+03:00", text="позвонить маме")
    assert result["fires_at"] == "завтра в 09:00"
    [timer] = storage.active_timers("owner")
    assert timer.kind == "reminder"
    assert timer.fire_at.astimezone(MSK).hour == 9


async def test_reminder_naive_time_uses_user_timezone(call, storage):
    await call("set_reminder", when="2026-10-29T18:00:00", text="x")
    assert storage.active_timers()[0].fire_at.astimezone(MSK).hour == 18


async def test_reminder_in_past_rejected(call, storage):
    result = await call("set_reminder", when="2026-10-28T09:00:00+03:00", text="x")
    assert "прошло" in result
    assert storage.active_timers() == []


async def test_list_and_cancel_by_label(call, storage):
    await call("set_timer", duration_seconds=60, label="паста")
    await call("set_reminder", when="2026-10-30T09:00:00+03:00", text="позвонить маме")
    listed = await call("list_timers")
    assert listed["count"] == 2
    assert {i["kind"] for i in listed["items"]} == {"таймер", "напоминание"}

    assert "паста" in await call("cancel_timer", label="Паста")
    assert [t.label for t in storage.active_timers()] == ["позвонить маме"]
    assert (await call("cancel_timer", label="нет такого")).startswith("Ошибка")


def test_prepared_call_type(registry):
    assert isinstance(registry.prepare("list_timers", None), PreparedCall)
