import asyncio

import pytest
from fastapi.testclient import TestClient

from lumo.config import Settings
from lumo.core.devices import DeviceRouter
from lumo.core.hub import ClientHub, Connection
from lumo.core.server import create_app
from lumo.protocol.messages import (
    ActionRequest,
    ActionResult,
    Hello,
    Reply,
    Utterance,
    parse_client_message,
    parse_server_message,
)


class FakeSocket:
    """Клиент, который сам отвечает на запросы действий."""

    def __init__(self, router: DeviceRouter | None = None, reply: str | None = "готово") -> None:
        self.router = router
        self.reply = reply
        self.requests: list[ActionRequest] = []

    async def send_text(self, data: str) -> None:
        message = parse_server_message(data)
        if isinstance(message, ActionRequest):
            self.requests.append(message)
            if self.reply is not None and self.router is not None:
                result = ActionResult(id=message.id, ok=True, text=self.reply)
                asyncio.get_running_loop().call_soon(self.router.resolve, result)


def add(hub, router, device, caps, reply="готово"):
    sock = FakeSocket(router, reply)
    conn = Connection(sock, "owner", device, frozenset(caps))
    hub.add(conn)
    return conn, sock


@pytest.fixture
def router():
    return DeviceRouter(ClientHub(), timeout=0.3)


def test_hello_without_capabilities_is_compatible():
    hello = parse_client_message('{"type": "hello", "token": "t", "device_id": "old"}')
    assert hello.capabilities == []


def test_action_capability_prefix():
    assert ActionRequest(action="pc.volume_set").capability == "pc"


async def test_action_goes_to_capable_client(router):
    add(router.hub, router, "phone", [])
    _, pc = add(router.hub, router, "pc", ["pc"])
    outcome = await router.run("owner", "pc.volume_set", {"level": 30}, origin="phone")
    assert outcome.ok and outcome.text == "готово"
    assert pc.requests[0].params == {"level": 30}


async def test_origin_device_preferred(router):
    _, first = add(router.hub, router, "pc1", ["pc"])
    _, second = add(router.hub, router, "pc2", ["pc"])
    await router.run("owner", "pc.lock", origin="pc1")
    assert len(first.requests) == 1 and not second.requests
    await router.run("owner", "pc.lock", origin="phone")
    assert len(second.requests) == 1  # подключился последним


async def test_no_capable_client(router):
    add(router.hub, router, "phone", [])
    outcome = await router.run("owner", "pc.lock")
    assert not outcome.ok
    assert "Компьютер сейчас не подключён" in outcome.text


async def test_disconnected_client_is_forgotten(router):
    conn, _ = add(router.hub, router, "pc", ["pc"])
    router.hub.remove(conn)
    assert router.pick("owner", "pc") is None


async def test_timeout(router):
    add(router.hub, router, "pc", ["pc"], reply=None)
    outcome = await router.run("owner", "pc.lock")
    assert not outcome.ok and "не ответило" in outcome.text


async def test_disconnect_during_action(router):
    conn, _ = add(router.hub, router, "pc", ["pc"], reply=None)
    task = asyncio.create_task(router.run("owner", "pc.lock"))
    await asyncio.sleep(0.05)
    router.disconnected(conn)
    outcome = await task
    assert not outcome.ok and "потеряна" in outcome.text


def test_skill_waits_for_action_from_same_client_without_deadlock():
    """Реплика ждёт действие, которое выполняет этот же клиент через то же соединение."""
    hub = ClientHub()
    devices = DeviceRouter(hub, timeout=3)

    class Assistant:
        async def handle_utterance(self, user_id, device_id, utterance):
            outcome = await devices.run(user_id, "pc.volume_get", origin=device_id)
            return Reply(id=utterance.id, text=outcome.text)

        async def on_client_connected(self, conn):
            pass

    settings = Settings(_env_file=None, token="t")
    app = create_app(settings, Assistant(), hub, devices)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_text(Hello(token="t", device_id="pc", capabilities=["pc"]).model_dump_json())
        ws.receive_text()  # welcome
        ws.send_text(Utterance(id="u1", text="какая громкость").model_dump_json())
        request = parse_server_message(ws.receive_text())
        assert isinstance(request, ActionRequest)
        ws.send_text(ActionResult(id=request.id, ok=True, text="Громкость 40%").model_dump_json())
        reply = parse_server_message(ws.receive_text())
    assert reply.type == "reply" and reply.id == "u1"
    assert reply.text == "Громкость 40%"
