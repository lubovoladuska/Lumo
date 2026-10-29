import pytest

from lumo.core.fast_commands import match, normalize


@pytest.mark.parametrize(
    ("phrase", "skill", "params"),
    [
        # системная громкость
        ("Громкость 30", "pc_volume", {"action": "set", "level": 30}),
        ("Громкость 30.", "pc_volume", {"action": "set", "level": 30}),
        ("Вальтер, громкость на 30%", "pc_volume", {"action": "set", "level": 30}),
        ("Поставь громкость на тридцать процентов", "pc_volume", {"action": "set", "level": 30}),
        ("Сделай звук 45", "pc_volume", {"action": "set", "level": 45}),
        ("громкость двадцать пять", "pc_volume", {"action": "set", "level": 25}),
        ("Громкость системы на 10, пожалуйста", "pc_volume", {"action": "set", "level": 10}),
        ("Тише", "pc_volume", {"action": "down"}),
        ("Потише, пожалуйста.", "pc_volume", {"action": "down"}),
        ("Тише на 20", "pc_volume", {"action": "down", "step": 20}),
        ("Убавь звук на пять", "pc_volume", {"action": "down", "step": 5}),
        ("Громче", "pc_volume", {"action": "up"}),
        ("Сделай погромче", "pc_volume", {"action": "up"}),
        ("Прибавь громкость на 10 процентов", "pc_volume", {"action": "up", "step": 10}),
        ("Выключи звук", "pc_volume", {"action": "mute"}),
        ("Включи звук", "pc_volume", {"action": "unmute"}),
        ("Какая сейчас громкость?", "pc_volume", {"action": "get"}),
        # медиа
        ("Пауза", "media_control", {"action": "pause"}),
        ("Поставь на паузу", "media_control", {"action": "pause"}),
        ("Продолжи", "media_control", {"action": "resume"}),
        ("Дальше", "media_control", {"action": "next"}),
        ("Следующий трек", "media_control", {"action": "next"}),
        ("Следующая песня!", "media_control", {"action": "next"}),
        ("Предыдущий трек", "media_control", {"action": "previous"}),
        ("Выключи музыку", "media_control", {"action": "stop"}),
        # громкость музыки
        ("Музыку тише", "music_volume", {"action": "down"}),
        ("Музыку потише на 20", "music_volume", {"action": "down", "step": 20}),
        ("Сделай музыку громче", "music_volume", {"action": "up"}),
        ("Громкость музыки 40", "music_volume", {"action": "set", "level": 40}),
        ("Музыку на 15%", "music_volume", {"action": "set", "level": 15}),
        # система
        ("Сверни всё", "pc_minimize_all", {}),
        ("Сверни все окна", "pc_minimize_all", {}),
        ("Заблокируй компьютер", "pc_lock", {}),
        ("Сделай скриншот", "pc_screenshot", {}),
        ("Скрин", "pc_screenshot", {}),
        ("Который час?", "get_datetime", {}),
        ("Сколько времени", "get_datetime", {}),
    ],
)
def test_recognized(phrase, skill, params):
    command = match(phrase)
    assert command is not None, phrase
    assert command.skill == skill
    assert command.params == params


@pytest.mark.parametrize(
    "phrase",
    [
        "30",  # может быть ответом на вопрос ассистента
        "Поставь громкость 20 и скажи, который час",  # составная просьба
        "Почему громкость 30 такая тихая",
        "Включи Кино",  # нужен поиск — это к LLM
        "Пауза в работе была долгой",
        "Тише едешь — дальше будешь",
        "Запомни, что я люблю чай",
        "Выключи компьютер",  # опасное — только с подтверждением через LLM
        "Как дела?",
        "",
    ],
)
def test_not_recognized(phrase):
    assert match(phrase) is None


def test_normalize():
    assert normalize("Вальтер, громкость на тридцать пять %!") == "громкость на 35 процентов"


def test_time_formatter():
    command = match("который час")
    text = command.format('{"time": "14:50", "weekday": "четверг", "date": "29 октября 2026"}')
    assert text == "Сейчас 14:50, четверг, 29 октября 2026."


# --- быстрый путь в движке ---

from tests.conftest import ScriptedLLM, text  # noqa: E402
from tests.test_devices import add  # noqa: E402


class NoLLM(ScriptedLLM):
    def __init__(self):
        super().__init__(text("LLM не должна вызываться"))


async def say(engine, phrase):
    from lumo.protocol.messages import Utterance

    return await engine.handle_utterance("owner", "pc", Utterance(text=phrase))


@pytest.fixture
def pc(devices, hub):
    _, sock = add(hub, devices, "pc", ["pc", "music"], reply="Громкость 30%.")
    return sock


async def test_volume_without_llm(make_engine, pc):
    llm = NoLLM()
    reply = await say(make_engine(llm), "Громкость 30")
    assert llm.calls == []
    assert reply.silent and reply.text == "Громкость 30%."
    assert pc.requests[0].params == {"action": "set", "level": 30, "step": None}


async def test_time_without_llm(make_engine):
    llm = NoLLM()
    reply = await say(make_engine(llm), "Который час?")
    assert llm.calls == []
    assert reply.text == "Сейчас 12:00, четверг, 29 октября 2026."


async def test_error_spoken_without_llm(make_engine):
    llm = NoLLM()
    reply = await say(make_engine(llm), "Пауза")  # ПК-клиента нет
    assert llm.calls == []
    assert reply.text == "Компьютер сейчас не подключён." and not reply.silent


async def test_user_style_respected(make_engine, pc, storage):
    storage.set_preference("owner", "reply:pc_volume", "short")
    reply = await say(make_engine(NoLLM()), "Громкость 30")
    assert not reply.silent  # пользователь попросил отвечать коротко


async def test_history_has_tool_call_for_llm_later(make_engine, pc):
    llm = ScriptedLLM(text("ok"))
    engine = make_engine(llm)
    await say(engine, "Громкость 30")
    await say(engine, "А что ты сейчас сделал, расскажи")
    history = llm.calls[0]
    assert any(m.get("tool_calls") for m in history if m["role"] == "assistant")


async def test_disabled_skill_goes_to_llm(make_engine, pc):
    llm = ScriptedLLM(text("Не могу менять громкость."))
    reply = await say(make_engine(llm, disabled=["pc_volume"]), "Громкость 30")
    assert len(llm.calls) == 1
    assert reply.text == "Не могу менять громкость."


async def test_pending_confirmation_answer_is_not_fast(make_engine, pc, storage):
    """«Дальше» в ответ на вопрос о подтверждении — это ответ, не команда плееру."""
    import json

    from tests.conftest import tool

    fact = storage.add_fact("owner", "x")
    llm = ScriptedLLM(tool("forget_fact", json.dumps({"fact_id": fact.id})), text("Ок, оставил."))
    engine = make_engine(llm)
    await say(engine, "Забудь x")
    await say(engine, "Дальше")
    assert not pc.requests  # media_control не вызывался
