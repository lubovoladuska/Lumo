"""Приложения Windows: поиск по названию, запуск и закрытие."""

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher

from lumo.desktop.actions import ActionError

log = logging.getLogger(__name__)

MATCH_THRESHOLD = 0.72
AMBIGUITY_GAP = 0.04
ALIAS_WEIGHT = 0.95

_TRANSLIT = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
        "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
        "я": "ya",
    }
)  # fmt: skip

# Ярлыки, которые никто не хочет «открыть» голосом.
_JUNK = re.compile(
    r"^(uninstall|удалить|website|help|readme|manual|документация|справка)\b|"
    r"\b(uninstall|uninstaller|manuals?|help file|release notes)\b",
    re.IGNORECASE,
)
_PREFIX = re.compile(r"^(запустить|launch|run)\s+", re.IGNORECASE)

# Группы взаимозаменяемых названий: любое из них находит остальные.
_ALIAS_GROUPS = [
    ["Telegram", "телеграм", "телега", "Telegram Desktop", "64Gram", "AyuGram", "Kotatogram"],
    ["Google Chrome", "хром", "chrome"],
    ["Блокнот", "Notepad"],
    ["Калькулятор", "Calculator"],
    ["Проводник", "File Explorer", "Explorer"],
    ["Параметры", "Настройки", "Settings"],
    ["Discord", "дискорд"],
    ["Steam", "стим"],
    ["Adobe Photoshop", "фотошоп", "Photoshop"],
    ["Word", "ворд"],
    ["Excel", "эксель"],
]


def normalize(text: str) -> str:
    text = _PREFIX.sub("", text.lower().replace("ё", "е"))
    text = re.sub(r"\(.*?\)|\d{4}", " ", text)  # «(64-bit)», «2025»
    return re.sub(r"[^\w]+", " ", text).strip()


def translit(text: str) -> str:
    return normalize(text).translate(_TRANSLIT)


def similarity(query: str, name: str) -> float:
    q, n = normalize(query), normalize(name)
    if not q or not n:
        return 0.0
    scores = [
        SequenceMatcher(None, q, n).ratio(),
        SequenceMatcher(None, translit(q), translit(n)).ratio(),
    ]
    # «telegram» внутри «telegram desktop», «chrome» внутри «google chrome»
    words = translit(n).split()
    if translit(q) in words or (len(q) >= 4 and translit(n).startswith(translit(q))):
        scores.append(0.9)
    return max(scores)


def aliases(query: str) -> list[str]:
    key = normalize(query)
    for group in _ALIAS_GROUPS:
        if key in (normalize(name) for name in group):
            return group
    return []


@dataclass(frozen=True)
class App:
    name: str
    app_id: str


class AppIndex:
    def __init__(self, apps: list[App]) -> None:
        self.apps = [a for a in apps if not _JUNK.search(a.name)]

    @classmethod
    def load(cls) -> "AppIndex":
        """Приложения меню «Пуск» (включая Store) через PowerShell Get-StartApps."""
        command = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "Get-StartApps | ConvertTo-Json -Compress"
        )
        output = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.decode("utf-8-sig", errors="replace")
        data = json.loads(output or "[]")
        if isinstance(data, dict):
            data = [data]
        apps = [App(d["Name"], d["AppID"]) for d in data if d.get("Name") and d.get("AppID")]
        log.info("Найдено приложений: %d", len(apps))
        return cls(apps)

    def ranked(self, query: str) -> list[tuple[float, App]]:
        alternatives = aliases(query)
        best: dict[str, tuple[float, App]] = {}
        for app in self.apps:
            score = similarity(query, app.name)
            if alternatives:
                # Синоним чуть слабее прямого совпадения: «Telegram» важнее «64Gram».
                score = max(
                    score, ALIAS_WEIGHT * max(similarity(a, app.name) for a in alternatives)
                )
            if app.app_id not in best or score > best[app.app_id][0]:
                best[app.app_id] = (score, app)
        return sorted(best.values(), key=lambda item: item[0], reverse=True)

    def find(self, query: str) -> App:
        ranked = self.ranked(query)
        if not ranked or ranked[0][0] < MATCH_THRESHOLD:
            similar = ", ".join(a.name for _, a in ranked[:3])
            hint = f" Похожие: {similar}." if similar else ""
            raise ActionError(f"Не нашёл приложение «{query}».{hint}")
        top_score, top = ranked[0]
        if len(ranked) > 1:
            second_score, second = ranked[1]
            same = normalize(second.name) == normalize(top.name)
            if not same and top_score - second_score < AMBIGUITY_GAP:
                raise ActionError(
                    f"Подходит несколько приложений: {top.name} и {second.name}. Уточните какое."
                )
        return top


def launch(app: App) -> None:
    subprocess.Popen(
        ["explorer.exe", f"shell:AppsFolder\\{app.app_id}"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


# --- закрытие ---

_BROWSER_EXE = {
    "chrome": "chrome.exe",
    "msedge": "msedge.exe",
    "firefox": "firefox.exe",
    "yandex": "browser.exe",
    "opera": "opera.exe",
    "brave": "brave.exe",
    "comet": "comet.exe",
}
BROWSER_WORDS = {"браузер", "browser", "браузер по умолчанию"}


def default_browser_exe() -> str | None:
    import winreg

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        )
        prog_id = winreg.QueryValueEx(key, "ProgId")[0].lower()
    except OSError:
        return None
    for marker, exe in _BROWSER_EXE.items():
        if marker in prog_id:
            return exe
    return None


def close_app(query: str) -> str:
    """Закрывает окна процессов, подходящих под название, штатно (WM_CLOSE)."""
    import psutil

    if normalize(query) in BROWSER_WORDS:
        exe = default_browser_exe()
        if exe is None:
            raise ActionError("Не удалось определить браузер по умолчанию.")
        targets = [exe.removesuffix(".exe")]
    else:
        targets = [query, *aliases(query)]

    pids: dict[int, str] = {}
    for proc in psutil.process_iter(["pid", "name"]):
        name = (proc.info["name"] or "").removesuffix(".exe").removesuffix(".EXE")
        if name and max(similarity(t, name) for t in targets) >= 0.8:
            pids[proc.info["pid"]] = name
    windows = [w for w in _top_windows() if w[1] in pids]
    if not windows:
        raise ActionError(f"Приложение «{query}» не запущено.")
    for hwnd, _pid in windows:
        _close_window(hwnd)
    return f"Закрываю {sorted(set(pids[p] for _, p in windows))[0]}."


def _top_windows() -> list[tuple[int, int]]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    result: list[tuple[int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            result.append((hwnd, pid.value))
        return True

    user32.EnumWindows(callback, 0)
    return result


def _close_window(hwnd: int) -> None:
    import ctypes

    wm_close = 0x0010
    ctypes.windll.user32.PostMessageW(hwnd, wm_close, 0, 0)
