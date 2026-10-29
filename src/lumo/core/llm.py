"""Клиент языковой модели. DeepSeek подключается через OpenAI-совместимый API."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Literal, Protocol

log = logging.getLogger(__name__)

ErrorKind = Literal["network", "auth", "timeout", "other"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMError(Exception):
    def __init__(self, kind: ErrorKind, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind


class LLMClient(Protocol):
    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


class OpenAICompatibleClient:
    """Работает с DeepSeek и любым другим OpenAI-совместимым API."""

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 30.0) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key or "missing", base_url=base_url, timeout=timeout, max_retries=1
        )
        self._model = model
        self._timeout = timeout

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        import openai

        try:
            response = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    tools=tools or openai.NOT_GIVEN,
                    temperature=0.6,
                ),
                timeout=self._timeout,
            )
        except (TimeoutError, openai.APITimeoutError) as exc:
            raise LLMError("timeout") from exc
        except openai.AuthenticationError as exc:
            raise LLMError("auth", "неверный ключ API") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("network", type(exc).__name__) from exc
        except openai.APIError as exc:
            raise LLMError("other", type(exc).__name__) from exc

        usage = response.usage
        if usage is not None:
            # prompt_cache_hit_tokens — поле DeepSeek: сколько входа взято из кэша (в разы дешевле)
            log.info(
                "Токены: вход %s (из кэша %s), выход %s",
                usage.prompt_tokens,
                getattr(usage, "prompt_cache_hit_tokens", "?"),
                usage.completion_tokens,
            )
        message = response.choices[0].message
        calls = [
            ToolCall(c.id, c.function.name, c.function.arguments or "{}")
            for c in (message.tool_calls or [])
        ]
        return LLMResponse(message.content, calls)
