"""Диалоговый движок: контекст, вызов LLM, цикл навыков, подтверждения."""

import logging
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from lumo.config import Settings
from lumo.core.confirmation import Answer, ConfirmationManager, PendingConfirmation, classify_answer
from lumo.core.hub import Connection
from lumo.core.llm import LLMClient, LLMError, ToolCall
from lumo.core.skill_registry import REPLY_STYLES, PreparedCall, SkillContext, SkillRegistry
from lumo.core.storage import Storage, utcnow
from lumo.protocol.messages import ConfirmRequest, Event, Reply, Utterance

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 5
HISTORY_RESULT_LIMIT = 500

_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

_ERROR_TEXTS = {
    "network": "Сейчас нет связи с моим мозгом. Проверьте интернет, и попробуем ещё раз.",
    "timeout": "Мой мозг слишком долго думает и не ответил. Попробуйте ещё раз.",
    "auth": "Ключ API недействителен. Проверьте настройку ключа DeepSeek.",
    "other": "Не получилось получить ответ от моего мозга. Попробуйте ещё раз чуть позже.",
}
LIMIT_TEXT = "Извините, не смогла довести действие до конца: слишком много шагов."
CANCELLED_TEXT = "Хорошо, отменяю."

_PERSONA = """Ты — {name}, личный голосовой ассистент пользователя, в духе Джарвиса.
Отвечай по-русски, вежливо, спокойно и по делу. Обычно не больше трёх коротких предложений.
Не заканчивай ответ вопросом, если ответ пользователя тебе на самом деле не нужен:
никаких «Чем ещё могу помочь?». Вопрос задавай, только когда без уточнения не обойтись.
Твои ответы произносит синтезатор речи, поэтому не используй Markdown, списки, эмодзи и ссылки,
а числа, даты и время пиши так, чтобы их было удобно произнести.
Если не знаешь чего-то о пользователе, честно скажи, что не знаешь, и не выдумывай.
Для действий (таймеры, память, время, компьютер, музыка) используй инструменты, не притворяйся,
что действие выполнено, если инструмент не вызывался. Каждую новую просьбу выполняй новым
вызовом инструмента, даже если похожее уже делал: не отвечай по памяти о прошлых результатах —
громкость, музыка и всё остальное могли измениться.
«Включи <что-то>» без слов «фильм», «видео», «сайт» — это музыка: вызови music_play
(например, «включи Кино» — это группа «Кино»). «Включи музыку» без уточнений — «Моя волна».
Если пользователь просит что-то запомнить — вызови remember_fact.
Если просит забыть — вызови forget_fact с номером факта из списка ниже.
Даты для инструментов передавай в ISO 8601 с часовым поясом пользователя.

Сейчас: {weekday}, {now:%d.%m.%Y %H:%M}, часовой пояс {tz} (UTC{offset}).
{facts}"""


SHORT_REPLIES = ("Готово.", "Сделано, сэр.", "Есть.", "Готово, сэр.")
SILENT_TEXT = "Готово."


@dataclass(frozen=True)
class DirectReply:
    """Ответ без повторного запроса к LLM: короткая фраза или тихое подтверждение."""

    text: str
    silent: bool


def direct_reply(tool_log: list[dict]) -> DirectReply | None:
    """Все навыки реплики успешны и не требуют полного ответа → отвечаем сами.

    Любая ошибка или навык с полным ответом → None (ответ формулирует LLM).
    """
    if not tool_log:
        return None
    styles = set()
    for entry in tool_log:
        if str(entry.get("result", "")).startswith("Ошибка") or entry.get("style") == "normal":
            return None
        styles.add(entry.get("style", "normal"))
    if "short" in styles:
        return DirectReply(random.choice(SHORT_REPLIES), silent=False)
    return DirectReply(SILENT_TEXT, silent=True)


def strip_markdown(text: str) -> str:
    text = re.sub(r"```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = text.replace("`", "").replace("*", "").replace("#", "")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


class ConversationEngine:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        registry: SkillRegistry,
        llm: LLMClient,
        scheduler: Any = None,
        devices: Any = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.registry = registry
        self.llm = llm
        self.scheduler = scheduler
        self.devices = devices
        self.clock = clock
        self.tz = ZoneInfo(settings.tz_name)
        self.confirmations = ConfirmationManager()

    # --- API для сервера ---

    async def on_client_connected(self, conn: Connection) -> None:
        for event in self.storage.take_pending_events(conn.user_id):
            await conn.send(
                Event(
                    kind=event.kind, text=event.text, scheduled_for=event.scheduled_for, missed=True
                )
            )

    async def handle_utterance(
        self, user_id: str, device_id: str, utterance: Utterance
    ) -> Reply | ConfirmRequest:
        text = utterance.text.strip()
        ctx = self._context(user_id, device_id)
        history = self._history(user_id)
        self.storage.add_message(user_id, device_id, "user", text, at=self.clock())

        prefix = ""
        pending = self.confirmations.pop(user_id)
        if pending is not None and not pending.expired(self.clock()):
            answer = classify_answer(text)
            if answer is Answer.YES:
                return await self._finish(utterance.id, ctx, await self._resume(pending, ctx))
            if answer is Answer.NO:
                return self._reply(utterance.id, ctx, CANCELLED_TEXT)
            prefix = "Предыдущее действие отменено. "

        messages = [
            {"role": "system", "content": self._system_prompt(user_id)},
            *history,
            {"role": "user", "content": text},
        ]
        result = await self._run_loop(messages, ctx, calls_used=0)
        if isinstance(result, DirectReply) and prefix:
            result = DirectReply(prefix + result.text, silent=False)
        elif isinstance(result, str) and prefix:
            result = prefix + result
        return await self._finish(utterance.id, ctx, result)

    # --- цикл LLM ↔ навыки ---

    async def _run_loop(
        self, messages: list[dict], ctx: SkillContext, calls_used: int
    ) -> "str | PendingConfirmation | DirectReply":
        tools = self.registry.tools()
        while True:
            try:
                response = await self.llm.complete(messages, tools)
            except LLMError as exc:
                log.error("Ошибка LLM: %s", exc)
                return _ERROR_TEXTS[exc.kind]
            if not response.tool_calls:
                return response.content or ""

            messages.append(_assistant_tool_message(response.content, response.tool_calls))
            for index, call in enumerate(response.tool_calls):
                if calls_used >= MAX_TOOL_CALLS:
                    log.warning("Превышен лимит вызовов навыков")
                    return LIMIT_TEXT
                calls_used += 1
                prepared = self.registry.prepare(call.name, call.arguments)
                if isinstance(prepared, PreparedCall) and prepared.skill.confirm:
                    return self._ask_confirmation(
                        prepared,
                        ctx,
                        messages,
                        call,
                        response.tool_calls[index + 1 :],
                        calls_used,
                    )
                if isinstance(prepared, str):
                    result, style = prepared, "normal"
                else:
                    result = await self.registry.run(prepared, ctx)
                    style = self._reply_style(prepared, ctx)
                messages.append(_tool_result(call.id, result))
                ctx.tool_log.append(
                    {
                        "name": call.name,
                        "arguments": call.arguments,
                        "result": result,
                        "style": style,
                    }
                )
            direct = direct_reply(ctx.tool_log)
            if direct is not None:
                return direct  # отвечать по режиму, без второго запроса к LLM

    def _reply_style(self, prepared: PreparedCall, ctx: SkillContext) -> str:
        """Режим ответа: настройка пользователя важнее режима навыка по умолчанию."""
        override = self.storage.get_preference(ctx.user_id, f"reply:{prepared.skill.name}")
        return override if override in REPLY_STYLES else prepared.skill.reply_style(prepared.params)

    def _ask_confirmation(
        self,
        prepared: PreparedCall,
        ctx: SkillContext,
        messages: list[dict],
        call: ToolCall,
        rest: list[ToolCall],
        calls_used: int,
    ) -> PendingConfirmation:
        skill = prepared.skill
        what = skill.describe(prepared.params, ctx) if skill.describe else skill.description
        pending = PendingConfirmation(
            call=prepared,
            question=f"Подтвердите: {what}. Выполнить?",
            created_at=self.clock(),
            state={
                "messages": messages,
                "call_id": call.id,
                "skipped": [c.id for c in rest],
                "calls_used": calls_used,
                "tool_log": ctx.tool_log,
            },
        )
        self.confirmations.put(ctx.user_id, pending)
        return pending

    async def _resume(self, pending: PendingConfirmation, ctx: SkillContext) -> str | Any:
        state = pending.state
        messages: list[dict] = state["messages"]
        result = await self.registry.run(pending.call, ctx)
        messages.append(_tool_result(state["call_id"], result))
        call = pending.call
        ctx.tool_log[:0] = state.get("tool_log", [])
        ctx.tool_log.append(
            {"name": call.skill.name, "arguments": call.params.model_dump_json(), "result": result}
        )
        for skipped_id in state["skipped"]:
            messages.append(
                _tool_result(skipped_id, "Не выполнено: ожидалось подтверждение другого действия.")
            )
        return await self._run_loop(messages, ctx, state["calls_used"])

    # --- вспомогательное ---

    async def _finish(
        self, reply_id: str, ctx: SkillContext, result: "str | PendingConfirmation | DirectReply"
    ) -> Reply | ConfirmRequest:
        if isinstance(result, DirectReply):
            return self._reply(reply_id, ctx, result.text, silent=result.silent)
        if isinstance(result, PendingConfirmation):
            self.storage.add_message(
                ctx.user_id, ctx.device_id, "assistant", result.question, self.clock(), ctx.tool_log
            )
            return ConfirmRequest(id=reply_id, text=result.question)
        return self._reply(reply_id, ctx, result)

    def _reply(self, reply_id: str, ctx: SkillContext, text: str, silent: bool = False) -> Reply:
        clean = strip_markdown(text) or "Извините, мне нечего ответить."
        self.storage.add_message(
            ctx.user_id, ctx.device_id, "assistant", clean, self.clock(), ctx.tool_log
        )
        if silent:
            return Reply(id=reply_id, text=clean, silent=True)
        return Reply(id=reply_id, text=clean, expects_response=clean.rstrip().endswith("?"))

    def _context(self, user_id: str, device_id: str) -> SkillContext:
        return SkillContext(
            user_id=user_id,
            device_id=device_id,
            storage=self.storage,
            scheduler=self.scheduler,
            settings=self.settings,
            now=lambda: self.clock().astimezone(self.tz),
            devices=self.devices,
            skill_names=frozenset(s.name for s in self.registry.enabled()),
        )

    def _history(self, user_id: str) -> list[dict]:
        """Недавний диалог вместе с вызовами навыков — чтобы LLM видела, что действия
        выполнялись инструментами, и не начинала «делать» их на словах."""
        history: list[dict] = []
        for n, m in enumerate(self.storage.recent_messages(user_id, now=self.clock())):
            if m.role == "assistant" and m.tool_calls:
                calls = [
                    ToolCall(f"h{n}_{i}", c["name"], c["arguments"])
                    for i, c in enumerate(m.tool_calls)
                ]
                history.append(_assistant_tool_message(None, calls))
                history += [
                    _tool_result(call.id, str(c["result"])[:HISTORY_RESULT_LIMIT])
                    for call, c in zip(calls, m.tool_calls, strict=True)
                ]
            history.append({"role": m.role, "content": m.content})
        return history

    def _system_prompt(self, user_id: str) -> str:
        now = self.clock().astimezone(self.tz)
        facts = self.storage.list_facts(user_id)
        if facts:
            facts_text = "Известные факты о пользователе (номер: факт):\n" + "\n".join(
                f"{f.id}: {f.text}" for f in facts
            )
        else:
            facts_text = "Фактов о пользователе пока нет."
        offset = now.strftime("%z")
        return _PERSONA.format(
            name=self.settings.assistant_name,
            weekday=_WEEKDAYS[now.weekday()],
            now=now,
            tz=self.settings.tz_name,
            offset=f"{offset[:3]}:{offset[3:]}",
            facts=facts_text,
        )


def _assistant_tool_message(content: str | None, calls: list[ToolCall]) -> dict:
    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in calls
        ],
    }


def _tool_result(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}
