"""Частые простые команды без языковой модели: 0 токенов и без задержки на запрос.

Фраза нормализуется и должна совпасть с шаблоном ЦЕЛИКОМ: любое лишнее слово отправляет
её в LLM. Ложное срабатывание хуже лишнего запроса.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FastCommand:
    skill: str
    params: dict[str, Any] = field(default_factory=dict)
    # Как озвучить результат навыка, если ему нужен полный ответ (например, время).
    format: Callable[[str], str] | None = None


# --- числа словами → цифры ---

_UNITS = {
    "ноль": 0, "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19,
}  # fmt: skip
_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100,
}  # fmt: skip


def _numbers_to_digits(text: str) -> str:
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word in _TENS:
            value = _TENS[word]
            if value < 100 and i + 1 < len(words) and words[i + 1] in _UNITS:
                unit = _UNITS[words[i + 1]]
                if 0 < unit < 10:
                    value += unit
                    i += 1
            out.append(str(value))
        elif word in _UNITS:
            out.append(str(_UNITS[word]))
        else:
            out.append(word)
        i += 1
    return " ".join(out)


_POLITE = re.compile(r"\b(пожалуйста|плиз|please|ну|давай|а)\b")


def normalize(text: str, wake_word: str = "вальтер") -> str:
    text = text.lower().replace("ё", "е").replace("%", " процентов ")
    text = re.sub(rf"^\s*{re.escape(wake_word)}\b", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = _POLITE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _numbers_to_digits(text)


# --- шаблоны ---

_PCT = r"(?: процент\w*)?"
_VOL = r"(?:громкость|звук)(?: системы| компьютера)?"
_SET = r"(?:поставь|поставить|сделай|сделать|установи|выстави|включи) "
_BY = rf"(?: на (?P<step>\d+){_PCT})?"


def _time_text(result: str) -> str:
    data = json.loads(result)
    return f"Сейчас {data['time']}, {data['weekday']}, {data['date']}."


Builder = Callable[[re.Match], FastCommand]

_RULES: list[tuple[re.Pattern, Builder]] = []


def _rule(*patterns: str) -> Callable[[Builder], Builder]:
    """Несколько вариантов фразы — отдельными шаблонами (имена групп в них повторяются)."""

    def register(builder: Builder) -> Builder:
        _RULES.extend((re.compile(pattern), builder) for pattern in patterns)
        return builder

    return register


def _step(m: re.Match) -> dict:
    return {"step": int(m["step"])} if m["step"] else {}


# Голое число («30») сюда не попадает: это может быть ответ на вопрос ассистента.
@_rule(rf"(?:{_SET})?{_VOL} (?:на )?(?P<level>\d+){_PCT}")
def _volume_set(m):
    return FastCommand("pc_volume", {"action": "set", "level": min(int(m["level"]), 100)})


@_rule(
    rf"(?:сделай |сделать )?(?:по)?тише{_BY}",
    rf"(?:убавь|убавить|уменьши|понизь) (?:{_VOL}){_BY}",
    rf"{_VOL} (?:тише|ниже|меньше){_BY}",
)
def _volume_down(m):
    return FastCommand("pc_volume", {"action": "down", **_step(m)})


@_rule(
    rf"(?:сделай |сделать )?(?:по)?громче{_BY}",
    rf"(?:прибавь|прибавить|увеличь|повысь) (?:{_VOL}){_BY}",
    rf"{_VOL} (?:громче|выше|больше){_BY}",
)
def _volume_up(m):
    return FastCommand("pc_volume", {"action": "up", **_step(m)})


@_rule(r"(?:выключи|отключи|убери) звук|без звука|заглуши(?: звук)?|мьют")
def _mute(m):
    return FastCommand("pc_volume", {"action": "mute"})


@_rule(r"(?:включи|верни) звук")
def _unmute(m):
    return FastCommand("pc_volume", {"action": "unmute"})


@_rule(rf"(?:какая|скажи) (?:сейчас )?{_VOL}|{_VOL} сейчас какая|сколько громкость")
def _volume_get(m):
    return FastCommand("pc_volume", {"action": "get"})


@_rule(r"пауза|(?:поставь |поставить )?на паузу|паузу|останови(?: музыку| трек)?")
def _pause(m):
    return FastCommand("media_control", {"action": "pause"})


@_rule(r"продолжи(?:ть)?(?: музыку| играть)?|играй(?: дальше)?|сними с паузы|сними паузу")
def _resume(m):
    return FastCommand("media_control", {"action": "resume"})


@_rule(r"дальше|следующ\w*(?: трек| песн\w*)?|переключи(?: трек| песню)?|скипни|пропусти(?: трек)?")
def _next(m):
    return FastCommand("media_control", {"action": "next"})


@_rule(r"назад|предыдущ\w*(?: трек| песн\w*)?|верни (?:прошлый|предыдущий) трек")
def _previous(m):
    return FastCommand("media_control", {"action": "previous"})


@_rule(r"(?:выключи|останови) музыку|стоп музыка")
def _stop_music(m):
    return FastCommand("media_control", {"action": "stop"})


@_rule(rf"(?:сделай )?музыку (?:по)?тише{_BY}", rf"убавь музыку{_BY}")
def _music_down(m):
    return FastCommand("music_volume", {"action": "down", **_step(m)})


@_rule(rf"(?:сделай )?музыку (?:по)?громче{_BY}", rf"прибавь музыку{_BY}")
def _music_up(m):
    return FastCommand("music_volume", {"action": "up", **_step(m)})


@_rule(rf"(?:{_SET})?(?:громкость музыки|музыку|музыка) (?:на )?(?P<level>\d+){_PCT}")
def _music_set(m):
    return FastCommand("music_volume", {"action": "set", "level": min(int(m["level"]), 100)})


@_rule(r"сверни (?:все|всё)(?: окна)?|покажи рабочий стол|рабочий стол")
def _minimize(m):
    return FastCommand("pc_minimize_all")


@_rule(r"заблокируй(?: компьютер| комп| экран| пк)?|блокировка(?: экрана)?")
def _lock(m):
    return FastCommand("pc_lock")


@_rule(r"(?:сделай |сделать )?скрин(?:шот)?(?: экрана)?|сфоткай экран")
def _screenshot(m):
    return FastCommand("pc_screenshot")


@_rule(r"(?:который|сколько) (?:сейчас )?(?:час|часов|времени)|время|который час сейчас")
def _time(m):
    return FastCommand("get_datetime", format=_time_text)


def match(text: str, wake_word: str = "вальтер") -> FastCommand | None:
    """Быстрая команда, если фраза целиком совпала с шаблоном, иначе ``None`` (→ LLM)."""
    phrase = normalize(text, wake_word)
    if not phrase:
        return None
    for pattern, build in _RULES:
        found = pattern.fullmatch(phrase)
        if found:
            return build(found)
    return None
