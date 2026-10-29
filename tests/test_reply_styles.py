import json

import pytest

from lumo.core.engine import SHORT_REPLIES, SILENT_TEXT, direct_reply
from lumo.core.skill_registry import SkillRegistry
from lumo.core.storage import Storage
from lumo.protocol.messages import Reply, Utterance
from tests.conftest import ScriptedLLM, text, tool
from tests.test_devices import add


async def ask(engine, phrase):
    return await engine.handle_utterance("owner", "pc", Utterance(text=phrase))


@pytest.fixture
def pc(devices, hub):
    _, sock = add(hub, devices, "pc", ["pc", "music"], reply="ок")
    return sock


@pytest.fixture
def registry():
    reg = SkillRegistry()
    reg.discover()
    return reg


@pytest.mark.parametrize(
    ("skill", "args", "style"),
    [
        ("pc_volume", {"action": "set", "level": 30}, "silent"),
        ("pc_volume", {"action": "get"}, "normal"),
        ("media_control", {"action": "pause"}, "silent"),
        ("music_volume", {"action": "down"}, "silent"),
        ("music_volume", {"action": "get"}, "normal"),
        ("pc_minimize_all", {}, "silent"),
        ("pc_lock", {}, "silent"),
        ("pc_open_app", {"name": "Telegram"}, "short"),
        ("pc_screenshot", {}, "short"),
        ("remember_fact", {"text": "Кот Барсик"}, "short"),
        ("set_timer", {"duration_seconds": 60}, "short"),
        ("get_datetime", {}, "normal"),
        ("music_play", {"source": "wave"}, "normal"),
        ("pc_power", {"action": "shutdown"}, "normal"),
    ],
)
def test_default_styles(registry, skill, args, style):
    prepared = registry.prepare(skill, json.dumps(args))
    assert prepared.skill.reply_style(prepared.params) == style


def test_direct_reply_rules():
    ok = "готово"
    assert direct_reply([{"style": "silent", "result": ok}]).silent
    short = direct_reply([{"style": "silent", "result": ok}, {"style": "short", "result": ok}])
    assert not short.silent and short.text in SHORT_REPLIES
    assert (
        direct_reply([{"style": "silent", "result": ok}, {"style": "normal", "result": ok}]) is None
    )
    assert direct_reply([{"style": "silent", "result": "Ошибка: нет связи"}]) is None
    assert direct_reply([]) is None


async def test_volume_is_silent_without_second_llm_call(make_engine, pc):
    llm = ScriptedLLM(tool("pc_volume", json.dumps({"action": "set", "level": 30})), text("unused"))
    reply = await ask(make_engine(llm), "громкость на 30")
    assert isinstance(reply, Reply) and reply.silent and reply.text == SILENT_TEXT
    assert len(llm.calls) == 1  # второго запроса к модели не было
    assert pc.requests[0].action == "pc.volume"


async def test_screenshot_is_short(make_engine, pc):
    llm = ScriptedLLM(tool("pc_screenshot"), text("unused"))
    reply = await ask(make_engine(llm), "сделай скриншот")
    assert not reply.silent and reply.text in SHORT_REPLIES
    assert len(llm.calls) == 1


async def test_volume_question_gets_full_answer(make_engine, pc):
    llm = ScriptedLLM(tool("pc_volume", json.dumps({"action": "get"})), text("Громкость 40%."))
    reply = await ask(make_engine(llm), "какая громкость?")
    assert reply.text == "Громкость 40%." and not reply.silent
    assert len(llm.calls) == 2


async def test_silent_plus_information_is_full(make_engine, pc):
    from lumo.core.llm import LLMResponse, ToolCall

    both = LLMResponse(
        None,
        [
            ToolCall("a", "pc_volume", json.dumps({"action": "set", "level": 20})),
            ToolCall("b", "get_datetime", "{}"),
        ],
    )
    llm = ScriptedLLM(both, text("Громкость 20, сейчас 12:00."))
    reply = await ask(make_engine(llm), "громкость 20 и который час")
    assert reply.text == "Громкость 20, сейчас 12:00."


async def test_errors_are_explained_even_in_silent_mode(make_engine):
    llm = ScriptedLLM(
        tool("pc_volume", json.dumps({"action": "set", "level": 30})),
        text("Компьютер сейчас не подключён."),
    )
    reply = await ask(make_engine(llm), "громкость на 30")  # ПК-клиента нет
    assert reply.text == "Компьютер сейчас не подключён." and not reply.silent


async def test_voice_setting_changes_style_and_survives_restart(make_engine, pc, settings):
    llm = ScriptedLLM(
        tool("set_reply_style", json.dumps({"skill": "pc_screenshot", "style": "silent"})),
        tool("pc_screenshot"),
    )
    engine = make_engine(llm)
    assert (await ask(engine, "на скриншоты отвечай молча")).text in SHORT_REPLIES
    assert (await ask(engine, "сделай скриншот")).silent

    reopened = Storage(settings.db_path)
    assert reopened.get_preference("owner", "reply:pc_screenshot") == "silent"
    reopened.close()


async def test_voice_setting_reset_to_default(make_engine, pc, storage):
    storage.set_preference("owner", "reply:pc_screenshot", "silent")
    llm = ScriptedLLM(
        tool("set_reply_style", json.dumps({"skill": "pc_screenshot", "style": "default"})),
        tool("pc_screenshot"),
    )
    engine = make_engine(llm)
    await ask(engine, "на скриншоты отвечай как раньше")
    assert storage.get_preference("owner", "reply:pc_screenshot") is None
    assert not (await ask(engine, "сделай скриншот")).silent


async def test_unknown_skill_in_setting(make_engine):
    llm = ScriptedLLM(
        tool("set_reply_style", json.dumps({"skill": "fly_to_moon", "style": "silent"})),
        text("Такой команды нет."),
    )
    assert (await ask(make_engine(llm), "на полёт на луну молчи")).text == "Такой команды нет."
