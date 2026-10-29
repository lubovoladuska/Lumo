"""Подтверждение опасных действий.

Решение принимает ядро по словарю, а не LLM, чтобы модель не могла сама
подтвердить действие за пользователя.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from lumo.core.skill_registry import PreparedCall

CONFIRM_TTL = timedelta(minutes=2)

_AFFIRMATIVE = {
    "да",
    "давай",
    "подтверждаю",
    "ага",
    "угу",
    "конечно",
    "ок",
    "окей",
    "хорошо",
    "согласен",
    "согласна",
    "верно",
    "точно",
    "выполняй",
    "делай",
    "удаляй",
    "yes",
}
_NEGATIVE = {"нет", "не", "отмена", "отмени", "отбой", "стоп", "неа", "no"}


class Answer(Enum):
    YES = "yes"
    NO = "no"
    OTHER = "other"


def classify_answer(text: str) -> Answer:
    words = re.findall(r"[a-zа-яё]+", text.lower())
    if not words:
        return Answer.OTHER
    if any(w in _NEGATIVE for w in words):
        return Answer.NO
    if words[0] in _AFFIRMATIVE and len(words) <= 4:
        return Answer.YES
    return Answer.OTHER


@dataclass
class PendingConfirmation:
    call: PreparedCall
    question: str
    created_at: datetime
    # Состояние диалога с LLM, чтобы продолжить его после ответа пользователя.
    state: dict[str, Any] = field(default_factory=dict)

    def expired(self, now: datetime) -> bool:
        return now - self.created_at > CONFIRM_TTL


class ConfirmationManager:
    def __init__(self) -> None:
        self._pending: dict[str, PendingConfirmation] = {}

    def put(self, user_id: str, pending: PendingConfirmation) -> None:
        self._pending[user_id] = pending

    def pop(self, user_id: str) -> PendingConfirmation | None:
        return self._pending.pop(user_id, None)

    def has(self, user_id: str) -> bool:
        return user_id in self._pending
