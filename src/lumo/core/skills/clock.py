from lumo.core.skill_registry import SkillContext, skill
from lumo.core.skills._format import MONTHS, WEEKDAYS


@skill("get_datetime", "Узнать текущие дату, день недели и время у пользователя.")
async def get_datetime(params, ctx: SkillContext) -> dict:
    now = ctx.now()
    return {
        "weekday": WEEKDAYS[now.weekday()],
        "date": f"{now.day} {MONTHS[now.month - 1]} {now.year}",
        "time": now.strftime("%H:%M"),
        "timezone": ctx.settings.timezone,
    }
