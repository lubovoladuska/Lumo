from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from lumo.config import Settings
from lumo.core.engine import ConversationEngine
from lumo.core.hub import ClientHub
from lumo.core.llm import LLMResponse, ToolCall
from lumo.core.scheduler import Scheduler
from lumo.core.skill_registry import SkillRegistry
from lumo.core.storage import Storage


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        from datetime import timedelta

        self.now += timedelta(**kwargs)


class ScriptedLLM:
    """Фейковая LLM: отвечает по очереди заданными ответами (или вызывает функцию)."""

    def __init__(self, *responses: LLMResponse | Exception | Callable) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict]] = []

    async def complete(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if callable(item) and not isinstance(item, LLMResponse):
            item = item(messages, tools)
        if isinstance(item, Exception):
            raise item
        return item


def text(content: str) -> LLMResponse:
    return LLMResponse(content)


def tool(name: str, arguments: str = "{}", call_id: str = "c1") -> LLMResponse:
    return LLMResponse(None, [ToolCall(call_id, name, arguments)])


@pytest.fixture
def clock():
    # 29.10.2026 12:00 по Москве
    return FakeClock(datetime(2026, 10, 29, 9, 0, tzinfo=UTC))


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, timezone="Europe/Moscow", token="t")


@pytest.fixture
def storage(settings):
    s = Storage(settings.db_path)
    yield s
    s.close()


@pytest.fixture
def hub():
    return ClientHub()


@pytest.fixture
def scheduler(storage, hub, clock):
    return Scheduler(storage, hub, clock)


@pytest.fixture
def make_engine(settings, storage, scheduler, clock):
    def factory(llm, disabled=None):
        registry = SkillRegistry(disabled=disabled)
        registry.discover()
        return ConversationEngine(settings, storage, registry, llm, scheduler, clock)

    return factory
