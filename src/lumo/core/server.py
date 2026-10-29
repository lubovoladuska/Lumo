"""HTTP/WebSocket API ядра."""

import asyncio
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Protocol

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from lumo import __version__
from lumo.config import Settings
from lumo.core.devices import DeviceRouter
from lumo.core.hub import ClientHub, Connection
from lumo.protocol.messages import (
    ActionResult,
    ConfirmRequest,
    Error,
    Hello,
    Reply,
    Utterance,
    Welcome,
    parse_client_message,
)

log = logging.getLogger(__name__)

# В MVP один пользователь-владелец; поле уже есть во всех данных.
OWNER = "owner"
HELLO_TIMEOUT = 10.0


class Assistant(Protocol):
    async def handle_utterance(
        self, user_id: str, device_id: str, utterance: Utterance
    ) -> Reply | ConfirmRequest: ...

    async def on_client_connected(self, conn: Connection) -> None: ...


Lifecycle = Callable[[], Awaitable[None]]


def create_app(
    settings: Settings,
    assistant: Assistant,
    hub: ClientHub,
    devices: DeviceRouter | None = None,
    on_startup: Lifecycle | None = None,
    on_shutdown: Lifecycle | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if on_startup:
            await on_startup()
        yield
        if on_shutdown:
            await on_shutdown()

    app = FastAPI(title="Lumo Core", version=__version__, lifespan=lifespan)
    expected_token = settings.token.get_secret_value()

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), HELLO_TIMEOUT)
            hello = parse_client_message(raw)
        except (TimeoutError, ValidationError, WebSocketDisconnect):
            await websocket.close(code=4400)
            return
        if not isinstance(hello, Hello) or not hmac.compare_digest(
            hello.token.encode(), expected_token.encode()
        ):
            log.warning("Отклонено подключение с неверным токеном")
            await websocket.close(code=4401)
            return

        conn = Connection(websocket, OWNER, hello.device_id, frozenset(hello.capabilities))
        hub.add(conn)
        await conn.send(Welcome(core_version=__version__, assistant_name=settings.assistant_name))
        # Реплики обрабатываются отдельной задачей по очереди: цикл чтения не блокируется
        # и может принять action_result, которого ждёт навык этой же реплики.
        utterances: asyncio.Queue[Utterance] = asyncio.Queue()
        worker = asyncio.create_task(_process_utterances(assistant, conn, utterances))
        try:
            await assistant.on_client_connected(conn)
            while True:
                raw = await websocket.receive_text()
                try:
                    message = parse_client_message(raw)
                except ValidationError:
                    await conn.send(Error(message="Некорректное сообщение"))
                    continue
                if isinstance(message, Utterance):
                    utterances.put_nowait(message)
                elif isinstance(message, ActionResult) and devices is not None:
                    devices.resolve(message)
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove(conn)
            if devices is not None:
                devices.disconnected(conn)
            worker.cancel()

    return app


async def _process_utterances(
    assistant: Assistant, conn: Connection, queue: "asyncio.Queue[Utterance]"
) -> None:
    while True:
        utterance = await queue.get()
        try:
            response = await assistant.handle_utterance(conn.user_id, conn.device_id, utterance)
            await conn.send(response)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ошибка обработки реплики")
            try:
                await conn.send(Error(id=utterance.id, message="Внутренняя ошибка ядра"))
            except Exception:
                return
