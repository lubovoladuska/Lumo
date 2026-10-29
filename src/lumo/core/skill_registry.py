"""Система навыков: объявление, обнаружение, проверка параметров и исполнение.

Навык — асинхронная функция ``(params, ctx) -> str | dict``, помеченная декоратором
``@skill``. Параметры описываются Pydantic-моделью, из неё строится JSON Schema для LLM.
"""

import importlib
import inspect
import json
import logging
import pkgutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from types import ModuleType
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from lumo.config import Settings
    from lumo.core.devices import DeviceRouter
    from lumo.core.scheduler import Scheduler
    from lumo.core.storage import Storage

log = logging.getLogger(__name__)

SKILL_ATTR = "__lumo_skill__"


class NoParams(BaseModel):
    pass


@dataclass
class SkillContext:
    user_id: str
    device_id: str
    storage: "Storage"
    scheduler: "Scheduler"
    settings: "Settings"
    now: Callable[[], datetime]
    devices: "DeviceRouter | None" = None

    async def device(self, action: str, params: dict | None = None) -> str:
        """Выполняет действие на устройстве и возвращает текст для LLM."""
        if self.devices is None:
            return "Ошибка: устройства недоступны."
        outcome = await self.devices.run(self.user_id, action, params, origin=self.device_id)
        return outcome.text if outcome.ok else f"Ошибка: {outcome.text}"


SkillFunc = Callable[[Any, SkillContext], Awaitable[str | dict]]


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    params: type[BaseModel]
    func: SkillFunc
    confirm: bool
    # Как озвучить суть действия при запросе подтверждения.
    describe: Callable[[Any, SkillContext], str] | None
    module: str

    def tool_schema(self) -> dict:
        schema = self.params.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": schema},
        }


def skill(
    name: str,
    description: str,
    params: type[BaseModel] = NoParams,
    confirm: bool = False,
    describe: Callable[[Any, SkillContext], str] | None = None,
) -> Callable[[SkillFunc], SkillFunc]:
    def decorator(func: SkillFunc) -> SkillFunc:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(f"Навык {name} должен быть async-функцией")
        spec = Skill(name, description, params, func, confirm, describe, func.__module__)
        setattr(func, SKILL_ATTR, spec)
        return func

    return decorator


class DuplicateSkillError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedCall:
    skill: Skill
    params: BaseModel


class SkillRegistry:
    def __init__(self, disabled: list[str] | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        self._disabled = set(disabled or [])

    # --- регистрация ---

    def register(self, spec: Skill) -> None:
        existing = self._skills.get(spec.name)
        if existing is not None:
            raise DuplicateSkillError(
                f"Навык «{spec.name}» объявлен дважды: {existing.module} и {spec.module}"
            )
        self._skills[spec.name] = spec

    def register_module(self, module: ModuleType) -> None:
        for obj in vars(module).values():
            spec = getattr(obj, SKILL_ATTR, None)
            if isinstance(spec, Skill) and spec.module == module.__name__:
                self.register(spec)

    def discover(self, package: str = "lumo.core.skills") -> None:
        """Импортирует все модули пакета и регистрирует найденные в них навыки."""
        pkg = importlib.import_module(package)
        for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
            if info.name.startswith("_"):
                continue
            self.register_module(importlib.import_module(f"{package}.{info.name}"))
        log.info("Навыки: %s", ", ".join(s.name for s in self.enabled()) or "нет")

    # --- доступ ---

    def enabled(self) -> list[Skill]:
        return [s for name, s in self._skills.items() if name not in self._disabled]

    def get(self, name: str) -> Skill | None:
        if name in self._disabled:
            return None
        return self._skills.get(name)

    def tools(self) -> list[dict]:
        return [s.tool_schema() for s in self.enabled()]

    # --- исполнение ---

    def prepare(self, name: str, raw_arguments: str | dict | None) -> PreparedCall | str:
        """Проверяет вызов. Возвращает подготовленный вызов или текст ошибки для LLM."""
        spec = self.get(name)
        if spec is None:
            return f"Ошибка: навык «{name}» не существует или отключён."
        try:
            args = (
                json.loads(raw_arguments or "{}")
                if isinstance(raw_arguments, str)
                else (raw_arguments or {})
            )
            params = spec.params.model_validate(args)
        except (json.JSONDecodeError, ValidationError) as exc:
            return f"Ошибка: недопустимые параметры для «{name}»: {_short_error(exc)}"
        return PreparedCall(spec, params)

    async def run(self, call: PreparedCall, ctx: SkillContext) -> str:
        """Исполняет навык. Любая ошибка превращается в текст для LLM, ядро не падает."""
        try:
            result = await call.skill.func(call.params, ctx)
        except Exception:
            log.exception("Навык %s завершился с ошибкой", call.skill.name)
            return "Ошибка: действие не удалось выполнить из-за внутренней ошибки навыка."
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'параметры'}: {e['msg']}" for e in exc.errors()
        )
    return str(exc)
