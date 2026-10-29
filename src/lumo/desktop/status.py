"""Живая строка состояния в терминале: громкость микрофона, состояние, что слышно."""

import asyncio
import math
import shutil
import sys

import numpy as np

METER_WIDTH = 16
# Диапазон индикатора в dBFS: тишина комнаты ≈ -60, обычная речь ≈ -30…-15.
METER_FLOOR, METER_CEIL = -60.0, -10.0


def level_db(frame: bytes) -> float:
    samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
    return 20 * math.log10(max(rms, 1e-6))


def meter(db: float) -> str:
    filled = round((db - METER_FLOOR) / (METER_CEIL - METER_FLOOR) * METER_WIDTH)
    filled = max(0, min(METER_WIDTH, filled))
    return "█" * filled + "░" * (METER_WIDTH - filled)


class StatusLine:
    def __init__(self) -> None:
        self.state = "Запуск…"
        self.heard = ""
        self.music = ""  # «♪ Кино — Кукушка», если играет встроенная музыка
        self._db = METER_FLOOR
        self._task: asyncio.Task | None = None

    def push_level(self, frame: bytes) -> None:
        # Быстрый подъём, плавный спад — чтобы индикатор не дёргался.
        db = level_db(frame)
        self._db = db if db > self._db else self._db * 0.85 + db * 0.15

    def set(self, state: str) -> None:
        self.state = state

    def print(self, text: str) -> None:
        """Печатает обычную строку над строкой состояния."""
        sys.stdout.write("\r\x1b[K" + text + "\n")
        self._render()

    def _render(self) -> None:
        width = shutil.get_terminal_size((100, 20)).columns - 1
        line = f"🎤 {meter(self._db)} {self._db:5.0f} dB │ {self.state}"
        if self.music:
            line += f" │ {self.music}"
        if self.heard:
            line += f" │ слышу: «{self.heard}»"
        if len(line) > width:
            line = line[: width - 1] + "…"
        sys.stdout.write("\r\x1b[K" + line)
        sys.stdout.flush()

    async def _loop(self) -> None:
        while True:
            self._render()
            await asyncio.sleep(0.1)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
        sys.stdout.write("\r\x1b[K")
        sys.stdout.flush()
