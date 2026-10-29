"""Распознавание речи faster-whisper (локально, GPU int8 или CPU)."""

import logging
from pathlib import Path

import numpy as np

from lumo.desktop.cuda import cuda_available

log = logging.getLogger(__name__)


class WhisperSTT:
    def __init__(self, model: str, device: str, download_root: Path) -> None:
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model

        # Сначала локальная копия — чтобы клиент запускался без интернета.
        try:
            model = download_model(model, cache_dir=str(download_root), local_files_only=True)
        except Exception:
            log.info("Модель whisper-%s не найдена локально, скачиваю", model)

        use_cuda = device == "cuda" or (device == "auto" and cuda_available())
        # GTX 16xx медленно работает с float16, поэтому int8 и на GPU.
        try:
            self._model = WhisperModel(
                model,
                device="cuda" if use_cuda else "cpu",
                compute_type="int8",
                download_root=str(download_root),
            )
            self.device = "cuda" if use_cuda else "cpu"
        except Exception:
            if not use_cuda:
                raise
            log.warning("Не удалось запустить распознавание на GPU, переключаюсь на CPU")
            self._model = WhisperModel(
                model, device="cpu", compute_type="int8", download_root=str(download_root)
            )
            self.device = "cpu"
        log.info("Распознавание речи: %s на %s", Path(model).name, self.device)

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            audio,
            language="ru",
            beam_size=5,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()
