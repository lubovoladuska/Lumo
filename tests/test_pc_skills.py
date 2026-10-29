import json

import pytest

from lumo.core.skill_registry import SkillContext, SkillRegistry
from lumo.protocol.messages import ConfirmRequest, Reply
from tests.conftest import ScriptedLLM, text, tool
from tests.test_devices import add


@pytest.fixture
def pc(devices, hub):
    _, sock = add(hub, devices, "pc", ["pc"], reply="сделано")
    return sock


@pytest.fixture
def run(devices, storage, scheduler, settings, clock):
    registry = SkillRegistry()
    registry.discover()
    ctx = SkillContext("owner", "pc", storage, scheduler, settings, clock, devices)

    async def call(skill_name, **args):
        prepared = registry.prepare(skill_name, json.dumps(args))
        if isinstance(prepared, str):
            return prepared
        return await registry.run(prepared, ctx)

    return call


@pytest.mark.parametrize(
    ("skill", "args", "action", "params"),
    [
        ("pc_open_app", {"name": "Telegram"}, "pc.app_open", {"name": "Telegram"}),
        ("pc_close_app", {"name": "браузер"}, "pc.app_close", {"name": "браузер"}),
        ("pc_open_site", {"site": "ютуб"}, "pc.site_open", {"site": "ютуб"}),
        ("pc_web_search", {"query": "рецепт плова"}, "pc.web_search", {"query": "рецепт плова"}),
        ("media_control", {"action": "next"}, "pc.media", {"action": "next"}),
        ("pc_lock", {}, "pc.lock", {}),
        ("pc_cancel_shutdown", {}, "pc.power_cancel", {}),
        ("pc_minimize_all", {}, "pc.minimize_all", {}),
        ("pc_screenshot", {}, "pc.screenshot", {}),
        ("pc_active_window", {}, "pc.active_window", {}),
    ],
)
async def test_skill_routes_to_pc(run, pc, skill, args, action, params):
    assert await run(skill, **args) == "сделано"
    [request] = pc.requests
    assert request.action == action
    assert request.params == params


async def test_volume_bounds(run, pc):
    assert (await run("pc_volume", action="set", level=150)).startswith("Ошибка")
    assert (await run("pc_volume", action="set")).startswith("Ошибка")
    assert not pc.requests
    assert await run("pc_volume", action="set", level=30) == "сделано"
    assert pc.requests[0].params == {"action": "set", "level": 30, "step": 10}


async def test_pc_not_connected(run):
    result = await run("pc_lock")
    assert result.startswith("Ошибка") and "не подключён" in result


async def test_power_requires_confirmation(make_engine, pc):
    from lumo.protocol.messages import Utterance

    llm = ScriptedLLM(
        tool("pc_power", json.dumps({"action": "shutdown", "delay_minutes": 60})),
        text("Компьютер выключится через час."),
    )
    engine = make_engine(llm)
    question = await engine.handle_utterance(
        "owner", "pc", Utterance(text="выключи комп через час")
    )
    assert isinstance(question, ConfirmRequest)
    assert "выключить компьютер через 60 мин" in question.text
    assert not pc.requests  # до «да» ничего не отправлено

    reply = await engine.handle_utterance("owner", "pc", Utterance(text="да"))
    assert isinstance(reply, Reply)
    assert pc.requests[0].action == "pc.power"
    assert pc.requests[0].params == {"action": "shutdown", "delay_minutes": 60}


async def test_lock_has_no_confirmation(make_engine, pc):
    from lumo.protocol.messages import Utterance

    engine = make_engine(ScriptedLLM(tool("pc_lock"), text("Заблокировал.")))
    reply = await engine.handle_utterance("owner", "pc", Utterance(text="заблокируй"))
    assert isinstance(reply, Reply)
    assert pc.requests[0].action == "pc.lock"


@pytest.mark.parametrize(
    ("args", "action", "params"),
    [
        ({"source": "search", "query": "Кино"}, "music.play", {"query": "Кино"}),
        ({"source": "wave"}, "music.wave", {}),
        ({"source": "liked"}, "music.liked", {}),
    ],
)
async def test_music_play_routes(run, devices, hub, args, action, params):
    _, player = add(hub, devices, "pc", ["music"], reply="играет")
    assert await run("music_play", **args) == "играет"
    assert player.requests[0].action == action
    assert player.requests[0].params == params


async def test_music_search_needs_query(run):
    assert (await run("music_play", source="search")).startswith("Ошибка")


async def test_music_without_token_explains_where_to_put_it(settings, storage, scheduler, clock):
    """Сквозной путь: навык → ядро → клиент без токена → подсказка."""
    from lumo.core.devices import DeviceRouter
    from lumo.core.hub import ClientHub
    from lumo.desktop.actions import ActionExecutor
    from lumo.desktop.actions.music import NO_TOKEN, MusicActions, YandexMusic
    from lumo.desktop.player import MusicPlayer
    from lumo.protocol.messages import ActionRequest

    executor = ActionExecutor()
    MusicActions(YandexMusic(""), MusicPlayer(None), engine=None).register(executor)

    class ClientSocket:
        def __init__(self, router):
            self.router = router

        async def send_text(self, data):
            from lumo.protocol.messages import parse_server_message

            request = parse_server_message(data)
            assert isinstance(request, ActionRequest)
            self.router.resolve(await executor.execute(request))

    hub = ClientHub()
    router = DeviceRouter(hub, timeout=1)
    from lumo.core.hub import Connection

    hub.add(Connection(ClientSocket(router), "owner", "pc", frozenset(executor.capabilities)))
    registry = SkillRegistry()
    registry.discover()
    ctx = SkillContext("owner", "pc", storage, scheduler, settings, clock, router)
    result = await registry.run(registry.prepare("music_play", '{"source": "wave"}'), ctx)
    assert result == f"Ошибка: {NO_TOKEN}"
