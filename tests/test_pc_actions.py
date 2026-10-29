import sys
from datetime import datetime

import pytest

from lumo.desktop.actions import ActionError
from lumo.desktop.actions.apps import App, AppIndex, normalize
from lumo.desktop.actions.system import power, search_url, shutdown_command, site_url

APPS = AppIndex(
    [
        App("64Gram", "gram"),
        App("Google Chrome", "chrome"),
        App("Блокнот", "notepad"),
        App("Калькулятор", "calc"),
        App("Discord", "discord"),
        App("Uninstall Discord", "discord-uninstall"),
        App("Запустить Need for Speed Most Wanted", "nfs"),
        App("Python 3.14 (64-bit)", "py"),
        App("Python 3.14 Manuals (64-bit)", "pydoc"),
        App("Adobe Photoshop 2025", "ps"),
        App("Adobe Illustrator 2025", "ai"),
    ]
)


@pytest.mark.parametrize(
    ("query", "app_id"),
    [
        ("Telegram", "gram"),  # форк Telegram через синонимы
        ("телеграм", "gram"),
        ("Google Chrome", "chrome"),
        ("хром", "chrome"),
        ("chrome", "chrome"),
        ("блокнот", "notepad"),
        ("Notepad", "notepad"),
        ("дискорд", "discord"),
        ("Need for Speed", "nfs"),
        ("фотошоп", "ps"),
        ("python", "py"),
    ],
)
def test_app_found(query, app_id):
    assert APPS.find(query).app_id == app_id


def test_junk_shortcuts_ignored():
    assert all("Uninstall" not in a.name and "Manuals" not in a.name for a in APPS.apps)


def test_app_not_found_suggests():
    with pytest.raises(ActionError) as exc:
        APPS.find("Spotify")
    assert "Не нашёл" in str(exc.value)


def test_normalize():
    assert normalize("Запустить Need for Speed") == "need for speed"
    assert normalize("Python 3.14 (64-bit)") == "python 3 14"


@pytest.mark.parametrize(
    ("site", "url"),
    [
        ("ютуб", "https://youtube.com"),
        ("ВК", "https://vk.com"),
        ("github.com", "https://github.com"),
        ("https://example.org/x", "https://example.org/x"),
    ],
)
def test_site_url(site, url):
    assert site_url(site) == url


def test_unknown_site():
    with pytest.raises(ActionError):
        site_url("мой любимый сайт")


def test_search_url():
    assert (
        search_url("рецепт плова")
        == "https://ya.ru/search/?text=%D1%80%D0%B5%D1%86%D0%B5%D0%BF%D1%82+%D0%BF%D0%BB%D0%BE%D0%B2%D0%B0"
    )
    assert search_url("x", "google").startswith("https://www.google.com/search?q=x")


def test_shutdown_command():
    assert shutdown_command("shutdown", 60) == ["shutdown", "/s", "/t", "3600"]
    assert shutdown_command("restart", 0) == ["shutdown", "/r", "/t", "0"]


def test_power_reports_time(monkeypatch):
    calls = []

    class Done:
        returncode = 0

    monkeypatch.setattr("lumo.desktop.actions.system._run", lambda cmd: calls.append(cmd) or Done())
    text = power("shutdown", 60, now=datetime(2026, 10, 29, 14, 50))
    assert text == "Компьютер выключится в 15:50."
    assert calls == [["shutdown", "/s", "/t", "3600"]]


@pytest.mark.skipif(sys.platform != "win32", reason="только Windows")
def test_real_start_menu_index_loads():
    index = AppIndex.load()
    assert len(index.apps) > 0


def test_direct_name_beats_alias():
    index = AppIndex([App("64Gram", "gram"), App("Telegram", "tg")])
    assert index.find("телеграм").app_id == "tg"


class FakeVolume:
    """Имитирует Windows: клавиша меняет громкость на 2%, mute — переключатель."""

    def __init__(self, level=40, muted=False):
        self.level, self.is_muted, self.pressed = level, muted, []

    def press(self, vk):
        from lumo.desktop.actions import system

        self.pressed.append(vk)
        if vk == system.VK_VOLUME_UP:
            self.level = min(100, self.level + 2)
        elif vk == system.VK_VOLUME_DOWN:
            self.level = max(0, self.level - 2)
        elif vk == system.VK_VOLUME_MUTE:
            self.is_muted = not self.is_muted


@pytest.fixture
def volume(monkeypatch):
    from lumo.desktop.actions.system import Volume

    fake = FakeVolume()
    vol = Volume(press=fake.press, pause=0)
    monkeypatch.setattr(Volume, "get", lambda self: fake.level)
    monkeypatch.setattr(Volume, "muted", lambda self: fake.is_muted)
    monkeypatch.setattr("lumo.desktop.actions.system.time.sleep", lambda s: None)
    return vol, fake


def test_volume_set_presses_keys_like_touchpad(volume):
    vol, fake = volume
    assert vol.handle("set", level=50) == "Громкость 50%."
    assert len(fake.pressed) == 5
    assert vol.handle("down", step=10) == "Громкость 40%."
    assert vol.handle("set", level=0) == "Громкость 0%."


def test_volume_set_unmutes_and_mute_is_idempotent(volume):
    vol, fake = volume
    fake.is_muted = True
    vol.handle("set", level=40)
    assert not fake.is_muted
    vol.handle("mute")
    vol.handle("mute")
    assert fake.is_muted  # второй раз не переключил обратно


@pytest.mark.parametrize(
    ("current", "action", "step"),
    [
        (80, "down", 10),
        (30, "down", 4),
        (10, "down", 2),
        (10, "up", 2),
        (16, "up", 4),
        (45, "up", 10),
    ],
)
def test_volume_step_depends_on_level(current, action, step):
    from lumo.desktop.actions.system import volume_step

    assert volume_step(current, action) == step


def test_quieter_at_low_volume_does_not_mute(volume):
    vol, fake = volume
    fake.level = 10
    assert vol.handle("down") == "Громкость 8%."
    assert vol.handle("down", step=10) == "Громкость 0%."  # явное число — как сказано
