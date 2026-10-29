"""Системные действия Windows: сайты, звук, медиа-клавиши, питание, окна, скриншоты."""

import ctypes
import subprocess
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

from lumo.desktop.actions import ActionError

# --- сайты и поиск ---

SITES = {
    "youtube.com": ["ютуб", "ютьюб", "youtube", "ютубчик"],
    "music.youtube.com": ["ютуб музыка", "youtube music"],
    "vk.com": ["вк", "вконтакте", "vk"],
    "google.com": ["гугл", "google"],
    "ya.ru": ["яндекс", "yandex"],
    "mail.google.com": ["гмейл", "gmail", "почта гугл"],
    "mail.yandex.ru": ["яндекс почта", "почта яндекс", "почта"],
    "web.telegram.org": ["телеграм веб", "telegram web"],
    "github.com": ["гитхаб", "github"],
    "ru.wikipedia.org": ["википедия", "wikipedia", "вики"],
    "kinopoisk.ru": ["кинопоиск"],
    "ozon.ru": ["озон", "ozon"],
    "wildberries.ru": ["вайлдберриз", "вб", "wildberries"],
    "avito.ru": ["авито", "avito"],
    "twitch.tv": ["твич", "twitch"],
    "chatgpt.com": ["чатгпт", "chatgpt", "чат гпт"],
    "music.yandex.ru": ["яндекс музыка"],
    "translate.google.com": ["переводчик", "гугл переводчик"],
}
SEARCH_URLS = {
    "yandex": "https://ya.ru/search/?text={}",
    "google": "https://www.google.com/search?q={}",
}


def site_url(site: str) -> str:
    key = site.strip().lower().replace("ё", "е")
    for domain, names in SITES.items():
        if key in names or key == domain:
            return f"https://{domain}"
    if "." in key and " " not in key:
        return key if key.startswith(("http://", "https://")) else f"https://{key}"
    raise ActionError(f"Не знаю сайт «{site}». Могу поискать его в интернете.")


def search_url(query: str, engine: str = "yandex") -> str:
    return SEARCH_URLS.get(engine, SEARCH_URLS["yandex"]).format(quote_plus(query))


def open_url(url: str) -> None:
    webbrowser.open(url)


# --- звук ---


class Volume:
    """Системная громкость через Core Audio (pycaw). Вызывается из рабочего потока."""

    def _endpoint(self):
        import comtypes
        from pycaw.pycaw import AudioUtilities

        comtypes.CoInitialize()
        return AudioUtilities.GetSpeakers().EndpointVolume

    def get(self) -> int:
        return round(self._endpoint().GetMasterVolumeLevelScalar() * 100)

    def set(self, level: int) -> int:
        level = max(0, min(100, level))
        endpoint = self._endpoint()
        endpoint.SetMute(0, None)
        endpoint.SetMasterVolumeLevelScalar(level / 100, None)
        return level

    def mute(self, muted: bool) -> None:
        self._endpoint().SetMute(1 if muted else 0, None)

    def handle(self, action: str, level: int | None = None, step: int = 10) -> str:
        if action == "get":
            return f"Громкость {self.get()}%."
        if action == "mute":
            self.mute(True)
            return "Звук выключен."
        if action == "unmute":
            self.mute(False)
            return "Звук включён."
        if action == "set":
            return f"Громкость {self.set(level or 0)}%."
        delta = step if action == "up" else -step
        return f"Громкость {self.set(self.get() + delta)}%."


# --- клавиши ---

VK_MEDIA = {
    "toggle": 0xB3,
    "pause": 0xB3,
    "resume": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
}
KEYEVENTF_KEYUP = 0x0002


def press_key(vk: int) -> None:
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def media_key(action: str) -> str:
    press_key(VK_MEDIA[action])
    return {
        "next": "Следующий трек.",
        "previous": "Предыдущий трек.",
        "stop": "Остановлено.",
    }.get(action, "Готово.")


# --- окна ---


def minimize_all() -> str:
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    comtypes.client.CreateObject("Shell.Application").MinimizeAll()
    return "Все окна свёрнуты."


def active_window() -> str:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    if not hwnd or length == 0:
        return "Сейчас нет активного окна с заголовком."
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return f"Активное окно: {buffer.value}."


def screenshots_dir() -> Path:
    return Path.home() / "Pictures" / "Lumo"


def screenshot(now: datetime | None = None) -> str:
    import mss
    import mss.tools

    folder = screenshots_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot-{(now or datetime.now()):%Y-%m-%d-%H%M%S}.png"
    with mss.mss() as grabber:
        shot = grabber.grab(grabber.monitors[0])  # все мониторы
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))
    return f"Скриншот сохранён в папку Изображения, Lumo: {path.name}."


# --- питание ---


def shutdown_command(action: str, delay_minutes: int) -> list[str]:
    flag = {"shutdown": "/s", "restart": "/r"}[action]
    return ["shutdown", flag, "/t", str(max(0, delay_minutes) * 60)]


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def power(action: str, delay_minutes: int = 0, now: datetime | None = None) -> str:
    if action == "sleep":
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
        return "Компьютер уходит в сон."
    result = _run(shutdown_command(action, delay_minutes))
    if result.returncode != 0:
        if result.returncode == 1190:  # уже запланировано
            raise ActionError("Выключение уже запланировано. Сначала отмените его.")
        raise ActionError("Windows не приняла команду выключения.")
    verb = "выключится" if action == "shutdown" else "перезагрузится"
    if delay_minutes == 0:
        return f"Компьютер {verb} сейчас."
    at = (now or datetime.now()) + timedelta(minutes=delay_minutes)
    return f"Компьютер {verb} в {at:%H:%M}."


def cancel_power() -> str:
    if _run(["shutdown", "/a"]).returncode != 0:
        return "Запланированного выключения нет."
    return "Выключение отменено."


def lock() -> str:
    ctypes.windll.user32.LockWorkStation()
    return "Компьютер заблокирован."
