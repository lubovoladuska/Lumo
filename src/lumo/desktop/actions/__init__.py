"""Действия, которые ядро может попросить выполнить на этом устройстве."""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from lumo.protocol.messages import ActionRequest, ActionResult

log = logging.getLogger(__name__)

Handler = Callable[..., str | Awaitable[str]]


class ActionError(Exception):
    """Ожидаемая ошибка действия: её текст уходит пользователю как есть."""


class ActionExecutor:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def register(self, action: str, handler: Handler) -> None:
        self._handlers[action] = handler

    def register_all(self, prefix: str, handlers: dict[str, Handler]) -> None:
        for name, handler in handlers.items():
            self.register(f"{prefix}.{name}", handler)

    @property
    def capabilities(self) -> list[str]:
        return sorted({name.split(".", 1)[0] for name in self._handlers})

    async def execute(self, request: ActionRequest) -> ActionResult:
        handler = self._handlers.get(request.action)
        if handler is None:
            return ActionResult(
                id=request.id, ok=False, text=f"Неизвестное действие {request.action}"
            )
        try:
            result = await _call(handler, request.params)
        except ActionError as exc:
            return ActionResult(id=request.id, ok=False, text=str(exc))
        except Exception:
            log.exception("Действие %s завершилось с ошибкой", request.action)
            return ActionResult(id=request.id, ok=False, text="Внутренняя ошибка на устройстве.")
        return ActionResult(id=request.id, ok=True, text=result)


async def _call(handler: Handler, params: dict[str, Any]) -> str:
    # Синхронные обработчики (вызовы Windows API) уходят в поток, чтобы не стопорить звук.
    if inspect.iscoroutinefunction(handler):
        return await handler(**params)
    return await asyncio.to_thread(handler, **params)
