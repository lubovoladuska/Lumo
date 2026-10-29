import pytest

from lumo.desktop.echo import ECHO_MEMORY, EchoGuard


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def guard():
    clock = Clock()
    g = EchoGuard(clock)
    g.clock = clock
    g.spoken("Сейчас двенадцать часов пять минут. Хотите поставить напоминание?")
    return g


@pytest.mark.parametrize(
    "heard",
    [
        "Хотите поставить напоминание?",  # хвост ответа
        "сейчас двенадцать часов пять минут",  # начало ответа
        "хотите поставить напоминания",  # искажение распознавания
    ],
)
def test_own_speech_is_echo(guard, heard):
    assert guard.is_echo(heard)


@pytest.mark.parametrize(
    "heard",
    [
        "да",  # короткий ответ на вопрос
        "нет, не надо",
        "Поставь таймер на десять минут",
        "Какая завтра погода в Москве?",
    ],
)
def test_user_speech_is_not_echo(guard, heard):
    assert not guard.is_echo(heard)


def test_old_speech_is_forgotten(guard):
    guard.clock.t += ECHO_MEMORY + 1
    assert not guard.is_echo("Хотите поставить напоминание?")
