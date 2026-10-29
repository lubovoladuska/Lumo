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
from lumo.core.hub import ClientHub, Connection
from lumo.protocol.messages import (
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

        conn = Connection(websocket, OWNER, hello.device_id)
        hub.add(conn)
        await conn.send(Welcome(core_version=__version__, assistant_name=settings.assistant_name))
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
                    response = await assistant.handle_utterance(
                        conn.user_id, conn.device_id, message
                    )
                    await conn.send(response)
        except WebSocketDisconnect:
            pass
        finally:
            hub.remove(conn)

    return app
