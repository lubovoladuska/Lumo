"""Настройка логирования с маскировкой секретов."""

import logging
import re

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
]


class SecretMaskingFilter(logging.Filter):
    """Заменяет похожие на API-ключи строки на «***» в любом сообщении лога."""

    def __init__(self, extra_secrets: list[str] | None = None) -> None:
        super().__init__()
        self._extra = [s for s in (extra_secrets or []) if len(s) >= 6]

    def mask(self, text: str) -> str:
        for secret in self._extra:
            text = text.replace(secret, "***")
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub(lambda m: (m.group(1) if m.groups() else "") + "***", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.mask(record.getMessage())
        record.args = None
        return True


def setup_logging(level: int = logging.INFO, secrets: list[str] | None = None) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    )
    handler.addFilter(SecretMaskingFilter(secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
