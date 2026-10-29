from pydantic import BaseModel, Field

from lumo.core.skill_registry import skill


class GreetParams(BaseModel):
    name: str = Field(description="Имя")


@skill("greet", "Поздороваться", GreetParams)
async def greet(params, ctx):
    return f"Привет, {params.name}"


@skill("boom", "Всегда падает")
async def boom(params, ctx):
    raise RuntimeError("boom")


@skill("wipe", "Опасное действие", confirm=True, describe=lambda p, c: "стереть всё")
async def wipe(params, ctx):
    return {"wiped": True}
