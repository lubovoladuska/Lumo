import pytest

pytest.importorskip("num2words")

from lumo.desktop.speech_text import MAX_CHUNK, normalize, split_phrases  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Сейчас 12:05.", "Сейчас двенадцать ноль пять."),
        ("Будильник на 09:00", "Будильник на девять ноль-ноль"),
        ("в 18:30", "в восемнадцать тридцать"),
        ("Будет 4", "Будет четыре"),
        ("Это 2,5 часа", "Это два и пять часа"),
        ("Готово ✅🙂", "Готово"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_split_phrases():
    assert split_phrases("Первое. Второе! Третье?") == ["Первое.", "Второе!", "Третье?"]
    long = "слово " * 400
    assert all(len(p) <= MAX_CHUNK for p in split_phrases(long))
