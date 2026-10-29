"""Музыка из Яндекс Музыки. Играет встроенный плеер ПК-клиента.

Пауза, продолжить и переключение треков — навык ``media_control`` (см. pc.py): он сам
понимает, управлять встроенной музыкой или любым другим плеером.
"""

from typing import Literal

from pydantic import BaseModel, Field

from lumo.core.skill_registry import SkillContext, skill


class PlayParams(BaseModel):
    source: Literal["search", "wave", "liked"] = Field(
        "search",
        description="search — найти и включить по запросу; wave — «Моя волна»; "
        "liked — любимые треки («Мне нравится»). Просьба «включи музыку» без уточнений — wave.",
    )
    query: str | None = Field(
        None,
        max_length=200,
        description="Для search: трек, исполнитель, альбом или плейлист, например «Кино» "
        "или «Bohemian Rhapsody».",
    )


class MusicVolumeParams(BaseModel):
    action: Literal["set", "up", "down", "get"]
    level: int | None = Field(None, ge=0, le=100, description="Уровень в процентах для set.")
    step: int = Field(10, ge=1, le=100)


@skill("music_play", "Включить музыку из Яндекс Музыки.", PlayParams)
async def music_play(params: PlayParams, ctx: SkillContext) -> str:
    if params.source == "search":
        if not params.query:
            return "Ошибка: для search нужен query."
        return await ctx.device("music.play", {"query": params.query})
    return await ctx.device(f"music.{params.source}")


@skill("music_now_playing", "Узнать, что сейчас играет во встроенном плеере.")
async def music_now_playing(params, ctx: SkillContext) -> str:
    return await ctx.device("music.now_playing")


@skill(
    "music_volume",
    "Громкость только встроенной музыки внутри ассистента (не ползунок Windows). Вызывай, "
    "только если пользователь прямо говорит про громкость МУЗЫКИ («музыку тише», «громкость "
    "музыки на 30»). Обычное «громкость на 50» — это pc_volume.",
    MusicVolumeParams,
)
async def music_volume(params: MusicVolumeParams, ctx: SkillContext) -> str:
    return await ctx.device("music.volume", params.model_dump())


@skill("music_like", "Добавить играющий трек в «Мне нравится».")
async def music_like(params, ctx: SkillContext) -> str:
    return await ctx.device("music.like")
