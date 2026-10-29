from lumo.core.skill_registry import skill


@skill("same", "b")
async def same(params, ctx):
    return "b"
