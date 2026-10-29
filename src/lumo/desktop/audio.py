"""Микрофон: поток 16 кГц моно int16 кадрами по 512 сэмплов (32 мс)."""

import asyncio

import numpy as np

SAMPLE_RATE = 16000
FRAME_SAMPLES = 512


def to_float(frame: bytes) -> np.ndarray:
    return np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0


class Microphone:
    def __init__(self, device: int | str | None = None) -> None:
        self.device = device
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        loop = asyncio.get_running_loop()

        def callback(indata, frames, time, status) -> None:  # noqa: ARG001
            loop.call_soon_threadsafe(self._queue.put_nowait, bytes(indata))

        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            channels=1,
            dtype="int16",
            device=self.device,
            callback=callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    async def frame(self) -> bytes:
        return await self._queue.get()

    def drain(self) -> None:
        """Выбрасывает накопленные кадры (например, эхо собственного голоса)."""
        while not self._queue.empty():
            self._queue.get_nowait()


def describe_input_device(device: int | str | None) -> str:
    import sounddevice as sd

    info = sd.query_devices(device, kind="input")
    return f"[{info['index']}] {info['name']}"


def list_input_devices() -> list[str]:
    """Микрофоны через MME: в отличие от WASAPI, они сами пересчитывают частоту в 16 кГц."""
    import sounddevice as sd

    apis = sd.query_hostapis()
    default = sd.default.device[0]
    lines = []
    for i, d in enumerate(sd.query_devices()):
        api = apis[d["hostapi"]]["name"]
        if d["max_input_channels"] > 0 and api == "MME" and "Mapper" not in d["name"]:
            mark = "  ← по умолчанию" if i == default else ""
            lines.append(f"[{i}] {d['name']}{mark}")
    return lines


def tone(frequency: float = 880.0, duration: float = 0.12, volume: float = 0.25) -> np.ndarray:
    """Короткий сигнал 48 кГц с плавными краями (без щелчков)."""
    sr = 48000
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    envelope = np.minimum(1.0, np.minimum(t, duration - t) * 40)
    return (np.sin(2 * np.pi * frequency * t) * envelope * volume).astype(np.float32)
