"""Сообщения протокола ядро ↔ клиент (JSON поверх WebSocket).

Каждое сообщение — объект с полем ``type``. Ядро работает только с текстом:
звук обрабатывают клиенты.
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, TypeAdapter


def _new_id() -> str:
    return uuid4().hex[:12]


class Hello(BaseModel):
    """Клиент → ядро: первое сообщение после подключения."""

    type: Literal["hello"] = "hello"
    token: str
    device_id: str
    client_version: str = ""


class Welcome(BaseModel):
    """Ядро → клиент: подключение принято."""

    type: Literal["welcome"] = "welcome"
    core_version: str
    assistant_name: str


class Utterance(BaseModel):
    """Клиент → ядро: реплика пользователя."""

    type: Literal["utterance"] = "utterance"
    id: str = Field(default_factory=_new_id)
    text: str


class Reply(BaseModel):
    """Ядро → клиент: ответ ассистента на реплику ``id``."""

    type: Literal["reply"] = "reply"
    id: str
    text: str
    expects_response: bool = False


class ConfirmRequest(BaseModel):
    """Ядро → клиент: навык ждёт согласия пользователя. Ответ присылается обычной репликой."""

    type: Literal["confirm_request"] = "confirm_request"
    id: str
    text: str


class Event(BaseModel):
    """Ядро → клиент: событие без запроса клиента (например, сработал таймер)."""

    type: Literal["event"] = "event"
    kind: str
    text: str
    scheduled_for: datetime | None = None
    missed: bool = False


class Error(BaseModel):
    type: Literal["error"] = "error"
    id: str | None = None
    message: str


ClientMessage = Annotated[Hello | Utterance, Field(discriminator="type")]
ServerMessage = Annotated[
    Welcome | Reply | ConfirmRequest | Event | Error, Field(discriminator="type")
]

client_message_adapter: TypeAdapter[Hello | Utterance] = TypeAdapter(ClientMessage)
server_message_adapter: TypeAdapter[Welcome | Reply | ConfirmRequest | Event | Error] = TypeAdapter(
    ServerMessage
)


def parse_client_message(raw: str) -> Hello | Utterance:
    return client_message_adapter.validate_json(raw)


def parse_server_message(raw: str) -> Welcome | Reply | ConfirmRequest | Event | Error:
    return server_message_adapter.validate_json(raw)
