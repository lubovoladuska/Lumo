"""Управление компьютером. Навыки только описывают действие для LLM, выполняет ПК-клиент."""

from typing import Literal

from pydantic import BaseModel, Field

from lumo.core.skill_registry import SkillContext, skill


class AppParams(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
        description="Название приложения, как оно называется в Windows, по возможности "
        "по-английски: Telegram, Google Chrome, Notepad, Calculator. Для браузера по умолчанию — "
        "«браузер».",
    )


class SiteParams(BaseModel):
    site: str = Field(
        min_length=1, max_length=300, description="Название сайта (ютуб, вк) или адрес."
    )


class SearchParams(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="Что искать.")


class VolumeParams(BaseModel):
    action: Literal["set", "up", "down", "get", "mute", "unmute"] = Field(
        description="set — установить уровень, up/down — громче/тише, get — узнать, "
        "mute/unmute — выключить/включить звук."
    )
    level: int | None = Field(None, ge=0, le=100, description="Уровень в процентах для set.")
    step: int = Field(10, ge=1, le=100, description="На сколько процентов менять для up/down.")


class MediaParams(BaseModel):
    action: Literal["pause", "resume", "toggle", "next", "previous", "stop"] = Field(
        description="Управление тем, что сейчас играет: встроенная музыка или любой плеер."
    )


class PowerParams(BaseModel):
    action: Literal["shutdown", "restart", "sleep"]
    delay_minutes: int = Field(
        0, ge=0, le=24 * 60, description="Через сколько минут (для shutdown/restart)."
    )


_POWER_NAMES = {"shutdown": "выключить", "restart": "перезагрузить", "sleep": "усыпить"}


def _describe_power(params: PowerParams, ctx: SkillContext) -> str:
    when = f" через {params.delay_minutes} мин" if params.delay_minutes else " сейчас"
    if params.action == "sleep":
        when = ""
    return f"{_POWER_NAMES[params.action]} компьютер{when}"


@skill("pc_open_app", "Запустить приложение на компьютере.", AppParams)
async def pc_open_app(params: AppParams, ctx: SkillContext) -> str:
    return await ctx.device("pc.app_open", {"name": params.name})


@skill("pc_close_app", "Закрыть приложение на компьютере (как крестиком окна).", AppParams)
async def pc_close_app(params: AppParams, ctx: SkillContext) -> str:
    return await ctx.device("pc.app_close", {"name": params.name})


@skill("pc_open_site", "Открыть сайт в браузере.", SiteParams)
async def pc_open_site(params: SiteParams, ctx: SkillContext) -> str:
    return await ctx.device("pc.site_open", {"site": params.site})


@skill(
    "pc_web_search", "Найти что-то в интернете: открыть результаты поиска в браузере.", SearchParams
)
async def pc_web_search(params: SearchParams, ctx: SkillContext) -> str:
    return await ctx.device("pc.web_search", {"query": params.query})


@skill("pc_volume", "Системная громкость компьютера.", VolumeParams)
async def pc_volume(params: VolumeParams, ctx: SkillContext) -> str:
    if params.action == "set" and params.level is None:
        return "Ошибка: для set нужен level."
    return await ctx.device("pc.volume", params.model_dump())


@skill(
    "media_control",
    "Пауза, продолжить, следующий или предыдущий трек — для встроенной музыки или любого "
    "плеера на компьютере (видео в браузере и т. п.).",
    MediaParams,
)
async def media_control(params: MediaParams, ctx: SkillContext) -> str:
    return await ctx.device("pc.media", {"action": params.action})


@skill(
    "pc_power",
    "Выключить, перезагрузить или усыпить компьютер. Для блокировки и отмены выключения "
    "есть отдельные навыки.",
    PowerParams,
    confirm=True,
    describe=_describe_power,
)
async def pc_power(params: PowerParams, ctx: SkillContext) -> str:
    return await ctx.device("pc.power", params.model_dump())


@skill("pc_lock", "Заблокировать экран компьютера.")
async def pc_lock(params, ctx: SkillContext) -> str:
    return await ctx.device("pc.lock")


@skill("pc_cancel_shutdown", "Отменить запланированное выключение или перезагрузку.")
async def pc_cancel_shutdown(params, ctx: SkillContext) -> str:
    return await ctx.device("pc.power_cancel")


@skill("pc_minimize_all", "Свернуть все окна, показать рабочий стол.")
async def pc_minimize_all(params, ctx: SkillContext) -> str:
    return await ctx.device("pc.minimize_all")


@skill("pc_screenshot", "Сделать скриншот экрана и сохранить его.")
async def pc_screenshot(params, ctx: SkillContext) -> str:
    return await ctx.device("pc.screenshot")


@skill("pc_active_window", "Узнать, какое окно сейчас активно на компьютере.")
async def pc_active_window(params, ctx: SkillContext) -> str:
    return await ctx.device("pc.active_window")
