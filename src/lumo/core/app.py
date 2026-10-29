"""Сборка ядра: хранилище, навыки, LLM, планировщик и API."""

from fastapi import FastAPI

from lumo.config import Settings
from lumo.core.engine import ConversationEngine
from lumo.core.hub import ClientHub
from lumo.core.llm import LLMClient, OpenAICompatibleClient
from lumo.core.scheduler import Scheduler
from lumo.core.server import create_app
from lumo.core.skill_registry import SkillRegistry
from lumo.core.storage import Storage


def build_app(settings: Settings, llm: LLMClient | None = None) -> FastAPI:
    storage = Storage(settings.db_path)
    hub = ClientHub()
    registry = SkillRegistry(disabled=settings.disabled_skills)
    registry.discover()
    scheduler = Scheduler(storage, hub)
    llm = llm or OpenAICompatibleClient(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout=settings.llm_timeout,
    )
    engine = ConversationEngine(settings, storage, registry, llm, scheduler)

    async def shutdown() -> None:
        await scheduler.stop()
        storage.close()

    app = create_app(settings, engine, hub, on_startup=scheduler.start, on_shutdown=shutdown)
    app.state.engine = engine
    app.state.scheduler = scheduler
    return app
