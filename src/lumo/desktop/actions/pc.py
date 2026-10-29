"""Регистрация действий ``pc.*`` для исполнителя ПК-клиента."""

import asyncio
import logging
from typing import Protocol

from lumo.desktop.actions import ActionExecutor, system
from lumo.desktop.actions import apps as apps_mod

log = logging.getLogger(__name__)


class MediaPlayer(Protocol):
    """Встроенный плеер: если он что-то играет, «пауза» относится к нему."""

    @property
    def active(self) -> bool: ...

    async def control(self, action: str) -> str: ...


class PcActions:
    def __init__(self, search_engine: str = "yandex", player: MediaPlayer | None = None) -> None:
        self.search_engine = search_engine
        self.player = player
        self.volume = system.Volume()
        self._index: apps_mod.AppIndex | None = None
        self._index_lock = asyncio.Lock()

    async def index(self) -> apps_mod.AppIndex:
        async with self._index_lock:
            if self._index is None:
                self._index = await asyncio.to_thread(apps_mod.AppIndex.load)
            return self._index

    async def app_open(self, name: str) -> str:
        if apps_mod.normalize(name) in apps_mod.BROWSER_WORDS:
            await asyncio.to_thread(system.open_url, "about:blank")
            return "Открываю браузер."
        app = (await self.index()).find(name)
        await asyncio.to_thread(apps_mod.launch, app)
        return f"Запускаю {app.name}."

    def site_open(self, site: str) -> str:
        url = system.site_url(site)
        system.open_url(url)
        return f"Открываю {url.removeprefix('https://')}."

    def web_search(self, query: str) -> str:
        system.open_url(system.search_url(query, self.search_engine))
        return f"Ищу «{query}»."

    async def media(self, action: str) -> str:
        if self.player is not None and self.player.active:
            return await self.player.control(action)
        return await asyncio.to_thread(system.media_key, action)

    def register(self, executor: ActionExecutor) -> None:
        executor.register_all(
            "pc",
            {
                "app_open": self.app_open,
                "app_close": apps_mod.close_app,
                "site_open": self.site_open,
                "web_search": self.web_search,
                "volume": self.volume.handle,
                "media": self.media,
                "power": system.power,
                "power_cancel": system.cancel_power,
                "lock": system.lock,
                "minimize_all": system.minimize_all,
                "screenshot": system.screenshot,
                "active_window": system.active_window,
            },
        )
