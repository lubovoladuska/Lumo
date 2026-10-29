"""Подготовка текста к озвучиванию: числа и время словами, разбивка на фразы."""

import re

from num2words import num2words

_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_NUMBER = re.compile(r"(?<![\w.,])-?\d+(?:[.,]\d+)?(?![\w])")
_ALLOWED = re.compile(r"[^0-9A-Za-zА-Яа-яЁё\s.,!?;:()«»\"'—–\-+%]")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
MAX_CHUNK = 800


def _words(number: int) -> str:
    return num2words(number, lang="ru")


def _time_to_words(match: re.Match) -> str:
    hours, minutes = int(match.group(1)), int(match.group(2))
    if minutes == 0:
        tail = "ноль-ноль"
    elif minutes < 10:
        tail = f"ноль {_words(minutes)}"
    else:
        tail = _words(minutes)
    return f"{_words(hours)} {tail}"


def _number_to_words(match: re.Match) -> str:
    raw = match.group(0).replace(",", ".")
    if "." in raw:
        whole, frac = raw.split(".")
        return f"{_words(int(whole))} и {_words(int(frac))}"
    return _words(int(raw))


def normalize(text: str) -> str:
    text = _ALLOWED.sub(" ", text)
    text = _TIME.sub(_time_to_words, text)
    text = text.replace("%", " процентов")
    text = _NUMBER.sub(_number_to_words, text)
    return re.sub(r"\s{2,}", " ", text).strip()


def split_phrases(text: str) -> list[str]:
    """Режет текст на фразы, чтобы озвучивание можно было прервать между ними."""
    phrases: list[str] = []
    for sentence in _SENTENCE.split(text.strip()):
        while len(sentence) > MAX_CHUNK:
            cut = sentence.rfind(" ", 0, MAX_CHUNK)
            cut = cut if cut > 0 else MAX_CHUNK
            phrases.append(sentence[:cut])
            sentence = sentence[cut:].strip()
        if sentence:
            phrases.append(sentence)
    return phrases
