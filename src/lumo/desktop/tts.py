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


class Player(Protocol):
    def play(self, audio: np.ndarray, sample_rate: int) -> None: ...

    def stop_playback(self) -> None: ...

    async def wait_played(self) -> None: ...


class Speaker:
    """Озвучивает текст по фразам; ``stop()`` прерывает речь сразу.

    Следующая фраза синтезируется, пока играет текущая, — так между ними нет пауз.
    """

    def __init__(self, tts: TTSEngine, player: Player) -> None:
        self.tts = tts
        self.player = player
        self._lock = asyncio.Lock()
        self._stopped = False
        self.current_text = ""

    @property
    def speaking(self) -> bool:
        return self._lock.locked()

    async def say(self, text: str) -> bool:
        """Возвращает False, если речь была прервана."""
        async with self._lock:
            self._stopped = False
            self.current_text = text
            try:
                for phrase in split_phrases(normalize(text)):
                    audio = await asyncio.to_thread(self.tts.synthesize, phrase)
                    if self._stopped:
                        return False
                    self.player.play(audio, self.tts.sample_rate)
                await self.player.wait_played()
                return not self._stopped
            finally:
                self.current_text = ""

    def stop(self) -> None:
        self._stopped = True
        self.player.stop_playback()
