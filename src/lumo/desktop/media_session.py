"""Медиапанель Windows (System Media Transport Controls) для встроенного плеера.

Трек появляется над ползунком громкости, как у браузера или Spotify: название,
исполнитель, обложка и кнопки ⏮ ⏯ ⏭. Медиа-клавиши и тачпад тоже управляют плеером.
Без пакетов winrt (не Windows) панель просто не показывается.
"""

import asyncio
import logging
from collections.abc import Callable

from lumo.desktop.player import MusicPlayer

log = logging.getLogger(__name__)

ButtonHandler = Callable[[str], None]


class WindowsMediaSession:
    def __init__(self, on_button: ButtonHandler) -> None:
        self.on_button = on_button
        self._smtc = None
        self._player = None  # MediaPlayer держим, иначе панель исчезнет
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shown_track: str | None = None

    def start(self) -> bool:
        try:
            from winrt.windows.media.playback import MediaPlayer
        except ImportError:
            log.info("Медиапанель Windows недоступна (нет пакетов winrt)")
            return False
        self._loop = asyncio.get_running_loop()
        self._player = MediaPlayer()
        # Сам MediaPlayer ничего не играет — нам нужны только его элементы управления.
        self._player.command_manager.is_enabled = False
        smtc = self._player.system_media_transport_controls
        smtc.is_play_enabled = True
        smtc.is_pause_enabled = True
        smtc.is_next_enabled = True
        smtc.is_previous_enabled = True
        smtc.is_stop_enabled = True
        smtc.is_enabled = False  # покажем, когда что-то заиграет
        smtc.add_button_pressed(self._pressed)
        self._smtc = smtc
        return True

    def update(self, player: MusicPlayer) -> None:
        """Подписчик плеера: отражает текущий трек и паузу в панели."""
        if self._smtc is None:
            return
        from winrt.windows.media import MediaPlaybackStatus, MediaPlaybackType

        track = player.current if player.active else None
        smtc = self._smtc
        if track is None:
            smtc.playback_status = MediaPlaybackStatus.STOPPED
            smtc.is_enabled = False
            self._shown_track = None
            return
        smtc.is_enabled = True
        smtc.playback_status = (
            MediaPlaybackStatus.PLAYING if player.playing else MediaPlaybackStatus.PAUSED
        )
        if self._shown_track == track.id:
            return
        self._shown_track = track.id
        updater = smtc.display_updater
        updater.type = MediaPlaybackType.MUSIC
        updater.music_properties.title = track.title
        updater.music_properties.artist = track.artists
        updater.music_properties.album_title = player.title
        if track.cover_url:
            from winrt.windows.foundation import Uri
            from winrt.windows.storage.streams import RandomAccessStreamReference

            updater.thumbnail = RandomAccessStreamReference.create_from_uri(Uri(track.cover_url))
        updater.update()

    def _pressed(self, sender, args) -> None:  # noqa: ARG002
        # Вызывается из потока Windows — передаём в asyncio.
        from winrt.windows.media import SystemMediaTransportControlsButton as Button

        action = {
            Button.PLAY: "resume",
            Button.PAUSE: "pause",
            Button.NEXT: "next",
            Button.PREVIOUS: "previous",
            Button.STOP: "stop",
        }.get(args.button)
        if action and self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_button, action)
