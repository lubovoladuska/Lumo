"""Запуск ПК-клиента: ``uv run lumo-desktop`` (голос) или ``uv run lumo-desktop --text``."""

import argparse
import asyncio
import logging
import sys

from lumo.config import Settings
from lumo.logging_setup import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="lumo-desktop", description="ПК-клиент Lumo")
    parser.add_argument("--text", action="store_true", help="текстовый режим без микрофона")
    parser.add_argument("--debug", action="store_true", help="подробные логи")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    settings = Settings()
    level = logging.DEBUG if args.debug else (logging.WARNING if args.text else logging.INFO)
    setup_logging(level, secrets=[settings.token.get_secret_value()])

    try:
        if args.text:
            from lumo.desktop.text_mode import run_text_mode

            asyncio.run(run_text_mode(settings))
        else:
            from lumo.desktop.voice_mode import run_voice_mode

            asyncio.run(run_voice_mode(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
