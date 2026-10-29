"""Синтез речи Silero TTS (локально, CPU) с прерываемым воспроизведением."""

import asyncio
import logging
from pathlib import Path
from typing import Protocol

import numpy as np

from lumo.desktop.speech_text import normalize, split_phrases

log = logging.getLogger(__name__)


class TTSEngine(Protocol):
    sample_rate: int

    def synthesize(self, text: str) -> np.ndarray: ...


class SileroTTS:
    def __init__(self, model_path: Path, speaker: str = "xenia", sample_rate: int = 48000) -> None:
        import torch

        torch.set_num_threads(4)
        importer = torch.package.PackageImporter(str(model_path))
        self._model = importer.load_pickle("tts_models", "model")
        self._model.to(torch.device("cpu"))
        self.speaker = speaker
        self.sample_rate = sample_rate
        log.info("Синтез речи: Silero v4, голос %s", speaker)

    def synthesize(self, text: str) -> np.ndarray:
        audio = self._model.apply_tts(
            text=text,
            speaker=self.speaker,
            sample_rate=self.sample_rate,
            put_accent=True,
            put_yo=True,
        )
        return audio.numpy().astype(np.float32)


class Speaker:
    """Озвучивает текст по фразам; ``stop()`` прерывает речь между и внутри фраз."""

    def __init__(self, tts: TTSEngine) -> None:
        self.tts = tts
        self._lock = asyncio.Lock()
        self._stopped = False
        self.current_text = ""

    @property
    def speaking(self) -> bool:
        return self._lock.locked()

    async def say(self, text: str) -> bool:
        """Возвращает False, если речь была прервана."""
        import sounddevice as sd

        async with self._lock:
            self._stopped = False
            self.current_text = text
            try:
                for phrase in split_phrases(normalize(text)):
                    if self._stopped:
                        return False
                    audio = await asyncio.to_thread(self.tts.synthesize, phrase)
                    if self._stopped:
                        return False
                    sd.play(audio, self.tts.sample_rate)
                    duration = len(audio) / self.tts.sample_rate
                    waited = 0.0
                    while waited < duration and not self._stopped:
                        await asyncio.sleep(0.05)
                        waited += 0.05
                return not self._stopped
            finally:
                self.current_text = ""

    def stop(self) -> None:
        import sounddevice as sd

        self._stopped = True
        sd.stop()
