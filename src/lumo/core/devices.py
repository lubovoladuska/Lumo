"""Действия на устройствах: навык ядра просит клиента с нужной возможностью что-то сделать.

Ядро не знает, как выключить компьютер или включить музыку, — это умеют клиенты. Навык
вызывает ``await devices.run("pc.volume_set", {...})``, роутер выбирает подходящий клиент,
отправляет ему ``action_request`` и ждёт ``action_result``.
"""

import asyncio
import logging
from dataclasses import dataclass

from lumo.core.hub import ClientHub, Connection
from lumo.protocol.messages import ActionRequest, ActionResult

log = logging.getLogger(__name__)

ACTION_TIMEOUT = 15.0

CAPABILITY_NAMES = {"pc": "Компьютер", "music": "Музыкальный плеер"}


@dataclass(frozen=True)
class ActionOutcome:
    ok: bool
    text: str


class DeviceRouter:
    def __init__(self, hub: ClientHub, timeout: float = ACTION_TIMEOUT) -> None:
        self.hub = hub
        self.timeout = timeout
        self._pending: dict[str, tuple[Connection, asyncio.Future[ActionResult]]] = {}

    def pick(self, user_id: str, capability: str, origin: str | None = None) -> Connection | None:
        """Клиент с возможностью: сначала устройство реплики, иначе подключившийся последним."""
        candidates = [c for c in self.hub.connections_for(user_id) if capability in c.capabilities]
        if not candidates:
            return None
        for conn in candidates:
            if conn.device_id == origin:
                return conn
        return max(candidates, key=lambda c: c.connected_at)

    async def run(
        self, user_id: str, action: str, params: dict | None = None, origin: str | None = None
    ) -> ActionOutcome:
        request = ActionRequest(action=action, params=params or {})
        capability = request.capability
        conn = self.pick(user_id, capability, origin)
        if conn is None:
            name = CAPABILITY_NAMES.get(capability, f"Устройство «{capability}»")
            return ActionOutcome(False, f"{name} сейчас не подключён.")

        future: asyncio.Future[ActionResult] = asyncio.get_running_loop().create_future()
        self._pending[request.id] = (conn, future)
        try:
            await conn.send(request)
            result = await asyncio.wait_for(future, self.timeout)
        except TimeoutError:
            log.warning("Устройство %s не ответило на %s", conn.device_id, action)
            return ActionOutcome(False, "Устройство не ответило вовремя.")
        except (ConnectionError, asyncio.CancelledError, RuntimeError):
            return ActionOutcome(False, "Связь с устройством потеряна.")
        finally:
            self._pending.pop(request.id, None)
        return ActionOutcome(result.ok, result.text)

    def resolve(self, result: ActionResult) -> None:
        entry = self._pending.get(result.id)
        if entry and not entry[1].done():
            entry[1].set_result(result)

    def disconnected(self, conn: Connection) -> None:
        """Клиент отключился — его действия уже не выполнятся."""
        for owner, future in list(self._pending.values()):
            if owner is conn and not future.done():
                future.set_exception(ConnectionError("клиент отключился"))
