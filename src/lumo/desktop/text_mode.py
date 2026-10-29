"""Текстовый режим клиента: реплики из консоли, ответы и события — в консоль."""

import asyncio
import sys

from lumo.config import Settings
from lumo.desktop.actions import ActionExecutor
from lumo.desktop.actions.pc import PcActions
from lumo.desktop.connection import CoreConnection, CoreUnavailable
from lumo.protocol.messages import ConfirmRequest, Event

EXIT_WORDS = {"выход", "exit", "quit"}


def _print(text: str) -> None:
    print(f"\r{text}\n> ", end="", flush=True)


async def run_text_mode(settings: Settings) -> None:
    async def on_event(event: Event) -> None:
        prefix = "⏰ (пропущено) " if event.missed else "⏰ "
        _print(prefix + event.text)

    async def on_status(connected: bool) -> None:
        _print("[связь с ядром есть]" if connected else "[нет связи с ядром, переподключаюсь…]")

    # В текстовом режиме ПК тоже управляется, а музыки нет: ей нужен аудиодвижок.
    executor = ActionExecutor()
    PcActions(settings.search_engine).register(executor)
    conn = CoreConnection(
        settings.core_url,
        settings.token.get_secret_value(),
        settings.device_id,
        on_event,
        on_status,
        executor=executor,
    )
    runner = asyncio.create_task(conn.run_forever())
    print("Текстовый режим Lumo. Напишите реплику, «выход» — завершить.")
    print("> ", end="", flush=True)
    try:
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                break
            text = line.strip()
            if not text:
                print("> ", end="", flush=True)
                continue
            if text.lower() in EXIT_WORDS:
                break
            if runner.done():
                runner.result()  # пробросит ошибку авторизации
            try:
                reply = await conn.ask(text)
            except (CoreUnavailable, TimeoutError) as exc:
                _print(f"[ошибка: {exc or 'ядро не ответило'}]")
                continue
            mark = "❓ " if isinstance(reply, ConfirmRequest) else ""
            _print(f"{conn.assistant_name}: {mark}{reply.text}")
    finally:
        runner.cancel()
