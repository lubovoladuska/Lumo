"""Реестр подключённых клиентов и рассылка им сообщений."""

import asyncio
import itertools
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket
from pydantic import BaseModel

log = logging.getLogger(__name__)


@dataclass(eq=False)
class Connection:
    websocket: WebSocket
    user_id: str
    device_id: str
    capabilities: frozenset[str] = frozenset()
    # Порядковый номер подключения: больше — подключился позже.
    connected_at: int = field(default_factory=itertools.count().__next__)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, message: BaseModel) -> None:
        async with self.lock:
            await self.websocket.send_text(message.model_dump_json())


class ClientHub:
    def __init__(self) -> None:
        self._connections: set[Connection] = set()

    def add(self, conn: Connection) -> None:
        self._connections.add(conn)
        caps = ", ".join(sorted(conn.capabilities)) or "нет"
        log.info("Клиент подключён: %s/%s (возможности: %s)", conn.user_id, conn.device_id, caps)

    def remove(self, conn: Connection) -> None:
        self._connections.discard(conn)
        log.info("Клиент отключён: %s/%s", conn.user_id, conn.device_id)

    def connections_for(self, user_id: str) -> list[Connection]:
        return [c for c in self._connections if c.user_id == user_id]

    async def broadcast(self, user_id: str, message: BaseModel) -> int:
        """Отправляет сообщение всем клиентам пользователя. Возвращает число доставок."""
        delivered = 0
        for conn in self.connections_for(user_id):
            try:
                await conn.send(message)
                delivered += 1
            except Exception:  # клиент мог отключиться в процессе
                log.warning("Не удалось отправить сообщение клиенту %s", conn.device_id)
        return delivered
