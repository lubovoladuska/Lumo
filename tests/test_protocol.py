from datetime import datetime

import pytest
from pydantic import ValidationError

from lumo.protocol.messages import (
    ConfirmRequest,
    Event,
    Hello,
    Reply,
    Utterance,
    parse_client_message,
    parse_server_message,
)


def test_client_messages_roundtrip():
    hello = Hello(token="t", device_id="pc")
    assert parse_client_message(hello.model_dump_json()) == hello
    utt = Utterance(id="abc", text="Привет")
    assert parse_client_message(utt.model_dump_json()) == utt


def test_utterance_gets_generated_id():
    assert len(Utterance(text="x").id) == 12


def test_server_messages_roundtrip():
    for msg in (
        Reply(id="abc", text="Ответ"),
        ConfirmRequest(id="abc", text="Удалить?"),
        Event(kind="timer", text="Таймер", scheduled_for=datetime(2026, 1, 1, 9)),
    ):
        assert parse_server_message(msg.model_dump_json()) == msg


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):
        parse_client_message('{"type": "hack", "text": "x"}')
