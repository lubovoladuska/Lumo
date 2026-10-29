"""Настройки Lumo из переменных окружения и файла .env."""

from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="LUMO_", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM
    deepseek_api_key: SecretStr = SecretStr("")
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout: float = 30.0

    # Ядро
    host: str = "127.0.0.1"
    port: int = 8765
    allow_network: bool = False
    token: SecretStr = SecretStr("change-me")
    # IANA-пояс, например Asia/Bishkek. Пусто — берётся из системы.
    timezone: str = ""
    data_dir: Path = Path("data")
    disabled_skills: Annotated[list[str], NoDecode] = Field(default_factory=list)
    assistant_name: str = "Вальтер"

    # ПК-клиент
    core_url: str = "ws://127.0.0.1:8765/ws"
    device_id: str = "desktop"
    models_dir: Path = Path("models")
    stt_model: str = "small"
    stt_device: str = "auto"
    vosk_model: str = "vosk-model-small-ru-0.22"
    wake_word: str = "вальтер"
    tts_speaker: str = "xenia"
    tts_sample_rate: int = 48000
    # Номер или часть названия микрофона (см. lumo-desktop --list-mics). Пусто — системный.
    mic_device: str = ""
    # Колонки/наушники: номер или часть названия. Пусто — системные.
    speaker_device: str = ""
    # Подавление эха (WebRTC AEC): ассистент не слышит себя, его можно перебивать голосом.
    echo_cancellation: bool = True
    # Как перебивать ассистента во время речи: voice — любой фразой, name — только по имени.
    barge_in: str = "voice"
    # Токен Яндекс Музыки (как получить — README). Пусто — музыка отвечает подсказкой.
    yandex_music_token: SecretStr = SecretStr("")
    # Поисковик для «найди в интернете»: yandex или google.
    search_engine: str = "yandex"

    @property
    def mic(self) -> int | str | None:
        return _device(self.mic_device)

    @property
    def speaker(self) -> int | str | None:
        return _device(self.speaker_device)

    @field_validator("disabled_skills", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def tz_name(self) -> str:
        if self.timezone.strip():
            return self.timezone.strip()
        from tzlocal import get_localzone_name

        return get_localzone_name() or "UTC"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lumo.db"

    @property
    def bind_host(self) -> str:
        """Хост для сервера: сеть открывается только явным флагом."""
        if self.allow_network:
            return self.host
        return "127.0.0.1"


def _device(value: str) -> int | str | None:
    value = value.strip()
    if not value:
        return None
    return int(value) if value.isdigit() else value
