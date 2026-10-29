"""Запуск ПК-клиента: ``uv run lumo-desktop`` (голос) или ``uv run lumo-desktop --text``."""

import argparse
import asyncio
import logging
import sys
import warnings

from lumo.config import Settings
from lumo.logging_setup import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="lumo-desktop", description="ПК-клиент Lumo")
    parser.add_argument("--text", action="store_true", help="текстовый режим без микрофона")
    parser.add_argument("--debug", action="store_true", help="подробные логи")
    parser.add_argument("--list-mics", action="store_true", help="показать микрофоны и выйти")
    parser.add_argument("--mic", help="номер или часть названия микрофона (вместо LUMO_MIC_DEVICE)")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    if args.list_mics:
        from lumo.desktop.audio import list_input_devices

        print("Микрофоны (номер укажите в LUMO_MIC_DEVICE или через --mic):")
        print("\n".join(list_input_devices()))
        return

    settings = Settings()
    if args.mic is not None:
        settings.mic_device = args.mic
    # Строка состояния сама показывает, что происходит, логи только для ошибок.
    level = logging.DEBUG if args.debug else logging.WARNING
    setup_logging(
        level,
        secrets=[settings.token.get_secret_value(), settings.yandex_music_token.get_secret_value()],
    )

    # torch предупреждает о внутренностях Silero, пользователю это не нужно.
    warnings.filterwarnings("ignore", category=FutureWarning)

    from lumo.desktop.connection import AuthRejected

    try:
        if args.text:
            from lumo.desktop.text_mode import run_text_mode

            asyncio.run(run_text_mode(settings))
        else:
            from lumo.desktop.voice_mode import run_voice_mode

            asyncio.run(run_voice_mode(settings))
    except KeyboardInterrupt:
        pass
    except AuthRejected:
        print(
            "Ядро не приняло токен. LUMO_TOKEN у клиента и ядра должен совпадать "
            "(оба читают его из .env). Если меняли .env — перезапустите клиент.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
