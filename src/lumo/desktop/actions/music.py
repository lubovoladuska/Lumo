"""Яндекс Музыка: поиск, «Моя волна», любимое, лайки — и действия ``music.*``.

Работает через неофициальную библиотеку ``yandex-music`` с токеном пользователя.
Вся работа с API изолирована здесь: если Яндекс что-то поменяет, чинить только этот файл.
"""

import asyncio
import logging
from typing import Any

import numpy as np

from lumo.desktop.actions import ActionError, ActionExecutor
from lumo.desktop.actions.system import volume_step
from lumo.desktop.player import MusicPlayer, MusicTrack
from lumo.desktop.streaming import ByteStream, TrackAudio, start_decoding

log = logging.getLogger(__name__)

WAVE = "user:onyourwave"
LIKED_LIMIT = 100
# Сколько ждать очередной кусок трека из сети, прежде чем считать загрузку оборванной.
DOWNLOAD_TIMEOUT = 30
# Сколько ждать первый трек, прежде чем ответить «загружаю» и догрузить в фоне
# (ядро ждёт ответа устройства 15 секунд).
START_WAIT = 8.0
ARTIST_TOP = 30
BAD_TOKEN = "Токен Яндекс Музыки недействителен. Обновите LUMO_YANDEX_MUSIC_TOKEN в .env."
NO_TOKEN = (
    "Яндекс Музыка не подключена: впишите токен в LUMO_YANDEX_MUSIC_TOKEN в файле .env "
    "и перезапустите клиент. Как получить токен — в README."
)


def to_track(raw: Any) -> MusicTrack | None:
    if raw is None or not getattr(raw, "available", True):
        return None
    artists = ", ".join(raw.artists_name()) if raw.artists else ""
    cover = getattr(raw, "cover_uri", None) or ""
    cover_url = f"https://{cover.replace('%%', '400x400')}" if cover else ""
    return MusicTrack(
        str(raw.id), raw.title or "Без названия", artists, raw.duration_ms or 0, raw, cover_url
    )


def to_tracks(raws) -> list[MusicTrack]:
    return [t for t in (to_track(r) for r in raws or []) if t is not None]


def decode_mp3(data: bytes) -> np.ndarray:
    import miniaudio

    decoded = miniaudio.decode(
        data, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=2, sample_rate=48000
    )
    return np.frombuffer(decoded.samples, dtype=np.float32).reshape(-1, 2).copy()


class YandexMusic:
    def __init__(self, token: str) -> None:
        self.token = token
        self._client = None
        self._lock = asyncio.Lock()
        self._wave_batch: str | None = None
        self._http = None  # aiohttp.ClientSession для потоковой загрузки треков

    async def client(self):
        if not self.token:
            raise ActionError(NO_TOKEN)
        async with self._lock:
            if self._client is None:
                from yandex_music import ClientAsync
                from yandex_music.exceptions import UnauthorizedError

                try:
                    self._client = await ClientAsync(self.token, report_unknown_fields=False).init()
                except UnauthorizedError as exc:
                    raise ActionError(BAD_TOKEN) from exc
            return self._client

    # --- поиск ---

    async def search(self, query: str) -> tuple[str, list[MusicTrack]]:
        """Лучший результат поиска → (описание для ответа, треки для очереди)."""
        client = await self.client()
        result = await client.search(query, type_="all")
        if result is None:
            return "", []
        best = result.best
        if best is not None and best.result is not None:
            kind, item = best.type, best.result
            if kind == "track":
                track = to_track(item)
                return (f"трек {track.label}", [track]) if track else ("", [])
            if kind == "artist":
                found = await client.artists_tracks(item.id, page_size=ARTIST_TOP)
                return f"исполнителя {item.name}", to_tracks(found.tracks if found else [])
            if kind == "album":
                album = await client.albums_with_tracks(item.id)
                raws = [t for volume in (album.volumes or []) for t in volume] if album else []
                artists = ", ".join(a.name for a in (item.artists or []))
                return f"альбом {item.title} ({artists})", to_tracks(raws)
            if kind == "playlist":
                shorts = await item.fetch_tracks_async()
                return f"плейлист {item.title}", await self._resolve_shorts(shorts)
        if result.tracks and result.tracks.results:
            track = to_track(result.tracks.results[0])
            return (f"трек {track.label}", [track]) if track else ("", [])
        return "", []

    async def similar(self, seed: MusicTrack) -> list[MusicTrack]:
        """Продолжение после одиночного трека — радио по этому треку."""
        client = await self.client()
        result = await client.rotor_station_tracks(f"track:{seed.id}")
        return to_tracks(s.track for s in (result.sequence if result else []))

    # --- «Моя волна» ---

    async def wave(self, queue_after: str | None = None) -> list[MusicTrack]:
        client = await self.client()
        result = await client.rotor_station_tracks(WAVE, queue=queue_after)
        if result is None:
            return []
        self._wave_batch = result.batch_id
        if queue_after is None:
            await client.rotor_station_feedback_radio_started(WAVE, "desktop-lumo", result.batch_id)
        return to_tracks(s.track for s in result.sequence)

    async def wave_track_started(self, track: MusicTrack) -> None:
        try:
            client = await self.client()
            await client.rotor_station_feedback_track_started(WAVE, track.id, self._wave_batch)
        except Exception:
            log.debug("Не удалось отправить фидбэк волны", exc_info=True)

    # --- любимое ---

    async def liked(self) -> list[MusicTrack]:
        client = await self.client()
        likes = await client.users_likes_tracks()
        if likes is None or not likes.tracks:
            return []
        ids = [short.track_id for short in likes.tracks[:LIKED_LIMIT]]
        return to_tracks(await client.tracks(ids))

    async def like(self, track: MusicTrack) -> None:
        client = await self.client()
        await client.users_likes_tracks_add(track.id)

    # --- звук ---

    async def load(self, track: MusicTrack) -> TrackAudio:
        """Начинает скачивать трек и сразу отдаёт растущий буфер звука: играть можно,
        не дожидаясь конца загрузки (на медленном канале трек качается минуту и больше)."""
        link = await self._direct_link(track)
        stream = ByteStream()
        audio = start_decoding(stream, "mp3")
        download = asyncio.ensure_future(self._download(link, stream))

        def cancel() -> None:
            download.cancel()
            stream.abort()

        audio.on_cancel = cancel
        return audio

    async def _direct_link(self, track: MusicTrack) -> str:
        raw = track.raw
        if raw is None:
            raw = (await (await self.client()).tracks([track.id]))[0]
        infos = await raw.get_download_info_async(get_direct_links=True)
        mp3 = [i for i in infos or [] if i.codec == "mp3"]
        if not mp3:
            raise ActionError("Этот трек недоступен для прослушивания.")
        # 192 кбит/с — хороший звук и всего ~24 КБ/с, чего хватает даже медленному каналу.
        best = min(mp3, key=lambda i: (abs(i.bitrate_in_kbps - 192), -i.bitrate_in_kbps))
        return best.direct_link

    async def _download(self, url: str, stream: ByteStream) -> None:
        import aiohttp

        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=DOWNLOAD_TIMEOUT)
        try:
            async with self._http.get(url, timeout=timeout) as response:
                response.raise_for_status()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    stream.feed(chunk)
        except asyncio.CancelledError:
            stream.abort()
            raise
        except Exception as exc:
            log.warning("Загрузка трека прервалась: %s", exc)
        finally:
            stream.finish()

    async def _resolve_shorts(self, shorts) -> list[MusicTrack]:
        raws = [s.track for s in shorts or [] if s.track is not None]
        missing = [s.track_id for s in shorts or [] if s.track is None]
        if missing:
            raws += await (await self.client()).tracks(missing[:LIKED_LIMIT])
        return to_tracks(raws)


class MusicActions:
    """Действия ``music.*`` ПК-клиента."""

    def __init__(self, source: YandexMusic, player: MusicPlayer, engine: Any) -> None:
        self.source = source
        self.player = player
        self.engine = engine

    async def play(self, query: str) -> str:
        what, tracks = await self.source.search(query)
        if not tracks:
            return f"Ничего не нашёл по запросу «{query}»."
        more = self._similar_more if len(tracks) == 1 else None
        name = what if len(tracks) > 1 else tracks[0].label
        return await self._start(self.player.play(tracks, what, more=more), name)

    async def wave(self) -> str:
        tracks = await self.source.wave()
        if not tracks:
            return "Моя волна сейчас недоступна."
        playing = self.player.play(
            tracks, "Моя волна", more=self._wave_more, on_start=self.source.wave_track_started
        )
        return await self._start(playing, "Мою волну")

    async def liked(self) -> str:
        tracks = await self.source.liked()
        if not tracks:
            return "В «Мне нравится» пока пусто."
        return await self._start(self.player.play(tracks, "Мне нравится"), "любимые треки")

    async def _start(self, playing, name: str) -> str:
        """Ждёт первый трек недолго; если интернет медленный — догружает в фоне."""
        task = asyncio.ensure_future(playing)
        try:
            track = await asyncio.wait_for(asyncio.shield(task), START_WAIT)
        except TimeoutError:
            task.add_done_callback(_log_start_failure)
            return f"Загружаю {name}, заиграет через несколько секунд."
        if track is None:
            return "Не получилось включить: треки недоступны или не загрузились."
        return (
            f"Включаю {name}. Играет {track.label}." if name != track.label else f"Включаю {name}."
        )

    async def like(self) -> str:
        track = self.player.current
        if track is None or not self.player.active:
            return "Сейчас ничего не играет."
        await self.source.like(track)
        return f"Добавил {track.label} в «Мне нравится»."

    def now_playing(self) -> str:
        return self.player.now_playing()

    def volume(self, action: str, level: int | None = None, step: int | None = None) -> str:
        current = round(self.engine.music_volume * 100)
        if action == "get":
            return f"Громкость музыки {current}%."
        if action == "set":
            target = level if level is not None else current
        else:
            delta = step or volume_step(current, action)
            target = current + (delta if action == "up" else -delta)
        self.engine.music_volume = max(0, min(100, target)) / 100
        return f"Громкость музыки {round(self.engine.music_volume * 100)}%."

    async def _wave_more(self, queue: list[MusicTrack]) -> list[MusicTrack]:
        return await self.source.wave(queue_after=queue[-1].id if queue else None)

    async def _similar_more(self, queue: list[MusicTrack]) -> list[MusicTrack]:
        if len(queue) > 1:
            return []  # продолжение уже подгружено
        return await self.source.similar(queue[0])

    def register(self, executor: ActionExecutor) -> None:
        executor.register_all(
            "music",
            {
                "play": self.play,
                "wave": self.wave,
                "liked": self.liked,
                "like": self.like,
                "now_playing": self.now_playing,
                "volume": self.volume,
            },
        )


def _log_start_failure(task: "asyncio.Future") -> None:
    if not task.cancelled() and task.exception() is None and task.result() is None:
        log.warning("Музыка так и не загрузилась")
