import asyncio

import numpy as np
import pytest

from lumo.desktop.player import MusicPlayer, MusicTrack


def tracks(*names):
    return [MusicTrack(n, f"Песня {n}", "Исполнитель") for n in names]


class FakeLoader:
    def __init__(self, frames=1000, fail=()):
        self.frames = frames
        self.fail = set(fail)
        self.loaded: list[str] = []

    async def __call__(self, track):
        await asyncio.sleep(0)
        if track.id in self.fail:
            raise RuntimeError("нет доступа")
        self.loaded.append(track.id)
        value = float(int(track.id) if track.id.isdigit() else 1) / 100
        return np.full((self.frames, 2), value, np.float32)


async def settle():
    # загрузка трека проходит через отдельный поток — ждём немного реального времени
    for _ in range(10):
        await asyncio.sleep(0.02)


async def test_play_and_read():
    player = MusicPlayer(FakeLoader())
    track = await player.play(tracks("1", "2"))
    assert track.id == "1"
    chunk = player.read(10)
    assert chunk.shape == (10, 2) and chunk[0, 0] == pytest.approx(0.01)
    assert "Исполнитель — Песня 1" in player.now_playing()


async def test_auto_advance_at_track_end():
    player = MusicPlayer(FakeLoader(frames=100))
    await player.play(tracks("1", "2"))
    chunk = player.read(150)  # конец первого трека, хвост — тишина
    assert chunk[-1, 0] == 0
    await settle()
    assert player.current.id == "2"


async def test_pause_resume_next_previous():
    player = MusicPlayer(FakeLoader())
    await player.play(tracks("1", "2", "3"))
    assert await player.control("pause") == "Музыка на паузе."
    assert player.read(10) is None
    assert player.active and not player.playing
    assert await player.control("resume") == "Продолжаю."
    assert player.playing
    assert "Песня 2" in await player.control("next")
    assert "Песня 1" in await player.control("previous")


async def test_queue_end_stops():
    player = MusicPlayer(FakeLoader())
    await player.play(tracks("1"))
    assert await player.control("next") == "Очередь закончилась."
    assert not player.active
    assert player.now_playing() == "Сейчас ничего не играет."


async def test_broken_track_is_skipped():
    loader = FakeLoader(fail={"1"})
    player = MusicPlayer(loader)
    track = await player.play(tracks("1", "2"))
    assert track.id == "2"


async def test_endless_queue_fetches_more():
    calls = []

    async def more(queue):
        calls.append(len(queue))
        return tracks(str(len(queue) + 1))

    player = MusicPlayer(FakeLoader())
    await player.play(tracks("1"), more=more)
    assert calls  # очередь короткая — сразу подгрузили продолжение
    assert "Песня 2" in await player.control("next")


async def test_on_start_callback_and_prefetch():
    started = []

    async def on_start(track):
        started.append(track.id)

    loader = FakeLoader()
    player = MusicPlayer(loader)
    await player.play(tracks("1", "2"), on_start=on_start)
    await settle()
    assert started == ["1"]
    assert loader.loaded == ["1", "2"]  # второй трек загружен заранее
    await player.control("next")
    assert loader.loaded == ["1", "2"]  # повторно не грузили


async def test_stop():
    player = MusicPlayer(FakeLoader())
    await player.play(tracks("1"))
    assert await player.control("stop") == "Музыка остановлена."
    assert player.read(10) is None


async def test_streaming_track_plays_while_downloading():
    """Трек стартует, когда накопилось ~2 с звука, а дальше догружается во время игры."""
    from lumo.desktop.streaming import TrackAudio

    audio = TrackAudio()

    async def streaming_loader(track):
        return audio

    async def feed():
        for _ in range(10):
            audio.append(np.full((48000, 2), 0.1, np.float32))
            await asyncio.sleep(0.01)
        audio.finish()

    feeder = asyncio.create_task(feed())
    player = MusicPlayer(streaming_loader)
    track = await player.play(tracks("1"))
    assert track is not None
    assert player.read(48000).shape == (48000, 2)
    await feeder
    assert audio.frames == 480000


async def test_listeners_notified_on_track_pause_and_stop():
    events = []
    player = MusicPlayer(FakeLoader())
    player.listeners.append(
        lambda p: events.append((p.current.id if p.active else None, p.playing))
    )
    await player.play(tracks("1", "2"))
    await player.control("pause")
    await player.control("resume")
    await player.control("next")
    player.stop()
    assert events == [("1", True), ("1", False), ("1", True), ("2", True), (None, False)]
