import math

import numpy as np
import pytest

pytest.importorskip("sounddevice")

from lumo.config import Settings  # noqa: E402
from lumo.desktop.audio_engine import BLOCK, _Decimator, _Playback, _resample  # noqa: E402
from lumo.desktop.voice_mode import VoiceAssistant, _is_stop  # noqa: E402


def test_playback_reads_in_order_and_pads_with_silence():
    pb = _Playback()
    pb.put(np.ones(3, np.float32))
    pb.put(np.full(2, 2, np.float32))
    assert not pb.idle.is_set()
    assert pb.read(4).tolist() == [1, 1, 1, 2]
    assert pb.read(3).tolist() == [2, 0, 0]
    assert pb.idle.is_set()


def test_playback_clear_stops_immediately():
    pb = _Playback()
    pb.put(np.ones(1000, np.float32))
    pb.clear()
    assert pb.idle.is_set()
    assert not pb.read(10).any()


def test_decimator_keeps_remainder():
    d = _Decimator()
    assert len(d.push(np.ones(4, np.float32))) == 1
    assert len(d.push(np.ones(2, np.float32))) == 1


def test_resample_length():
    assert len(_resample(np.zeros(24000, np.float32), 24000, 48000)) == 48000


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Стоп", True), ("хватит!", True), ("стоп, поставь таймер", False), ("", False)],
)
def test_stop_words(text, expected):
    assert _is_stop(text) is expected


def test_strip_wake_word():
    assistant = VoiceAssistant(Settings(_env_file=None))
    assert assistant._strip_wake_word("Вальтер, поставь таймер.") == "Поставь таймер."
    assert assistant._strip_wake_word("Вальтер.") == ""
    assert assistant._strip_wake_word("поставь таймер") == "Поставь таймер"


def test_webrtc_aec_suppresses_echo_but_keeps_user():
    pytest.importorskip("livekit")
    from lumo.desktop.audio_engine import _LivekitAPM

    rate = 48000
    t = np.arange(rate * 4) / rate
    rng = np.random.default_rng(1)
    # «Речь» ассистента — модулированный шум, как у голоса; пользователь — другой сигнал.
    far = (rng.normal(0, 0.2, len(t)) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)
    delay = int(0.06 * rate)
    mic = np.zeros_like(far)
    mic[delay:] = 0.5 * far[:-delay]
    user = (0.2 * np.sin(2 * np.pi * 300 * t) * (t > 3)).astype(np.float32)
    mic += user

    apm = _LivekitAPM()
    out = np.zeros_like(mic)
    for i in range(0, len(mic) - BLOCK + 1, BLOCK):
        apm.reverse(far[i : i + BLOCK])
        out[i : i + BLOCK] = apm.capture(mic[i : i + BLOCK], 60)

    def db(x):
        return 20 * math.log10(max(float(np.sqrt(np.mean(x**2))), 1e-9))

    echo_only = slice(int(rate * 1.5), int(rate * 3))
    assert db(mic[echo_only]) - db(out[echo_only]) > 15
    # голос пользователя после подавления остаётся слышимым
    assert db(out[int(rate * 3.2) :]) > db(out[echo_only]) + 10
