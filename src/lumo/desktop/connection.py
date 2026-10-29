"""Соединение клиента с ядром: рукопожатие, запросы-ответы, события, переподключение."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import websockets
from websockets.exceptions import ConnectionClosed

from lumo import __version__
from lumo.protocol.messages import (
    ActionRequest,
    ConfirmRequest,
    Error,
    Event,
    Hello,
    Reply,
    Utterance,
    Welcome,
    parse_server_message,
)

if TYPE_CHECKING:
    from lumo.desktop.actions import ActionExecutor

log = logging.getLogger(__name__)

EventHandler = Callable[[Event], Awaitable[None]]
StatusHandler = Callable[[bool], Awaitable[None]]


class CoreUnavailable(RuntimeError):
    pass


class AuthRejected(RuntimeError):
    pass


class CoreConnection:
    def __init__(
        self,
        url: str,
        token: str,
        device_id: str,
        on_event: EventHandler,
        on_status: StatusHandler | None = None,
        max_backoff: float = 30.0,
        initial_backoff: float = 1.0,
        executor: "ActionExecutor | None" = None,
    ) -> None:
        self.url = url
        self.token = token
        self.device_id = device_id
        self.on_event = on_event
        self.on_status = on_status
        self.max_backoff = max_backoff
        self.initial_backoff = initial_backoff
        self.executor = executor
        self.assistant_name = "Вальтер"
        self._ws: websockets.ClientConnection | None = None
        self._connected = asyncio.Event()
        self._waiters: dict[str, asyncio.Future] = {}

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def run_forever(self) -> None:
        """Держит соединение, переподключаясь с нарастающей паузой (до ``max_backoff``)."""
        backoff = self.initial_backoff
        while True:
            try:
                async with websockets.connect(self.url, open_timeout=10) as ws:
                    await self._handshake(ws)
                    backoff = self.initial_backoff
                    await self._set_status(True)
                    await self._read_loop(ws)
            except AuthRejected:
                log.error("Ядро отклонило токен. Проверьте LUMO_TOKEN в .env")
                raise
            except (OSError, ConnectionClosed, TimeoutError) as exc:
                log.debug("Соединение с ядром: %r", exc)
            finally:
                self._ws = None
                if self._connected.is_set():
                    self._connected.clear()
                    self._fail_waiters()
                    await self._set_status(False)
            log.info("Нет связи с ядром, повтор через %.0f с", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.max_backoff)

    async def wait_connected(self, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def ask(self, text: str, timeout: float = 90.0) -> Reply | ConfirmRequest:
        """Отправляет реплику и ждёт ответ на неё."""
        if self._ws is None or not self.connected:
            raise CoreUnavailable("нет связи с ядром")
        utterance = Utterance(text=text)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiters[utterance.id] = future
        try:
            await self._ws.send(utterance.model_dump_json())
            return await asyncio.wait_for(future, timeout)
        except ConnectionClosed as exc:
            raise CoreUnavailable("связь с ядром потеряна") from exc
        finally:
            self._waiters.pop(utterance.id, None)

    async def _handshake(self, ws: websockets.ClientConnection) -> None:
        hello = Hello(
            token=self.token,
            device_id=self.device_id,
            client_version=__version__,
            capabilities=self.executor.capabilities if self.executor else [],
        )
        await ws.send(hello.model_dump_json())
        try:
            welcome = parse_server_message(await asyncio.wait_for(ws.recv(), 10))
        except ConnectionClosed as exc:
            if exc.rcvd and exc.rcvd.code == 4401:
                raise AuthRejected from exc
            raise
        if not isinstance(welcome, Welcome):
            raise ConnectionClosed(None, None)
        self.assistant_name = welcome.assistant_name
        self._ws = ws
        self._connected.set()
        log.info("Подключено к ядру %s", welcome.core_version)

    async def _read_loop(self, ws: websockets.ClientConnection) -> None:
        async for raw in ws:
            message = parse_server_message(raw)
            if isinstance(message, Reply | ConfirmRequest | Error) and message.id:
                future = self._waiters.get(message.id)
                if future and not future.done():
                    future.set_result(message)
            elif isinstance(message, Event):
                asyncio.create_task(self.on_event(message))
            elif isinstance(message, ActionRequest):
                asyncio.create_task(self._run_action(ws, message))

    async def _run_action(self, ws: websockets.ClientConnection, request: ActionRequest) -> None:
        if self.executor is None:
            return
        result = await self.executor.execute(request)
        log.info("Действие %s: %s", request.action, "ок" if result.ok else result.text)
        with contextlib.suppress(ConnectionClosed):
            await ws.send(result.model_dump_json())

    def _fail_waiters(self) -> None:
        for future in self._waiters.values():
            if not future.done():
                future.set_exception(CoreUnavailable("связь с ядром потеряна"))

    async def _set_status(self, connected: bool) -> None:
        if self.on_status:
            await self.on_status(connected)
