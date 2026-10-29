"""Потоковое воспроизведение: трек играет, пока ещё скачивается.

``ByteStream`` получает куски файла из сети, ``decode_stream`` в отдельном потоке
декодирует их (miniaudio) в ``TrackAudio`` — растущий буфер стерео 48 кГц, из которого
читает плеер.
"""

import asyncio
import logging
import threading
from collections.abc import Callable

import numpy as np

log = logging.getLogger(__name__)

RATE = 48000


class TrackAudio:
    """Звук трека, который может ещё догружаться. Потокобезопасен."""

    def __init__(self) -> None:
        self._data = np.zeros((RATE * 30, 2), np.float32)
        self._frames = 0
        self.done = False
        self.error: Exception | None = None
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self.on_cancel: Callable[[], None] | None = None

    @classmethod
    def from_array(cls, samples: np.ndarray) -> "TrackAudio":
        audio = cls()
        audio.append(samples)
        audio.finish()
        return audio

    @property
    def frames(self) -> int:
        return self._frames

    def append(self, samples: np.ndarray) -> None:
        with self._changed:
            needed = self._frames + len(samples)
            if needed > len(self._data):
                grown = np.zeros((max(needed, len(self._data) * 2), 2), np.float32)
                grown[: self._frames] = self._data[: self._frames]
                self._data = grown
            self._data[self._frames : needed] = samples
            self._frames = needed
            self._changed.notify_all()

    def finish(self, error: Exception | None = None) -> None:
        with self._changed:
            self.done = True
            self.error = error
            self._changed.notify_all()

    def read(self, pos: int, frames: int) -> np.ndarray:
        with self._lock:
            return self._data[pos : min(pos + frames, self._frames)].copy()

    def wait_for(self, frames: int, timeout: float) -> bool:
        """Ждёт, пока накопится ``frames`` кадров или трек закончится. Блокирующий."""
        with self._changed:
            return self._changed.wait_for(
                lambda: self._frames >= frames or self.done, timeout=timeout
            )

    async def ready(self, seconds: float, timeout: float) -> None:
        """Ждёт запас звука перед стартом; ошибка — если звука так и не появилось."""
        await asyncio.to_thread(self.wait_for, int(seconds * RATE), timeout)
        if self._frames == 0:
            raise self.error or TimeoutError("трек не загрузился")

    async def wait_done(self) -> None:
        while not self.done:
            await asyncio.sleep(0.5)

    def cancel(self) -> None:
        if self.on_cancel is not None:
            self.on_cancel()


def _stream_source_base():
    import miniaudio

    return miniaudio.StreamableSource


class ByteStream(_stream_source_base()):
    """Байты файла, приходящие из сети; ``read`` блокируется, пока данных нет."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._closed = False
        self._aborted = False
        self._cond = threading.Condition()

    def feed(self, data: bytes) -> None:
        with self._cond:
            self._buf += data
            self._cond.notify_all()

    def finish(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def abort(self) -> None:
        with self._cond:
            self._aborted = True
            self._cond.notify_all()

    def read(self, num_bytes: int) -> bytes:
        with self._cond:
            self._cond.wait_for(lambda: self._buf or self._closed or self._aborted)
            if self._aborted:
                return b""
            out = bytes(self._buf[:num_bytes])
            del self._buf[:num_bytes]
            return out


def decode_stream(stream: ByteStream, audio: TrackAudio, file_format: str = "mp3") -> None:
    """Декодирует поток в ``audio`` по мере поступления. Запускать в отдельном потоке."""
    import miniaudio

    try:
        frames = miniaudio.stream_any(
            stream,
            source_format=getattr(miniaudio.FileFormat, file_format.upper()),
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=2,
            sample_rate=RATE,
            frames_to_read=RATE // 10,
        )
        for chunk in frames:
            audio.append(np.frombuffer(chunk, dtype=np.float32).reshape(-1, 2))
        audio.finish()
    except Exception as exc:  # обрыв сети, битый файл
        log.warning("Поток трека прервался: %s", exc)
        audio.finish(exc)


def start_decoding(stream: ByteStream, file_format: str = "mp3") -> TrackAudio:
    audio = TrackAudio()
    threading.Thread(target=decode_stream, args=(stream, audio, file_format), daemon=True).start()
    return audio
