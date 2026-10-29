"""Сообщения протокола ядро ↔ клиент (JSON поверх WebSocket).

Каждое сообщение — объект с полем ``type``. Ядро работает только с текстом:
звук обрабатывают клиенты.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
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
    # Что умеет делать устройство: «pc», «music»… Ядро шлёт действия только туда, где они есть.
    capabilities: list[str] = Field(default_factory=list)


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
    # Тихий ответ: клиент не произносит текст, а играет звук подтверждения.
    silent: bool = False


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


class ActionRequest(BaseModel):
    """Ядро → клиент: выполнить действие на устройстве (``pc.volume_set``, ``music.play``…)."""

    type: Literal["action_request"] = "action_request"
    id: str = Field(default_factory=_new_id)
    action: str
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def capability(self) -> str:
        return self.action.split(".", 1)[0]


class ActionResult(BaseModel):
    """Клиент → ядро: итог действия ``id``."""

    type: Literal["action_result"] = "action_result"
    id: str
    ok: bool
    text: str = ""


class Error(BaseModel):
    type: Literal["error"] = "error"
    id: str | None = None
    message: str


AnyClientMessage = Hello | Utterance | ActionResult
AnyServerMessage = Welcome | Reply | ConfirmRequest | Event | ActionRequest | Error

ClientMessage = Annotated[AnyClientMessage, Field(discriminator="type")]
ServerMessage = Annotated[AnyServerMessage, Field(discriminator="type")]

client_message_adapter: TypeAdapter[AnyClientMessage] = TypeAdapter(ClientMessage)
server_message_adapter: TypeAdapter[AnyServerMessage] = TypeAdapter(ServerMessage)


def parse_client_message(raw: str) -> AnyClientMessage:
    return client_message_adapter.validate_json(raw)


def parse_server_message(raw: str) -> AnyServerMessage:
    return server_message_adapter.validate_json(raw)
