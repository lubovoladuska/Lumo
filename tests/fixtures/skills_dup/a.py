from lumo.core.skill_registry import skill


@skill("same", "a")
async def same(params, ctx):
    return "a"
