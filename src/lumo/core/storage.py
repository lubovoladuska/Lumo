"""Хранилище ядра в SQLite: факты, история, таймеры, недоставленные события.

Все даты хранятся в UTC в формате ISO 8601.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_messages_user_time ON messages(user_id, created_at);
CREATE TABLE IF NOT EXISTS timers (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    fire_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_events (
    id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    scheduled_for TEXT,
    created_at TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    # Фиксированная точность, чтобы строки корректно сравнивались в SQL.
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


@dataclass(frozen=True)
class Fact:
    id: int
    text: str
    created_at: datetime


@dataclass(frozen=True)
class Message:
    role: str
    content: str
    device_id: str
    created_at: datetime


@dataclass(frozen=True)
class Timer:
    id: int
    user_id: str
    kind: str
    label: str
    fire_at: datetime
    status: str


@dataclass(frozen=True)
class PendingEvent:
    id: int
    kind: str
    text: str
    scheduled_for: datetime | None


class Storage:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            self._db.executescript(_SCHEMA)
            self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    # --- факты ---

    def add_fact(self, user_id: str, text: str) -> Fact:
        now = utcnow()
        cur = self._db.execute(
            "INSERT INTO facts (user_id, text, created_at) VALUES (?, ?, ?)",
            (user_id, text, _iso(now)),
        )
        self._db.commit()
        return Fact(cur.lastrowid, text, now)

    def list_facts(self, user_id: str, limit: int = 200) -> list[Fact]:
        rows = self._db.execute(
            "SELECT id, text, created_at FROM facts WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [Fact(r["id"], r["text"], _dt(r["created_at"])) for r in reversed(rows)]

    def get_fact(self, user_id: str, fact_id: int) -> Fact | None:
        r = self._db.execute(
            "SELECT id, text, created_at FROM facts WHERE user_id = ? AND id = ?",
            (user_id, fact_id),
        ).fetchone()
        return Fact(r["id"], r["text"], _dt(r["created_at"])) if r else None

    def delete_fact(self, user_id: str, fact_id: int) -> bool:
        cur = self._db.execute("DELETE FROM facts WHERE user_id = ? AND id = ?", (user_id, fact_id))
        self._db.commit()
        return cur.rowcount > 0

    # --- история ---

    def add_message(
        self, user_id: str, device_id: str, role: str, content: str, at: datetime | None = None
    ) -> None:
        self._db.execute(
            "INSERT INTO messages (user_id, device_id, role, content, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, device_id, role, content, _iso(at or utcnow())),
        )
        self._db.commit()

    def recent_messages(
        self,
        user_id: str,
        window: timedelta = timedelta(minutes=30),
        limit: int = 20,
        now: datetime | None = None,
    ) -> list[Message]:
        """Последние сообщения пользователя со всех устройств, по возрастанию времени."""
        since = (now or utcnow()) - window
        rows = self._db.execute(
            "SELECT role, content, device_id, created_at FROM messages"
            " WHERE user_id = ? AND created_at >= ? ORDER BY id DESC LIMIT ?",
            (user_id, _iso(since), limit),
        ).fetchall()
        return [
            Message(r["role"], r["content"], r["device_id"], _dt(r["created_at"]))
            for r in reversed(rows)
        ]

    # --- таймеры ---

    def add_timer(self, user_id: str, kind: str, label: str, fire_at: datetime) -> Timer:
        cur = self._db.execute(
            "INSERT INTO timers (user_id, kind, label, fire_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, kind, label, _iso(fire_at), _iso(utcnow())),
        )
        self._db.commit()
        return Timer(cur.lastrowid, user_id, kind, label, fire_at.astimezone(UTC), "active")

    def active_timers(self, user_id: str | None = None) -> list[Timer]:
        query = "SELECT * FROM timers WHERE status = 'active'"
        params: tuple = ()
        if user_id is not None:
            query += " AND user_id = ?"
            params = (user_id,)
        rows = self._db.execute(query + " ORDER BY fire_at", params).fetchall()
        return [
            Timer(r["id"], r["user_id"], r["kind"], r["label"], _dt(r["fire_at"]), r["status"])
            for r in rows
        ]

    def set_timer_status(self, timer_id: int, status: str) -> bool:
        cur = self._db.execute(
            "UPDATE timers SET status = ? WHERE id = ? AND status = 'active'", (status, timer_id)
        )
        self._db.commit()
        return cur.rowcount > 0

    # --- недоставленные события ---

    def add_pending_event(
        self, user_id: str, kind: str, text: str, scheduled_for: datetime | None
    ) -> None:
        self._db.execute(
            "INSERT INTO pending_events (user_id, kind, text, scheduled_for, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, kind, text, _iso(scheduled_for) if scheduled_for else None, _iso(utcnow())),
        )
        self._db.commit()

    def take_pending_events(self, user_id: str) -> list[PendingEvent]:
        """Возвращает и удаляет недоставленные события пользователя."""
        rows = self._db.execute(
            "SELECT * FROM pending_events WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        self._db.execute("DELETE FROM pending_events WHERE user_id = ?", (user_id,))
        self._db.commit()
        return [PendingEvent(r["id"], r["kind"], r["text"], _dt(r["scheduled_for"])) for r in rows]
