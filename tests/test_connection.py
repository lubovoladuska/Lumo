import asyncio
import socket

import pytest
import uvicorn

from lumo.core.app import build_app
from lumo.desktop.connection import AuthRejected, CoreConnection
from lumo.protocol.messages import Event
from tests.conftest import ScriptedLLM, text


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _start_core(settings, port):
    config = uvicorn.Config(
        build_app(settings, ScriptedLLM(text("pong"))), port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    return server, task


async def _stop_core(server, task):
    server.should_exit = True
    await task


async def test_ask_and_reconnect_after_core_restart(settings):
    port = _free_port()
    events: list[Event] = []
    statuses: list[bool] = []

    async def on_event(e):
        events.append(e)

    async def on_status(s):
        statuses.append(s)

    conn = CoreConnection(
        f"ws://127.0.0.1:{port}/ws",
        "t",
        "pc",
        on_event,
        on_status,
        initial_backoff=0.1,
        max_backoff=0.2,
    )
    runner = asyncio.create_task(conn.run_forever())

    server, task = await _start_core(settings, port)
    await conn.wait_connected(5)
    assert (await conn.ask("ping")).text == "pong"

    await _stop_core(server, task)
    await asyncio.sleep(0.3)
    assert not conn.connected

    server, task = await _start_core(settings, port)
    await conn.wait_connected(5)
    assert (await conn.ask("ping")).text == "pong"
    assert statuses == [True, False, True]

    runner.cancel()
    await _stop_core(server, task)


async def test_wrong_token_raises(settings):
    port = _free_port()
    server, task = await _start_core(settings, port)
    conn = CoreConnection(f"ws://127.0.0.1:{port}/ws", "wrong", "pc", lambda e: None)
    with pytest.raises(AuthRejected):
        await asyncio.wait_for(conn.run_forever(), 5)
    await _stop_core(server, task)
