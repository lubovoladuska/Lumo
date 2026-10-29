"""Настройки поведения ассистента голосом: например, как отвечать на команды."""

from typing import Literal

from pydantic import BaseModel, Field

from lumo.core.skill_registry import SkillContext, skill

STYLE_NAMES = {"silent": "молча", "short": "коротко", "normal": "полностью"}


class ReplyStyleParams(BaseModel):
    skill: str = Field(
        description="Имя навыка-инструмента, к которому относится команда пользователя: "
        "например pc_volume для громкости, pc_screenshot для скриншотов, media_control для "
        "паузы и треков."
    )
    style: Literal["silent", "short", "normal", "default"] = Field(
        description="silent — молча (только звук), short — коротко («Готово»), "
        "normal — полностью, default — вернуть как было по умолчанию."
    )


@skill(
    "set_reply_style",
    "Изменить, как ассистент отвечает на команду: «на скриншоты отвечай молча», "
    "«на громкость отвечай коротко», «верни ответы на громкость как было».",
    ReplyStyleParams,
    reply="short",
)
async def set_reply_style(params: ReplyStyleParams, ctx: SkillContext) -> str:
    if params.skill not in ctx.skill_names or params.skill == "set_reply_style":
        return f"Ошибка: нет навыка {params.skill}."
    value = None if params.style == "default" else params.style
    ctx.storage.set_preference(ctx.user_id, f"reply:{params.skill}", value)
    if value is None:
        return f"Для {params.skill} снова режим по умолчанию."
    return f"Теперь на {params.skill} отвечаю {STYLE_NAMES[value]}."
