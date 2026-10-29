from pydantic import BaseModel, Field

from lumo.core.skill_registry import SkillContext, skill


class RememberParams(BaseModel):
    text: str = Field(
        min_length=2,
        max_length=500,
        description="Факт о пользователе одной фразой от третьего лица, "
        "например «Любимый цвет — синий».",
    )


class ForgetParams(BaseModel):
    fact_id: int = Field(description="Номер факта из списка известных фактов.")


@skill(
    "remember_fact",
    "Сохранить факт о пользователе в долговременную память по его просьбе.",
    RememberParams,
)
async def remember_fact(params: RememberParams, ctx: SkillContext) -> str:
    fact = ctx.storage.add_fact(ctx.user_id, params.text.strip())
    return f"Сохранено под номером {fact.id}."


def _describe_forget(params: ForgetParams, ctx: SkillContext) -> str:
    fact = ctx.storage.get_fact(ctx.user_id, params.fact_id)
    return f"забыть «{fact.text}»" if fact else "забыть факт"


@skill(
    "forget_fact",
    "Удалить факт о пользователе из памяти по его просьбе.",
    ForgetParams,
    confirm=True,
    describe=_describe_forget,
)
async def forget_fact(params: ForgetParams, ctx: SkillContext) -> str:
    if ctx.storage.delete_fact(ctx.user_id, params.fact_id):
        return "Факт удалён."
    return "Ошибка: факт с таким номером не найден."
