"""Проверка голосового конвейера на синтетической речи: TTS → запись по VAD → STT.

Нужны модели в models/ (uv run lumo-download-models), иначе тесты пропускаются.
"""

import asyncio

import numpy as np
import pytest

from lumo.config import Settings

pytest.importorskip("faster_whisper")
pytest.importorskip("silero_vad")

from lumo.desktop.audio import FRAME_SAMPLES, SAMPLE_RATE  # noqa: E402
from lumo.desktop.download_models import silero_tts_path, whisper_root  # noqa: E402

settings = Settings(_env_file=None)
pytestmark = pytest.mark.skipif(not silero_tts_path(settings).exists(), reason="модели не скачаны")


@pytest.fixture(scope="module")
def phrase_audio() -> np.ndarray:
    import torch

    from lumo.desktop.tts import SileroTTS

    tts = SileroTTS(silero_tts_path(settings), "aidar", 48000)
    audio = torch.from_numpy(tts.synthesize("Поставь таймер на две минуты."))
    # 48 кГц → 16 кГц
    return audio[::3].numpy()


def _frames(audio: np.ndarray, lead_silence: float, tail_silence: float) -> list[bytes]:
    silence = lambda s: np.zeros(int(SAMPLE_RATE * s), dtype=np.float32)  # noqa: E731
    full = np.concatenate([silence(lead_silence), audio, silence(tail_silence)])
    pcm = (np.clip(full, -1, 1) * 32767).astype(np.int16)
    return [
        pcm[i : i + FRAME_SAMPLES].tobytes()
        for i in range(0, len(pcm) - FRAME_SAMPLES, FRAME_SAMPLES)
    ]


def _feeder(frames: list[bytes]):
    it = iter(frames)

    async def next_frame() -> bytes:
        await asyncio.sleep(0)
        return next(it)

    return next_frame


async def test_record_and_transcribe(phrase_audio):
    from lumo.desktop.recorder import CommandRecorder
    from lumo.desktop.stt import WhisperSTT

    recorder = CommandRecorder()
    audio = await recorder.record(_feeder(_frames(phrase_audio, 0.5, 3.0)))
    assert audio is not None
    # запись остановилась на паузе, а не на конце буфера
    assert len(audio) / SAMPLE_RATE < len(phrase_audio) / SAMPLE_RATE + 2.0

    stt = WhisperSTT(settings.stt_model, "auto", whisper_root(settings))
    text = stt.transcribe(audio).lower()
    assert "таймер" in text
    assert "минут" in text


async def test_silence_returns_none():
    from lumo.desktop.recorder import CommandRecorder

    frames = _frames(np.zeros(0, dtype=np.float32), 6.0, 0.0)
    assert await CommandRecorder().record(_feeder(frames), no_speech_timeout=5.0) is None


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("Вальтер, который час?", True),
        ("Вальтер.", True),
        ("Сегодня отличная погода для прогулки.", False),
        ("Валентина пришла домой.", False),
    ],
)
def test_wake_word(phrase, expected):
    from lumo.desktop.download_models import vosk_path
    from lumo.desktop.tts import SileroTTS
    from lumo.desktop.wakeword import VoskWakeWord

    tts = SileroTTS(silero_tts_path(settings), "xenia", 48000)
    wake = VoskWakeWord(vosk_path(settings), "вальтер")
    frames = _frames(tts.synthesize(phrase)[::3], 0.5, 1.0)
    assert any(wake.process(f) for f in frames) is expected
