"""Встроенный музыкальный плеер: очередь, загрузка наперёд, управление.

Плеер — источник второго канала аудиодвижка: ``read()`` вызывается из аудиопотока и
отдаёт стерео-кадры 48 кГц. Всё остальное работает в asyncio.
"""

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

PREFETCH_MORE_AT = 2  # за сколько треков до конца очереди подгружать ещё (для «Моей волны»)
MAX_SKIPS = 3


@dataclass(frozen=True)
class MusicTrack:
    id: str
    title: str
    artists: str
    duration_ms: int = 0
    raw: Any = field(default=None, compare=False, repr=False)

    @property
    def label(self) -> str:
        return f"{self.artists} — {self.title}" if self.artists else self.title


Loader = Callable[[MusicTrack], Awaitable[np.ndarray]]
MoreTracks = Callable[[list[MusicTrack]], Awaitable[list[MusicTrack]]]
TrackStarted = Callable[[MusicTrack], Awaitable[None]]


class MusicPlayer:
    def __init__(self, loader: Loader) -> None:
        self.loader = loader
        self.queue: list[MusicTrack] = []
        self.index = -1
        self.title = ""
        self._more: MoreTracks | None = None
        self._on_start: TrackStarted | None = None
        self._buffer: np.ndarray | None = None
        self._pos = 0
        self._paused = False
        self._end_signaled = False
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._generation = 0
        self._prefetched: dict[str, asyncio.Task] = {}

    # --- для аудиодвижка (аудиопоток) ---

    def read(self, frames: int) -> np.ndarray | None:
        with self._lock:
            if self._buffer is None or self._paused:
                return None
            chunk = self._buffer[self._pos : self._pos + frames]
            self._pos += frames
            if len(chunk) < frames:
                chunk = np.concatenate([chunk, np.zeros((frames - len(chunk), 2), np.float32)])
                if not self._end_signaled and self._loop is not None:
                    self._end_signaled = True
                    self._loop.call_soon_threadsafe(self._track_ended)
            return chunk

    # --- состояние ---

    @property
    def active(self) -> bool:
        """Что-то загружено (играет или на паузе) — команды медиа относятся к плееру."""
        return self._buffer is not None

    @property
    def playing(self) -> bool:
        return self._buffer is not None and not self._paused

    @property
    def current(self) -> MusicTrack | None:
        return self.queue[self.index] if 0 <= self.index < len(self.queue) else None

    # --- управление ---

    async def play(
        self,
        tracks: list[MusicTrack],
        title: str = "",
        more: MoreTracks | None = None,
        on_start: TrackStarted | None = None,
    ) -> MusicTrack | None:
        self._loop = asyncio.get_running_loop()
        self._cancel_prefetch()
        self.queue = list(tracks)
        self.title = title
        self._more = more
        self._on_start = on_start
        return await self._start(0)

    async def control(self, action: str) -> str:
        if action in ("pause", "toggle") and self.playing:
            self._set_paused(True)
            return "Музыка на паузе."
        if action in ("resume", "toggle"):
            if not self.active:
                return "Сейчас ничего не играет."
            self._set_paused(False)
            return "Продолжаю."
        if action == "pause":
            return "Музыка уже на паузе."
        if action == "next":
            track = await self._start(self.index + 1)
            return f"Включаю {track.label}." if track else "Очередь закончилась."
        if action == "previous":
            restart = self._pos > 48000 * 5  # дальше 5 секунд — сначала текущий
            track = await self._start(self.index if restart else max(0, self.index - 1))
            return f"Включаю {track.label}." if track else "Не получилось."
        if action == "stop":
            self.stop()
            return "Музыка остановлена."
        return "Не знаю такой команды."

    def stop(self) -> None:
        self._generation += 1
        self._cancel_prefetch()
        with self._lock:
            self._buffer = None
            self._pos = 0
            self._paused = False

    def now_playing(self) -> str:
        track = self.current
        if track is None or not self.active:
            return "Сейчас ничего не играет."
        state = "на паузе" if self._paused else "играет"
        return f"Сейчас {state}: {track.label}."

    # --- внутреннее ---

    def _set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = paused

    def _track_ended(self) -> None:
        asyncio.ensure_future(self._start(self.index + 1))

    async def _start(self, index: int, skips: int = 0) -> MusicTrack | None:
        self._generation += 1
        generation = self._generation
        await self._extend_if_needed(index)
        if index >= len(self.queue):
            self.stop()
            return None
        track = self.queue[index]
        try:
            audio = await self._load(track)
        except Exception:
            log.exception("Не удалось загрузить трек %s", track.label)
            if skips + 1 >= MAX_SKIPS or generation != self._generation:
                return None
            return await self._start(index + 1, skips + 1)
        if generation != self._generation:
            return None  # пользователь уже переключил на другое
        with self._lock:
            self.index = index
            self._buffer = audio
            self._pos = 0
            self._paused = False
            self._end_signaled = False
        log.info("Играет: %s", track.label)
        if self._on_start is not None:
            asyncio.ensure_future(self._on_start(track))
        self._prefetch(index + 1)
        return track

    async def _extend_if_needed(self, index: int) -> None:
        if self._more is None or index < len(self.queue) - PREFETCH_MORE_AT:
            return
        try:
            self.queue.extend(await self._more(self.queue))
        except Exception:
            log.exception("Не удалось подгрузить продолжение очереди")

    async def _load(self, track: MusicTrack) -> np.ndarray:
        task = self._prefetched.pop(track.id, None)
        if task is not None:
            return await task
        return await self.loader(track)

    def _prefetch(self, index: int) -> None:
        if index < len(self.queue):
            track = self.queue[index]
            if track.id not in self._prefetched:
                self._prefetched[track.id] = asyncio.ensure_future(self.loader(track))

    def _cancel_prefetch(self) -> None:
        for task in self._prefetched.values():
            task.cancel()
        self._prefetched.clear()
