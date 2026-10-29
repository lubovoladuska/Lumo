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


def test_mix_voice_stays_full_volume_while_music_ducks():
    from lumo.desktop.audio_engine import DUCK_LEVEL, mix

    frames = 48000
    voice = np.full(frames, 0.1, np.float32)
    music = np.full((frames, 2), 0.5, np.float32)
    out, gain = mix(voice, music, volume=1.0, gain=1.0, target=DUCK_LEVEL)
    assert gain == pytest.approx(DUCK_LEVEL)
    # в начале музыка ещё громкая, в конце — приглушена, голос не трогается
    assert out[0, 0] == pytest.approx(0.1 + 0.5, abs=1e-3)
    assert out[-1, 0] == pytest.approx(0.1 + 0.5 * DUCK_LEVEL, abs=1e-3)
    # переход плавный: соседние сэмплы почти не отличаются
    assert np.max(np.abs(np.diff(out[:, 0]))) < 1e-3


def test_mix_restores_and_respects_volume():
    from lumo.desktop.audio_engine import mix

    voice = np.zeros(48000, np.float32)
    music = np.full((48000, 2), 0.5, np.float32)
    out, gain = mix(voice, music, volume=0.4, gain=0.15, target=1.0)
    assert gain == pytest.approx(1.0)
    assert out[-1, 1] == pytest.approx(0.2)


def test_mix_without_music_and_clipping():
    from lumo.desktop.audio_engine import mix

    out, gain = mix(np.full(10, 2.0, np.float32), None, 1.0, 1.0, 1.0)
    assert out.shape == (10, 2) and out.max() == 1.0 and gain == 1.0


def test_aec_cancels_music_and_voice_mix():
    pytest.importorskip("livekit")
    from lumo.desktop.audio_engine import _LivekitAPM, mix

    rate = 48000
    t = np.arange(rate * 4) / rate
    rng = np.random.default_rng(2)
    voice = (rng.normal(0, 0.1, len(t)) * (t < 2)).astype(np.float32)
    chord = sum(np.sin(2 * np.pi * f * t) for f in (220, 277, 330, 440))
    music = np.stack([chord, chord], axis=1).astype(np.float32) * 0.1
    played, _ = mix(voice, music, volume=1.0, gain=1.0, target=1.0)
    far = played.mean(axis=1).astype(np.float32)
    delay = int(0.05 * rate)
    mic = np.zeros_like(far)
    mic[delay:] = 0.5 * far[:-delay]

    apm = _LivekitAPM()
    out = np.zeros_like(mic)
    for i in range(0, len(mic) - BLOCK + 1, BLOCK):
        apm.reverse(far[i : i + BLOCK])
        out[i : i + BLOCK] = apm.capture(mic[i : i + BLOCK], 50)

    def db(x):
        return 20 * math.log10(max(float(np.sqrt(np.mean(x**2))), 1e-9))

    tail = slice(int(rate * 2.5), None)  # только музыка, после схождения
    assert db(mic[tail]) - db(out[tail]) > 15


@pytest.mark.parametrize(
    ("text", "logprob", "no_speech", "expected"),
    [
        ("Поставь таймер на десять минут", -0.3, 0.05, True),  # нормальная команда
        ("Стоп", -0.3, 0.05, True),  # короткое «стоп» — всегда всерьёз
        ("Ключик", -0.4, 0.1, False),  # одно слово — скорее обрывок эха
        ("Вайдер в плечи кинул", -1.4, 0.2, False),  # Whisper не уверен — галлюцинация
        ("Включи что-нибудь", -0.3, 0.8, False),  # Whisper считает, что речи не было
        ("сейчас двенадцать часов пять минут", -0.2, 0.05, False),  # собственные слова
        ("", -0.1, 0.0, False),
    ],
)
def test_real_interruption(text, logprob, no_speech, expected):
    from lumo.desktop.echo import EchoGuard
    from lumo.desktop.stt import Transcript
    from lumo.desktop.voice_mode import is_real_interruption

    echo = EchoGuard()
    echo.spoken("Сейчас двенадцать часов пять минут, на улице солнечно.")
    assert is_real_interruption(text, Transcript(text, logprob, no_speech), echo) is expected


async def test_now_playing_shown_in_status_and_announced_once():
    from lumo.desktop.player import MusicTrack

    assistant = VoiceAssistant(Settings(_env_file=None))
    printed = []
    assistant.status.print = printed.append

    class Player:
        active, playing, title = True, True, "Тест"
        current = MusicTrack("1", "Кукушка", "Кино")

    assistant._music_changed(Player())
    assert assistant.status.music == "♪ Кино — Кукушка"
    Player.playing = False
    assistant._music_changed(Player())
    assert assistant.status.music == "⏸ Кино — Кукушка"
    assert printed == ["♪ Играет: Кино — Кукушка"]  # о той же песне второй раз не объявляем
    Player.active = False
    assistant._music_changed(Player())
    assert assistant.status.music == ""


async def test_silent_reply_plays_chime_instead_of_speech():
    from lumo.protocol.messages import Reply

    assistant = VoiceAssistant(Settings(_env_file=None))
    played, said, printed = [], [], []
    assistant.audio.play = lambda audio, rate: played.append(len(audio))
    assistant._say = said.append
    assistant.status.print = printed.append

    class Conn:
        assistant_name = "Вальтер"

        async def ask(self, text):
            return Reply(id="1", text="Готово.", silent=True)

    assistant.conn = Conn()
    assert await assistant._process_text("Громкость на 30") is False
    assert played and not said
    assert printed[-1] == "✓ Готово"
