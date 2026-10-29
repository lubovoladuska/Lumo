"""Отсев собственного эха: распознанная фраза, повторяющая недавнюю речь ассистента."""

import re
import time
from collections import deque
from difflib import SequenceMatcher

# Сколько секунд после окончания речи фраза ещё может оказаться эхом.
ECHO_MEMORY = 20.0
MIN_WORDS = 3
WORD_OVERLAP = 0.6
SEQUENCE_RATIO = 0.6


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zа-яё0-9]+", text.lower().replace("ё", "е"))


class EchoGuard:
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._spoken: deque[tuple[float, list[str]]] = deque(maxlen=5)

    def spoken(self, text: str) -> None:
        """Запоминает, что сказал ассистент."""
        self._spoken.append((self._clock(), _words(text)))

    def is_echo(self, heard: str) -> bool:
        heard_words = _words(heard)
        if len(heard_words) < MIN_WORDS:
            # Короткие ответы («да», «нет, не надо») почти всегда от человека.
            return False
        now = self._clock()
        for spoken_at, spoken_words in self._spoken:
            if now - spoken_at > ECHO_MEMORY or not spoken_words:
                continue
            vocabulary = set(spoken_words)
            overlap = sum(w in vocabulary for w in heard_words) / len(heard_words)
            ratio = SequenceMatcher(None, " ".join(heard_words), " ".join(spoken_words)).ratio()
            # Эхо часто ловится обрывком, поэтому сравниваем и с подстрокой той же длины.
            partial = _best_window_ratio(heard_words, spoken_words)
            if overlap >= WORD_OVERLAP or ratio >= SEQUENCE_RATIO or partial >= SEQUENCE_RATIO:
                return True
        return False


def _best_window_ratio(heard: list[str], spoken: list[str]) -> float:
    size = len(heard)
    if size > len(spoken):
        return 0.0
    target = " ".join(heard)
    return max(
        SequenceMatcher(None, target, " ".join(spoken[i : i + size])).ratio()
        for i in range(len(spoken) - size + 1)
    )
