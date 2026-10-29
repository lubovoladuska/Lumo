"""Запись команды до паузы с помощью Silero VAD."""

from collections.abc import Awaitable, Callable

import numpy as np

from lumo.desktop.audio import FRAME_SAMPLES, SAMPLE_RATE, to_float

FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE


class CommandRecorder:
    def __init__(
        self,
        end_silence_ms: int = 1000,
        max_seconds: float = 15.0,
        threshold: float = 0.5,
    ) -> None:
        from silero_vad import VADIterator, load_silero_vad

        self._model = load_silero_vad()
        self._vad_cls = VADIterator
        self.end_silence_ms = end_silence_ms
        self.max_seconds = max_seconds
        self.threshold = threshold

    async def record(
        self,
        next_frame: Callable[[], Awaitable[bytes]],
        no_speech_timeout: float = 5.0,
        initial: list[bytes] | None = None,
    ) -> np.ndarray | None:
        """Пишет речь до паузы. ``None`` — если пользователь так и не заговорил.

        ``initial`` — уже услышанные кадры (например, начало фразы, которой перебили).
        """
        import torch

        pending = list(initial or [])

        async def frame() -> bytes:
            return pending.pop(0) if pending else await next_frame()

        vad = self._vad_cls(
            self._model,
            threshold=self.threshold,
            sampling_rate=SAMPLE_RATE,
            min_silence_duration_ms=self.end_silence_ms,
            speech_pad_ms=150,
        )
        # Небольшой запас перед началом речи, чтобы не срезать первый слог.
        preroll: list[np.ndarray] = []
        chunks: list[np.ndarray] = []
        speaking = False
        elapsed = 0.0
        while True:
            samples = to_float(await frame())
            elapsed += FRAME_SECONDS
            event = vad(torch.from_numpy(samples))
            if speaking:
                chunks.append(samples)
            else:
                preroll = [*preroll[-9:], samples]
            if event and "start" in event and not speaking:
                speaking = True
                chunks = list(preroll)
            elif event and "end" in event and speaking:
                break
            if not speaking and elapsed >= no_speech_timeout:
                vad.reset_states()
                return None
            if speaking and len(chunks) * FRAME_SECONDS >= self.max_seconds:
                break
        vad.reset_states()
        return np.concatenate(chunks)

    def speech_probability(self, frame: bytes) -> float:
        """Вероятность речи в одном кадре — для перебивания во время ответа."""
        import torch

        with torch.no_grad():
            return float(self._model(torch.from_numpy(to_float(frame)), SAMPLE_RATE))

    def reset(self) -> None:
        self._model.reset_states()
