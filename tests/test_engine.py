import json
import logging

import pytest

from lumo.core.engine import CANCELLED_TEXT, LIMIT_TEXT, MAX_TOOL_CALLS, strip_markdown
from lumo.core.llm import LLMError
from lumo.protocol.messages import ConfirmRequest, Reply, Utterance
from tests.conftest import ScriptedLLM, text, tool


async def ask(engine, phrase, device="pc", uid="u1"):
    return await engine.handle_utterance("owner", device, Utterance(id=uid, text=phrase))


async def test_simple_reply(make_engine):
    engine = make_engine(ScriptedLLM(text("Будет четыре.")))
    reply = await ask(engine, "Сколько будет дважды два?")
    assert isinstance(reply, Reply)
    assert reply.id == "u1"
    assert "четыре" in reply.text


async def test_system_prompt_has_persona_time_and_facts(make_engine, storage):
    storage.add_fact("owner", "Любимый цвет — синий")
    llm = ScriptedLLM(text("ok"))
    await ask(make_engine(llm), "привет")
    system = llm.calls[0][0]["content"]
    assert "Вальтер" in system
    assert "29.10.2026 12:00" in system
    assert "Любимый цвет — синий" in system
    assert "не выдумывай" in system


def test_strip_markdown():
    raw = "## Итог\n**Важно**: смотри `код` и [ссылку](http://x)\n- пункт"
    clean = strip_markdown(raw)
    assert not any(ch in clean for ch in "*#`")
    assert "ссылку" in clean and "http" not in clean


async def test_reply_has_no_markdown(make_engine):
    reply = await ask(make_engine(ScriptedLLM(text("**Готово**, `всё` #ок"))), "x")
    assert not any(ch in reply.text for ch in "*#`")


async def test_question_reply_expects_response(make_engine):
    reply = await ask(make_engine(ScriptedLLM(text("На какое время?"))), "поставь напоминание")
    assert reply.expects_response


async def test_history_shared_across_devices(make_engine):
    llm = ScriptedLLM(text("Хорошо"), text("Франция"))
    engine = make_engine(llm)
    await ask(engine, "Меня интересует Париж", device="pc")
    await ask(engine, "Какой это страны столица?", device="phone")
    contents = [m["content"] for m in llm.calls[1]]
    assert "Меня интересует Париж" in contents
    assert "Хорошо" in contents


async def test_tool_call_result_is_fed_back(make_engine):
    def answer(messages, tools):
        result = json.loads(messages[-1]["content"])
        return text(f"Сейчас {result['time']}.")

    llm = ScriptedLLM(tool("get_datetime"), answer)
    reply = await ask(make_engine(llm), "Который час?")
    assert reply.text == "Сейчас 12:00."
    assert llm.calls[1][-1]["role"] == "tool"


async def test_tool_call_limit(make_engine):
    llm = ScriptedLLM(tool("get_datetime"))
    reply = await ask(make_engine(llm), "зациклись")
    assert reply.text == LIMIT_TEXT
    assert len(llm.calls) == MAX_TOOL_CALLS + 1


@pytest.mark.parametrize(
    ("kind", "fragment"),
    [("network", "нет связи"), ("timeout", "долго"), ("auth", "Ключ API недействителен")],
)
async def test_llm_errors_are_friendly(make_engine, kind, fragment):
    engine = make_engine(ScriptedLLM(LLMError(kind)))
    reply = await ask(engine, "привет")
    assert fragment in reply.text
    engine.llm = ScriptedLLM(text("снова работаю"))
    assert (await ask(engine, "привет")).text == "снова работаю"


async def test_auth_error_logged_without_key(make_engine, caplog, settings):
    engine = make_engine(ScriptedLLM(LLMError("auth", "неверный ключ API")))
    with caplog.at_level(logging.ERROR):
        await ask(engine, "привет")
    assert "auth" in caplog.text
    assert "sk-" not in caplog.text


# --- подтверждение опасных действий ---


async def _ask_forget(make_engine, storage, after_confirm):
    fact = storage.add_fact("owner", "Любимый цвет — синий")
    llm = ScriptedLLM(tool("forget_fact", json.dumps({"fact_id": fact.id})), *after_confirm)
    engine = make_engine(llm)
    question = await ask(engine, "Забудь мой любимый цвет")
    return engine, llm, fact, question


async def test_confirmation_yes_executes(make_engine, storage):
    engine, llm, _, question = await _ask_forget(make_engine, storage, [text("Забыла.")])
    assert isinstance(question, ConfirmRequest)
    assert "Любимый цвет — синий" in question.text
    assert storage.list_facts("owner")  # до подтверждения ничего не удалено

    reply = await ask(engine, "да", uid="u2")
    assert reply.text == "Забыла."
    assert storage.list_facts("owner") == []
    assert llm.calls[-1][-1] == {"role": "tool", "tool_call_id": "c1", "content": "Факт удалён."}


async def test_confirmation_no_cancels(make_engine, storage):
    engine, llm, _, _ = await _ask_forget(make_engine, storage, [text("unused")])
    calls_before = len(llm.calls)
    reply = await ask(engine, "нет", uid="u2")
    assert reply.text == CANCELLED_TEXT
    assert len(storage.list_facts("owner")) == 1
    assert len(llm.calls) == calls_before


async def test_confirmation_other_topic_cancels(make_engine, storage):
    engine, _, _, _ = await _ask_forget(make_engine, storage, [text("Сейчас 12:00.")])
    reply = await ask(engine, "А который час?", uid="u2")
    assert reply.text.startswith("Предыдущее действие отменено.")
    assert len(storage.list_facts("owner")) == 1


async def test_confirmation_expires(make_engine, storage, clock):
    engine, _, _, _ = await _ask_forget(make_engine, storage, [text("Слушаю.")])
    clock.advance(minutes=5)
    reply = await ask(engine, "да", uid="u2")
    assert reply.text == "Слушаю."
    assert len(storage.list_facts("owner")) == 1


async def test_persona_discourages_filler_questions(make_engine):
    llm = ScriptedLLM(text("ok"))
    await ask(make_engine(llm), "привет")
    assert "Чем ещё могу помочь" in llm.calls[0][0]["content"]
