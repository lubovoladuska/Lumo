"""Слово-активатор. Сейчас на Vosk с грамматикой из одного слова, позже — openWakeWord."""

import json
from pathlib import Path
from typing import Protocol

from lumo.desktop.audio import SAMPLE_RATE


class WakeWordDetector(Protocol):
    def process(self, frame: bytes) -> bool:
        """Принимает кадр int16 16 кГц, возвращает True, если прозвучало слово-активатор."""
        ...

    def reset(self) -> None: ...


class VoskWakeWord:
    """Распознаёт речь только из словаря [слово, <неизвестно>] и срабатывает по частичному
    результату — так реакция быстрая, и команду можно говорить без паузы после имени."""

    def __init__(self, model_path: Path, word: str = "вальтер") -> None:
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        self.word = word.lower()
        self._model = Model(str(model_path))
        self._recognizer = KaldiRecognizer(
            self._model, SAMPLE_RATE, json.dumps([self.word, "[unk]"], ensure_ascii=False)
        )

    def process(self, frame: bytes) -> bool:
        if self._recognizer.AcceptWaveform(frame):
            text = json.loads(self._recognizer.Result()).get("text", "")
        else:
            text = json.loads(self._recognizer.PartialResult()).get("partial", "")
        if self.word in text.split():
            self.reset()
            return True
        return False

    def reset(self) -> None:
        self._recognizer.Reset()
