"""Планировщик таймеров и напоминаний поверх таблицы ``timers``."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from lumo.core.hub import ClientHub
from lumo.core.storage import Storage, Timer, utcnow
from lumo.protocol.messages import Event

log = logging.getLogger(__name__)

# Длинные ожидания режутся на куски, чтобы не уплывать после сна ПК или смены часов.
MAX_SLEEP_CHUNK = 30.0
MISSED_AFTER = timedelta(minutes=1)


def event_text(timer: Timer) -> str:
    if timer.kind == "reminder":
        return f"Напоминание: {timer.label}"
    return f"Сработал таймер «{timer.label}»." if timer.label else "Время вышло, таймер сработал."


class Scheduler:
    def __init__(
        self,
        storage: Storage,
        hub: ClientHub,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.storage = storage
        self.hub = hub
        self.clock = clock
        self._tasks: dict[int, asyncio.Task] = {}

    async def start(self) -> None:
        """Загружает активные таймеры. Просроченные срабатывают сразу."""
        timers = self.storage.active_timers()
        for timer in timers:
            self.schedule(timer)
        log.info("Планировщик: загружено %d активных таймеров", len(timers))

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    def add(self, user_id: str, kind: str, label: str, fire_at: datetime) -> Timer:
        timer = self.storage.add_timer(user_id, kind, label, fire_at)
        self.schedule(timer)
        return timer

    def schedule(self, timer: Timer) -> None:
        task = asyncio.create_task(self._wait_and_fire(timer), name=f"timer-{timer.id}")
        self._tasks[timer.id] = task
        task.add_done_callback(lambda _: self._tasks.pop(timer.id, None))

    def cancel(self, timer_id: int) -> bool:
        if not self.storage.set_timer_status(timer_id, "cancelled"):
            return False
        task = self._tasks.pop(timer_id, None)
        if task:
            task.cancel()
        return True

    async def _wait_and_fire(self, timer: Timer) -> None:
        while (remaining := (timer.fire_at - self.clock()).total_seconds()) > 0:
            await asyncio.sleep(min(remaining, MAX_SLEEP_CHUNK))
        await self.fire(timer)

    async def fire(self, timer: Timer) -> None:
        if not self.storage.set_timer_status(timer.id, "fired"):
            return  # уже отменён или сработал
        missed = self.clock() - timer.fire_at > MISSED_AFTER
        event = Event(
            kind=timer.kind, text=event_text(timer), scheduled_for=timer.fire_at, missed=missed
        )
        delivered = await self.hub.broadcast(timer.user_id, event)
        log.info("Сработал %s #%d, доставлено клиентам: %d", timer.kind, timer.id, delivered)
        if delivered == 0:
            self.storage.add_pending_event(timer.user_id, timer.kind, event.text, timer.fire_at)
