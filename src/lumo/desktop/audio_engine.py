"""Аудиодвижок с подавлением эха (WebRTC AEC3 через livekit).

Движок сам играет звук и сам слушает микрофон. Каждый кусок, ушедший в колонки, передаётся
в AEC как образец, и модуль вычитает его из сигнала микрофона. Поэтому ассистент не слышит
себя, а пользователя — слышит, даже когда тот перебивает.

Вся обработка идёт кадрами по 10 мс на 48 кГц; наружу отдаются кадры 16 кГц по 512 сэмплов,
как ждут Vosk, Silero VAD и Whisper.
"""

import asyncio
import logging
import threading
from collections import deque
from typing import Protocol

import numpy as np

from lumo.desktop.audio import FRAME_SAMPLES, SAMPLE_RATE

log = logging.getLogger(__name__)

ENGINE_RATE = 48000
BLOCK = ENGINE_RATE // 100  # 10 мс — размер кадра WebRTC APM
DECIMATE = ENGINE_RATE // SAMPLE_RATE
# Приглушение музыки, пока ассистент говорит или слушает, и скорость перехода (~150 мс).
DUCK_LEVEL = 0.15
DUCK_STEP = 1.0 / (0.15 * ENGINE_RATE)


class MusicSource(Protocol):
    """Источник музыки для второго канала: отдаёт стерео-кадры, None — тишина."""

    def read(self, frames: int) -> np.ndarray | None: ...


def mix(
    voice: np.ndarray,
    music: np.ndarray | None,
    volume: float,
    gain: float,
    target: float,
    step: float = DUCK_STEP,
) -> tuple[np.ndarray, float]:
    """Смешивает моно-голос и стерео-музыку. Возвращает стерео и новое значение приглушения.

    Приглушение меняется плавно, по ``step`` на сэмпл, без щелчков.
    """
    frames = len(voice)
    out = np.repeat(voice[:, None], 2, axis=1)
    if target >= gain:
        ramp = np.minimum(target, gain + step * np.arange(1, frames + 1))
    else:
        ramp = np.maximum(target, gain - step * np.arange(1, frames + 1))
    if music is not None:
        out += music[:frames] * (volume * ramp)[:, None]
    return np.clip(out, -1.0, 1.0), float(ramp[-1]) if frames else gain


class _Playback:
    """Очередь звука для колонок; читается из аудиопотока, пишется из asyncio."""

    def __init__(self) -> None:
        self._chunks: deque[np.ndarray] = deque()
        self._offset = 0
        self._lock = threading.Lock()
        self.idle = threading.Event()
        self.idle.set()

    def put(self, audio: np.ndarray) -> None:
        with self._lock:
            self._chunks.append(audio.astype(np.float32))
            self.idle.clear()

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._offset = 0
            self.idle.set()

    def read(self, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < n and self._chunks:
                chunk = self._chunks[0]
                take = min(n - filled, len(chunk) - self._offset)
                out[filled : filled + take] = chunk[self._offset : self._offset + take]
                filled += take
                self._offset += take
                if self._offset >= len(chunk):
                    self._chunks.popleft()
                    self._offset = 0
            if not self._chunks:
                self.idle.set()
        return out


class _Decimator:
    """48 кГц → 16 кГц с простым фильтром нижних частот (среднее по 3 сэмплам)."""

    def __init__(self) -> None:
        self._pending = np.zeros(0, dtype=np.float32)

    def push(self, samples: np.ndarray) -> np.ndarray:
        data = np.concatenate([self._pending, samples])
        usable = len(data) - len(data) % DECIMATE
        self._pending = data[usable:]
        return data[:usable].reshape(-1, DECIMATE).mean(axis=1)


class EchoCancellingAudio:
    """Микрофон и динамики с AEC. API для микрофона совместим с ``audio.Microphone``."""

    def __init__(
        self,
        input_device: int | str | None = None,
        output_device: int | str | None = None,
        echo_cancellation: bool = True,
        stream_delay_ms: int = 60,
    ) -> None:
        self.input_device = input_device
        self.output_device = output_device
        self.stream_delay_ms = stream_delay_ms
        self.aec_enabled = echo_cancellation
        self._apm = None
        self._apm_lock = threading.Lock()
        self._playback = _Playback()
        self._decimator = _Decimator()
        self._out16 = np.zeros(0, dtype=np.float32)
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._streams: list = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._channels = 2
        self.music: MusicSource | None = None
        self.music_volume = 0.7
        self._duck_gain = 1.0
        self._ducked = False

    # --- запуск ---

    def start(self) -> None:
        import sounddevice as sd

        self._loop = asyncio.get_running_loop()
        if self.aec_enabled:
            self._apm = _create_apm()
        out_info = sd.query_devices(self.output_device, kind="output")
        self._channels = 2 if out_info["max_output_channels"] >= 2 else 1
        out_stream = sd.OutputStream(
            samplerate=ENGINE_RATE,
            blocksize=BLOCK,
            channels=self._channels,
            dtype="float32",
            device=self.output_device,
            callback=self._on_output,
        )
        in_stream = sd.InputStream(
            samplerate=ENGINE_RATE,
            blocksize=BLOCK,
            channels=1,
            dtype="float32",
            device=self.input_device,
            callback=self._on_input,
        )
        self._streams = [out_stream, in_stream]
        for stream in self._streams:
            stream.start()
        # Задержка между «отдали в колонки» и «микрофон это услышал».
        self.stream_delay_ms = max(
            self.stream_delay_ms, int((out_stream.latency + in_stream.latency) * 1000)
        )
        log.info(
            "Аудио: AEC %s, задержка %d мс", "вкл" if self._apm else "выкл", self.stream_delay_ms
        )

    def stop(self) -> None:
        for stream in self._streams:
            stream.stop()
            stream.close()
        self._streams = []

    @property
    def aec_active(self) -> bool:
        return self._apm is not None

    # --- микрофон ---

    async def frame(self) -> bytes:
        return await self._queue.get()

    def drain(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()

    # --- динамики ---

    def play(self, audio: np.ndarray, sample_rate: int) -> None:
        if sample_rate != ENGINE_RATE:
            audio = _resample(audio, sample_rate, ENGINE_RATE)
        self._playback.put(audio)

    def stop_playback(self) -> None:
        self._playback.clear()

    @property
    def playing(self) -> bool:
        return not self._playback.idle.is_set()

    async def wait_played(self) -> None:
        while self.playing:
            await asyncio.sleep(0.02)

    def duck(self, ducked: bool) -> None:
        """Приглушить музыку (ассистент говорит или слушает) или вернуть громкость."""
        self._ducked = ducked

    # --- аудиопотоки (вызываются из потока sounddevice) ---

    def _on_output(self, outdata, frames, time, status) -> None:  # noqa: ARG002
        voice = self._playback.read(frames)
        music = self.music.read(frames) if self.music is not None else None
        target = DUCK_LEVEL if self._ducked else 1.0
        stereo, self._duck_gain = mix(voice, music, self.music_volume, self._duck_gain, target)
        if self._channels == 2:
            outdata[:] = stereo
        else:
            outdata[:, 0] = stereo.mean(axis=1)
        if self._apm is not None:
            # AEC должен знать всё, что реально звучит из колонок: и голос, и музыку.
            reference = stereo.mean(axis=1)
            for start in range(0, frames - BLOCK + 1, BLOCK):
                with self._apm_lock:
                    self._apm.reverse(reference[start : start + BLOCK])

    def _on_input(self, indata, frames, time, status) -> None:  # noqa: ARG002
        samples = indata[:, 0].copy()
        if self._apm is not None:
            cleaned = []
            for start in range(0, frames - BLOCK + 1, BLOCK):
                with self._apm_lock:
                    cleaned.append(
                        self._apm.capture(samples[start : start + BLOCK], self.stream_delay_ms)
                    )
            samples = np.concatenate(cleaned) if cleaned else samples
        self._out16 = np.concatenate([self._out16, self._decimator.push(samples)])
        while len(self._out16) >= FRAME_SAMPLES:
            frame, self._out16 = self._out16[:FRAME_SAMPLES], self._out16[FRAME_SAMPLES:]
            pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16).tobytes()
            self._loop.call_soon_threadsafe(self._queue.put_nowait, pcm)


class _LivekitAPM:
    """Тонкая обёртка над livekit.rtc.AudioProcessingModule для кадров 10 мс float32."""

    def __init__(self) -> None:
        from livekit import rtc

        self._rtc = rtc
        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=True,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=True,
        )

    def _frame(self, block: np.ndarray):
        pcm = (np.clip(block, -1, 1) * 32767).astype(np.int16)
        return self._rtc.AudioFrame(pcm.tobytes(), ENGINE_RATE, 1, len(pcm))

    def reverse(self, block: np.ndarray) -> None:
        self._apm.process_reverse_stream(self._frame(block))

    def capture(self, block: np.ndarray, delay_ms: int) -> np.ndarray:
        frame = self._frame(block)
        self._apm.set_stream_delay_ms(delay_ms)
        self._apm.process_stream(frame)
        return np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0


def _create_apm() -> _LivekitAPM | None:
    try:
        return _LivekitAPM()
    except Exception:
        log.exception("Не удалось включить подавление эха, работаю без него")
        return None


def _resample(audio: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return audio
    positions = np.linspace(0, len(audio) - 1, int(len(audio) * dst / src))
    return np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
