"""Сквозные тесты ядра через WebSocket с фейковой LLM."""

import json

from fastapi.testclient import TestClient

from lumo.core.app import build_app
from lumo.protocol.messages import Hello, Utterance, parse_server_message
from tests.conftest import ScriptedLLM, text, tool


def _session(client, device="pc"):
    ws = client.websocket_connect("/ws")
    return ws


def _hello(ws, device="pc"):
    ws.send_text(Hello(token="t", device_id=device).model_dump_json())
    assert parse_server_message(ws.receive_text()).type == "welcome"


def test_utterance_to_reply_over_websocket(settings):
    llm = ScriptedLLM(text("Здравствуйте, я Вальтер."))
    with TestClient(build_app(settings, llm)) as client, client.websocket_connect("/ws") as ws:
        _hello(ws)
        ws.send_text(Utterance(id="q1", text="Как тебя зовут?").model_dump_json())
        reply = parse_server_message(ws.receive_text())
    assert reply.type == "reply" and reply.id == "q1"
    assert "Вальтер" in reply.text


def test_confirmation_flow_over_websocket(settings):
    llm = ScriptedLLM(
        tool("remember_fact", json.dumps({"text": "Любимый цвет — синий"})),
        # «запомни» отвечает коротко без второго запроса к LLM
        tool("forget_fact", json.dumps({"fact_id": 1})),
        text("Готово, забыла."),
    )
    with TestClient(build_app(settings, llm)) as client, client.websocket_connect("/ws") as ws:
        _hello(ws)
        for utterance, expected_type in [
            ("Запомни, что мой любимый цвет синий", "reply"),
            ("Забудь мой любимый цвет", "confirm_request"),
            ("да", "reply"),
        ]:
            ws.send_text(Utterance(text=utterance).model_dump_json())
            assert parse_server_message(ws.receive_text()).type == expected_type
        engine = client.app.state.engine
        assert engine.storage.list_facts("owner") == []


def test_fact_survives_core_restart(settings):
    llm = ScriptedLLM(tool("remember_fact", json.dumps({"text": "Кота зовут Барсик"})), text("ok"))
    with TestClient(build_app(settings, llm)) as client, client.websocket_connect("/ws") as ws:
        _hello(ws)
        ws.send_text(Utterance(text="Запомни, что кота зовут Барсик").model_dump_json())
        ws.receive_text()

    llm2 = ScriptedLLM(text("Барсик"))
    with TestClient(build_app(settings, llm2)) as client, client.websocket_connect("/ws") as ws:
        _hello(ws)
        ws.send_text(Utterance(text="Как зовут кота?").model_dump_json())
        ws.receive_text()
    assert "Кота зовут Барсик" in llm2.calls[0][-2]["content"]


def test_missed_reminder_delivered_on_connect(settings):
    from datetime import timedelta

    from lumo.core.storage import Storage, utcnow

    storage = Storage(settings.db_path)
    storage.add_pending_event(
        "owner", "reminder", "Напоминание: позвонить маме", utcnow() - timedelta(minutes=10)
    )
    storage.close()

    with (
        TestClient(build_app(settings, ScriptedLLM(text("x")))) as client,
        client.websocket_connect("/ws") as ws,
    ):
        _hello(ws)
        event = parse_server_message(ws.receive_text())
    assert event.type == "event"
    assert event.missed
    assert event.text == "Напоминание: позвонить маме"
