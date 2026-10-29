"""Музыкальные действия на подставных ответах API Яндекс Музыки (без сети и токена)."""

import io
import wave
from types import SimpleNamespace as NS

import numpy as np
import pytest

pytest.importorskip("miniaudio")

from lumo.desktop.actions import ActionError  # noqa: E402
from lumo.desktop.actions.music import (  # noqa: E402
    NO_TOKEN,
    MusicActions,
    YandexMusic,
    decode_mp3,
    to_track,
)
from lumo.desktop.player import MusicPlayer  # noqa: E402


def raw_track(id_, title, artist="Кино", available=True):
    return NS(
        id=id_,
        title=title,
        available=available,
        artists=[NS(name=artist)],
        artists_name=lambda: [artist],
        duration_ms=200000,
    )


class FakeClient:
    def __init__(self, best=None, tracks=None):
        self.best = best
        self.tracks_results = tracks or []
        self.liked_added = []
        self.feedback = []

    async def search(self, text, type_="all"):
        return NS(best=self.best, tracks=NS(results=self.tracks_results))

    async def artists_tracks(self, artist_id, page_size=20):
        return NS(tracks=[raw_track(1, "Группа крови"), raw_track(2, "Кукушка")])

    async def albums_with_tracks(self, album_id):
        return NS(
            volumes=[[raw_track(3, "Звезда по имени Солнце")], [raw_track(4, "Пачка сигарет")]]
        )

    async def rotor_station_tracks(self, station, queue=None):
        n = 10 if queue is None else int(queue) + 1
        return NS(batch_id="b1", sequence=[NS(track=raw_track(n, f"Волна {n}", "Разные"))])

    async def rotor_station_feedback_radio_started(self, station, from_, batch_id):
        self.feedback.append(("radio", station))

    async def rotor_station_feedback_track_started(self, station, track_id, batch_id):
        self.feedback.append(("track", track_id))

    async def users_likes_tracks(self):
        return NS(tracks=[NS(track_id=5), NS(track_id=6)])

    async def tracks(self, ids):
        return [raw_track(i, f"Любимая {i}", "Сплин") for i in ids]

    async def users_likes_tracks_add(self, track_id):
        self.liked_added.append(track_id)


def source_with(client):
    source = YandexMusic("token")
    source._client = client
    return source


async def fake_loader(track):
    return np.zeros((48000, 2), np.float32)


@pytest.fixture
def engine():
    return NS(music_volume=0.7)


def actions(client, engine):
    return MusicActions(source_with(client), MusicPlayer(fake_loader), engine)


def test_unavailable_track_skipped():
    assert to_track(raw_track(1, "x", available=False)) is None
    assert to_track(raw_track(1, "Кукушка")).label == "Кино — Кукушка"


async def test_play_artist(engine):
    client = FakeClient(best=NS(type="artist", result=NS(id=7, name="Кино")))
    music = actions(client, engine)
    assert await music.play("кино") == "Включаю исполнителя Кино."
    assert [t.title for t in music.player.queue] == ["Группа крови", "Кукушка"]


async def test_play_album_flattens_volumes(engine):
    best = NS(type="album", result=NS(id=1, title="Звезда", artists=[NS(name="Кино")]))
    music = actions(FakeClient(best=best), engine)
    assert "альбом Звезда (Кино)" in await music.play("звезда")
    assert len(music.player.queue) == 2


async def test_play_single_track_then_similar(engine):
    best = NS(type="track", result=raw_track(42, "Кукушка"))
    music = actions(FakeClient(best=best), engine)
    assert await music.play("кукушка") == "Включаю Кино — Кукушка."
    assert len(music.player.queue) > 1  # дальше радио по треку


async def test_nothing_found(engine):
    music = actions(FakeClient(), engine)
    assert "Ничего не нашёл" in await music.play("абракадабра")
    assert not music.player.active


async def test_wave_sends_feedback_and_extends(engine):
    client = FakeClient()
    music = actions(client, engine)
    assert "Мою волну" in await music.wave()
    assert ("radio", "user:onyourwave") in client.feedback
    assert len(music.player.queue) >= 2  # подгрузилось продолжение


async def test_liked_and_like(engine):
    client = FakeClient()
    music = actions(client, engine)
    assert "любимые" in await music.liked()
    assert music.player.current.artists == "Сплин"
    assert "Мне нравится" in await music.like()
    assert client.liked_added == ["5"]


def test_music_volume(engine):
    music = MusicActions(source_with(FakeClient()), MusicPlayer(fake_loader), engine)
    assert music.volume("get") == "Громкость музыки 70%."
    assert music.volume("down", step=20) == "Громкость музыки 50%."
    assert music.volume("set", level=150) == "Громкость музыки 100%."


async def test_no_token_hint():
    with pytest.raises(ActionError) as exc:
        await YandexMusic("").client()
    assert str(exc.value) == NO_TOKEN


def test_decode_audio_to_stereo_48k():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.sin(np.arange(16000) / 10) * 10000).astype(np.int16).tobytes())
    audio = decode_mp3(buffer.getvalue())  # miniaudio понимает и wav
    assert audio.shape[1] == 2
    assert abs(len(audio) - 48000) < 100
