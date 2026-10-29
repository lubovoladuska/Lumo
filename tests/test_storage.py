from datetime import UTC, datetime, timedelta

import pytest

from lumo.core.storage import Storage


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "lumo.db"


def test_facts_survive_reopen(db_path):
    s = Storage(db_path)
    fact = s.add_fact("owner", "Любимый цвет — синий")
    s.close()

    s = Storage(db_path)
    assert [f.text for f in s.list_facts("owner")] == ["Любимый цвет — синий"]
    assert s.delete_fact("owner", fact.id)
    assert s.list_facts("owner") == []


def test_facts_are_per_user(db_path):
    s = Storage(db_path)
    s.add_fact("owner", "a")
    assert s.list_facts("someone") == []


def test_context_spans_devices_within_window(db_path):
    s = Storage(db_path)
    now = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    s.add_message("owner", "pc", "user", "старое", at=now - timedelta(hours=2))
    s.add_message("owner", "pc", "user", "Меня интересует Париж", at=now - timedelta(minutes=10))
    s.add_message("owner", "pc", "assistant", "Хорошо", at=now - timedelta(minutes=10))
    s.add_message("owner", "phone", "user", "А что там посмотреть?", at=now - timedelta(minutes=1))

    msgs = s.recent_messages("owner", now=now)
    assert [m.content for m in msgs] == ["Меня интересует Париж", "Хорошо", "А что там посмотреть?"]
    assert {m.device_id for m in msgs} == {"pc", "phone"}


def test_context_limit_keeps_latest(db_path):
    s = Storage(db_path)
    now = datetime.now(UTC)
    for i in range(30):
        s.add_message("owner", "pc", "user", str(i), at=now)
    msgs = s.recent_messages("owner", limit=20)
    assert [m.content for m in msgs] == [str(i) for i in range(10, 30)]


def test_timers_and_pending_events_survive_reopen(db_path):
    s = Storage(db_path)
    fire_at = datetime(2026, 10, 29, 18, 0, tzinfo=UTC)
    timer = s.add_timer("owner", "reminder", "позвонить маме", fire_at)
    s.add_pending_event("owner", "reminder", "текст", fire_at)
    s.close()

    s = Storage(db_path)
    [loaded] = s.active_timers()
    assert loaded.id == timer.id
    assert loaded.fire_at == fire_at
    assert s.set_timer_status(timer.id, "fired")
    assert s.active_timers() == []
    [event] = s.take_pending_events("owner")
    assert event.scheduled_for == fire_at
    assert s.take_pending_events("owner") == []


def test_migration_from_v1_keeps_data(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, user_id TEXT, device_id TEXT,"
        " role TEXT, content TEXT, created_at TEXT); PRAGMA user_version = 1;"
    )
    db.execute(
        "INSERT INTO messages (user_id, device_id, role, content, created_at)"
        " VALUES ('owner', 'pc', 'user', 'старое', '2026-10-29T12:00:00.000000+00:00')"
    )
    db.commit()
    db.close()

    s = Storage(path)
    now = datetime(2026, 10, 29, 12, 5, tzinfo=UTC)
    s.add_message("owner", "pc", "assistant", "ok", at=now, tool_calls=[{"name": "x"}])
    msgs = s.recent_messages("owner", now=now)
    assert [m.content for m in msgs] == ["старое", "ok"]
    assert msgs[0].tool_calls is None and msgs[1].tool_calls == [{"name": "x"}]
