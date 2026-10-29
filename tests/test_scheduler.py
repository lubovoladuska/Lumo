import asyncio
from datetime import timedelta

from lumo.core.hub import Connection
from lumo.core.scheduler import Scheduler
from lumo.protocol.messages import Event, parse_server_message


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list = []

    async def send_text(self, data: str) -> None:
        self.sent.append(parse_server_message(data))


def connect(hub, device="pc"):
    sock = FakeSocket()
    hub.add(Connection(sock, "owner", device))
    return sock


async def run_until_idle(scheduler, clock, seconds):
    """Двигает фейковые часы и даёт задачам планировщика проснуться."""
    clock.advance(seconds=seconds)
    # MAX_SLEEP_CHUNK в тестах крошечный, задачи успевают перепроверить часы.
    await asyncio.sleep(0.1)


async def test_fires_and_broadcasts_to_all_clients(scheduler, hub, clock, monkeypatch):
    monkeypatch.setattr("lumo.core.scheduler.MAX_SLEEP_CHUNK", 0.01)
    pc, phone = connect(hub, "pc"), connect(hub, "phone")
    scheduler.add("owner", "timer", "паста", clock() + timedelta(seconds=30))
    await run_until_idle(scheduler, clock, 31)
    for sock in (pc, phone):
        [event] = sock.sent
        assert isinstance(event, Event)
        assert event.text == "Сработал таймер «паста»."
        assert not event.missed


async def test_not_fired_before_time(scheduler, hub, clock, storage, monkeypatch):
    monkeypatch.setattr("lumo.core.scheduler.MAX_SLEEP_CHUNK", 0.01)
    sock = connect(hub)
    scheduler.add("owner", "timer", "", clock() + timedelta(minutes=10))
    await run_until_idle(scheduler, clock, 60)
    assert sock.sent == []
    assert len(storage.active_timers()) == 1
    await scheduler.stop()


async def test_undelivered_event_is_queued(scheduler, clock, storage):
    timer = storage.add_timer("owner", "reminder", "позвонить маме", clock() - timedelta(seconds=1))
    await scheduler.fire(timer)
    [pending] = storage.take_pending_events("owner")
    assert pending.text == "Напоминание: позвонить маме"


async def test_overdue_after_restart_fires_immediately(storage, hub, clock):
    storage.add_timer("owner", "reminder", "встреча", clock() - timedelta(hours=1))
    sock = connect(hub)
    scheduler = Scheduler(storage, hub, clock)  # «перезапуск» ядра
    await scheduler.start()
    await asyncio.wait(list(scheduler._tasks.values()), timeout=1)
    [event] = sock.sent
    assert event.missed
    assert storage.active_timers() == []


async def test_future_timer_survives_restart(storage, hub, clock, monkeypatch):
    monkeypatch.setattr("lumo.core.scheduler.MAX_SLEEP_CHUNK", 0.01)
    storage.add_timer("owner", "reminder", "в 18:00", clock() + timedelta(hours=1))
    sock = connect(hub)
    scheduler = Scheduler(storage, hub, clock)
    await scheduler.start()
    await run_until_idle(scheduler, clock, 30 * 60)
    assert sock.sent == []
    await run_until_idle(scheduler, clock, 31 * 60)
    assert [e.text for e in sock.sent] == ["Напоминание: в 18:00"]


async def test_cancelled_timer_never_fires(scheduler, hub, clock, storage, monkeypatch):
    monkeypatch.setattr("lumo.core.scheduler.MAX_SLEEP_CHUNK", 0.01)
    sock = connect(hub)
    timer = scheduler.add("owner", "timer", "паста", clock() + timedelta(seconds=30))
    assert scheduler.cancel(timer.id)
    await run_until_idle(scheduler, clock, 60)
    assert sock.sent == []
    assert not scheduler.cancel(timer.id)


async def test_real_time_precision(storage, hub):
    """Без фейковых часов таймер срабатывает с отклонением меньше 2 секунд."""
    from lumo.core.storage import utcnow

    sock = connect(hub)
    scheduler = Scheduler(storage, hub)
    fire_at = utcnow() + timedelta(seconds=0.5)
    scheduler.add("owner", "timer", "", fire_at)
    await asyncio.wait(list(scheduler._tasks.values()), timeout=3)
    assert len(sock.sent) == 1
    assert (utcnow() - fire_at).total_seconds() < 2
