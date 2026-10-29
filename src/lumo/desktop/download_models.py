"""Скачивание локальных моделей в каталог models/: ``uv run lumo-download-models``."""

import logging
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from lumo.config import Settings

log = logging.getLogger(__name__)

VOSK_URL = "https://alphacephei.com/vosk/models/{name}.zip"
SILERO_TTS_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"


def silero_tts_path(settings: Settings) -> Path:
    return settings.models_dir / "silero" / "v4_ru.pt"


def vosk_path(settings: Settings) -> Path:
    return settings.models_dir / settings.vosk_model


def whisper_root(settings: Settings) -> Path:
    return settings.models_dir / "whisper"


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Скачиваю {url}")
    with (
        tempfile.NamedTemporaryFile(delete=False, dir=target.parent, suffix=".part") as tmp,
        urllib.request.urlopen(url, timeout=60) as response,
    ):
        shutil.copyfileobj(response, tmp)
    Path(tmp.name).replace(target)


def ensure_vosk(settings: Settings) -> Path:
    path = vosk_path(settings)
    if path.exists():
        return path
    archive = path.with_suffix(".zip")
    _download(VOSK_URL.format(name=settings.vosk_model), archive)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(settings.models_dir)
    archive.unlink()
    return path


def ensure_silero_tts(settings: Settings) -> Path:
    path = silero_tts_path(settings)
    if not path.exists():
        _download(SILERO_TTS_URL, path)
    return path


def ensure_whisper(settings: Settings) -> str:
    from faster_whisper.utils import download_model

    print(f"Проверяю модель распознавания whisper-{settings.stt_model}")
    return download_model(settings.stt_model, cache_dir=str(whisper_root(settings)))


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    settings = Settings()
    print("Vosk:", ensure_vosk(settings))
    print("Silero TTS:", ensure_silero_tts(settings))
    print("Whisper:", ensure_whisper(settings))

    from lumo.desktop.cuda import cuda_available

    if cuda_available():
        print("CUDA доступна: распознавание речи пойдёт на видеокарте.")
    else:
        print("CUDA не найдена: распознавание речи пойдёт на процессоре (медленнее).")
    print("Готово.")


if __name__ == "__main__":
    main()
