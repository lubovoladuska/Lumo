import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from lumo.config import Settings
from lumo.core.hub import ClientHub
from lumo.core.server import create_app
from lumo.protocol.messages import Event, Hello, Reply, Utterance, parse_server_message

TOKEN = "secret-token"


class EchoAssistant:
    async def handle_utterance(self, user_id, device_id, utterance):
        return Reply(id=utterance.id, text=f"echo: {utterance.text}")

    async def on_client_connected(self, conn):
        pass


@pytest.fixture
def hub():
    return ClientHub()


@pytest.fixture
def client(hub):
    settings = Settings(_env_file=None, token=TOKEN)
    with TestClient(create_app(settings, EchoAssistant(), hub)) as c:
        yield c


def _hello(ws, device_id="pc", token=TOKEN):
    ws.send_text(Hello(token=token, device_id=device_id).model_dump_json())


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"]


def test_connect_with_valid_token(client):
    with client.websocket_connect("/ws") as ws:
        _hello(ws)
        assert parse_server_message(ws.receive_text()).type == "welcome"


@pytest.mark.parametrize("token", ["wrong", ""])
def test_connect_with_invalid_token_rejected(client, token):
    with client.websocket_connect("/ws") as ws:
        _hello(ws, token=token)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4401


def test_reply_matches_utterance_id(client):
    with client.websocket_connect("/ws") as ws:
        _hello(ws)
        ws.receive_text()
        ws.send_text(Utterance(id="abc", text="привет").model_dump_json())
        reply = parse_server_message(ws.receive_text())
    assert reply.type == "reply"
    assert reply.id == "abc"
    assert reply.text == "echo: привет"


def test_event_broadcast_to_all_clients(client, hub):
    with client.websocket_connect("/ws") as ws1, client.websocket_connect("/ws") as ws2:
        _hello(ws1, "pc")
        _hello(ws2, "phone")
        ws1.receive_text()
        ws2.receive_text()
        event = Event(kind="timer", text="Таймер сработал")
        assert client.portal.call(hub.broadcast, "owner", event) == 2
        assert parse_server_message(ws1.receive_text()) == event
        assert parse_server_message(ws2.receive_text()) == event
