"""Запуск ядра: ``uv run lumo-core``."""

import logging

import uvicorn

from lumo.config import Settings
from lumo.core.app import build_app
from lumo.logging_setup import setup_logging

log = logging.getLogger("lumo.core")


def main() -> None:
    settings = Settings()
    setup_logging(
        secrets=[settings.deepseek_api_key.get_secret_value(), settings.token.get_secret_value()]
    )
    if not settings.deepseek_api_key.get_secret_value():
        log.warning(
            "LUMO_DEEPSEEK_API_KEY не задан — ассистент не сможет отвечать. См. .env.example"
        )
    if settings.token.get_secret_value() == "change-me":
        log.warning("Используется токен по умолчанию. Задайте свой LUMO_TOKEN в .env")
    app = build_app(settings)
    log.info("Ядро Lumo слушает %s:%d", settings.bind_host, settings.port)
    uvicorn.run(app, host=settings.bind_host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
