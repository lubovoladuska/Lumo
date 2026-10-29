"""Голосовой цикл: ожидание имени → сигнал → запись → ядро → озвучивание.

Звук идёт через движок с подавлением эха, поэтому ассистент не слышит себя, и его можно
перебить голосом прямо во время ответа — не обязательно по имени.
"""

import asyncio
import logging
import re
import time
from collections import deque

from lumo.config import Settings
from lumo.desktop.actions import ActionExecutor
from lumo.desktop.actions.music import MusicActions, YandexMusic
from lumo.desktop.actions.pc import PcActions
from lumo.desktop.audio import confirm_tone, describe_input_device, tone
from lumo.desktop.audio_engine import EchoCancellingAudio
from lumo.desktop.connection import CoreConnection, CoreUnavailable
from lumo.desktop.cuda import register_nvidia_dlls
from lumo.desktop.download_models import silero_tts_path, vosk_path, whisper_root
from lumo.desktop.echo import EchoGuard
from lumo.desktop.media_session import WindowsMediaSession
from lumo.desktop.status import StatusLine
from lumo.protocol.messages import ConfirmRequest, Event, Reply

log = logging.getLogger(__name__)

COMMAND_NO_SPEECH = 5.0
FOLLOW_UP_WINDOW = 8.0
# Перебивание голосом: сколько подряд кадров (по 32 мс) уверенной речи нужно.
BARGE_IN_FRAMES = 10
BARGE_IN_PROBABILITY = 0.7
BARGE_IN_BUFFER = 40
# Начало каждой фразы ассистента не слушаем: AEC подстраивается, эхо сильнее всего.
BARGE_IN_GRACE = 0.8
# Пока проверяем, правда ли перебили, ассистент говорит тише, но не замолкает.
BARGE_VOICE_GAIN = 0.25
# Порог уверенности Whisper: ниже — это скорее эхо или шум, чем речь человека.
MIN_LOGPROB = -0.9
MAX_NO_SPEECH = 0.5
# Без подавления эха: сколько ждать после своей речи, пока затихнет эхо.
ECHO_TAIL = 0.6
STOP_WORDS = {"стоп", "хватит", "замолчи", "тихо", "помолчи", "отмена", "всё", "все"}


class VoiceAssistant:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.audio = EchoCancellingAudio(
            settings.mic, settings.speaker, echo_cancellation=settings.echo_cancellation
        )
        self.status = StatusLine()
        self.executor = self._build_executor()
        self.conn = CoreConnection(
            settings.core_url,
            settings.token.get_secret_value(),
            settings.device_id,
            self.on_event,
            self.on_status,
            executor=self.executor,
        )
        self.echo = EchoGuard()
        self._speech: asyncio.Task | None = None
        self._was_speaking = False
        self._barge_frames: deque[bytes] = deque(maxlen=BARGE_IN_BUFFER)
        self._barge_count = 0
        self._speech_started = 0.0
        self.wake_word = settings.wake_word.lower()
        self.idle_state = f"Жду «{settings.wake_word.capitalize()}»"

    def _build_executor(self) -> ActionExecutor:
        """Действия, которые ядро может выполнить на этом ПК: управление и музыка."""
        from lumo.desktop.player import MusicPlayer

        source = YandexMusic(self.settings.yandex_music_token.get_secret_value())
        self.player = MusicPlayer(source.load)
        self.audio.music = self.player
        self.media_session = WindowsMediaSession(self._media_button)
        self._announced_track: str | None = None
        self.player.listeners.append(self._music_changed)
        executor = ActionExecutor()
        PcActions(self.settings.search_engine, self.player).register(executor)
        MusicActions(source, self.player, self.audio).register(executor)
        return executor

    async def load(self) -> None:
        from lumo.desktop.recorder import CommandRecorder
        from lumo.desktop.stt import WhisperSTT
        from lumo.desktop.tts import SileroTTS, Speaker
        from lumo.desktop.wakeword import VoskWakeWord

        s = self.settings
        missing = [p for p in (vosk_path(s), silero_tts_path(s)) if not p.exists()]
        if missing:
            raise SystemExit(
                "Не найдены модели: "
                + ", ".join(map(str, missing))
                + "\nСкачайте их командой: uv run lumo-download-models"
            )
        register_nvidia_dlls()
        print("Загружаю модели…", flush=True)
        self.wake = await asyncio.to_thread(VoskWakeWord, vosk_path(s), s.wake_word)
        self.recorder = await asyncio.to_thread(CommandRecorder)
        self.stt = await asyncio.to_thread(WhisperSTT, s.stt_model, s.stt_device, whisper_root(s))
        print(f"Распознавание речи: {self.stt.name} на {self.stt.device}")
        tts = await asyncio.to_thread(
            SileroTTS, silero_tts_path(s), s.tts_speaker, s.tts_sample_rate
        )
        print(f"Голос: {s.tts_speaker}")
        self.speaker = Speaker(tts, self.audio)

    def _music_changed(self, player) -> None:
        """Плеер сменил трек или паузу: строка состояния и медиапанель Windows."""
        self.media_session.update(player)
        track = player.current if player.active else None
        if track is None:
            self.status.music = ""
            self._announced_track = None
            return
        self.status.music = f"{'♪' if player.playing else '⏸'} {track.label}"
        if track.id != self._announced_track:
            self._announced_track = track.id
            self.status.print(f"♪ Играет: {track.label}")

    def _media_button(self, action: str) -> None:
        """Кнопки медиапанели Windows, медиа-клавиши и тачпад."""
        asyncio.ensure_future(self.player.control(action))

    @property
    def aec(self) -> bool:
        return self.audio.aec_active

    @property
    def barge_in_by_voice(self) -> bool:
        # Без подавления эха голосовое перебивание срабатывало бы на собственную речь.
        return self.aec and self.settings.barge_in == "voice"

    def speaking(self) -> bool:
        return self.speaker.speaking or (self._speech is not None and not self._speech.done())

    # --- события ядра ---

    async def on_event(self, event: Event) -> None:
        text = event.text
        if event.missed and event.scheduled_for:
            local = event.scheduled_for.astimezone()
            text = f"Пропущенное в {local:%H:%M}. {text}"
        self.status.print(f"⏰ {text}")
        self.audio.play(tone(660), 48000)
        self.echo.spoken(text)
        await self.speaker.say(text)

    async def on_status(self, connected: bool) -> None:
        if hasattr(self, "speaker"):
            self._idle()  # сначала новое состояние, чтобы строка не мигала старым
        self.status.print("✓ Связь с ядром есть" if connected else "✗ Нет связи с ядром")

    # --- основной цикл ---

    async def _frame(self) -> bytes:
        frame = await self.audio.frame()
        self.status.push_level(frame)
        return frame

    def _idle(self) -> None:
        # Музыка тише, пока ассистент говорит; во время записи команды — см. _handle_command.
        self.audio.duck(self.speaking())
        if not self.conn.connected:
            self.status.set("Нет связи с ядром (запущен ли lumo-core?)")
        elif self.speaking():
            hint = "просто начните говорить" if self.barge_in_by_voice else "скажите имя"
            self.status.set(f"Говорю… ({hint}, чтобы перебить)")
        else:
            self.status.set(self.idle_state)

    async def run(self) -> None:
        await self.load()
        runner = asyncio.create_task(self.conn.run_forever())
        self.audio.start()
        self.media_session.start()
        print(f"Микрофон: {describe_input_device(self.settings.mic)}")
        if self.aec:
            print("Подавление эха: включено — можно перебивать голосом")
        else:
            print("Подавление эха: выключено — перебивать только по имени")
        print(
            f"Готов. Скажите «{self.settings.wake_word.capitalize()}» и команду. Ctrl+C — выход.\n"
        )
        self.status.start()
        follow_up = False
        try:
            while True:
                if runner.done():
                    runner.result()
                if follow_up and not self.speaking():
                    await self._after_speech()
                    follow_up = await self._handle_command(FOLLOW_UP_WINDOW)
                    continue
                if self._was_speaking and not self.speaking():
                    await self._after_speech()
                if not self._was_speaking and self.speaking():
                    self._speech_started = time.monotonic()
                self._was_speaking = self.speaking()
                self._idle()

                frame = await self._frame()
                if self._was_speaking and self.barge_in_by_voice and self._barge_in(frame):
                    follow_up = await self._handle_barge_in()
                    continue

                detected = self.wake.process(frame)
                self.status.heard = self.wake.last_text
                if not detected:
                    continue
                if self.speaking():
                    if not self.aec and self.wake_word in self.speaker.current_text.lower():
                        continue  # без AEC это ассистент сам произнёс своё имя
                    self.speaker.stop()
                self.audio.play(tone(), 48000)
                await asyncio.sleep(0.12)
                follow_up = await self._handle_command(COMMAND_NO_SPEECH)
        finally:
            self.status.stop()
            runner.cancel()
            self.audio.stop()

    def _barge_in(self, frame: bytes) -> bool:
        """Во время речи ассистента ловит голос пользователя (сигнал уже очищен от эха)."""
        self._barge_frames.append(frame)
        if time.monotonic() - self._speech_started < BARGE_IN_GRACE:
            return False
        if self.recorder.speech_probability(frame) >= BARGE_IN_PROBABILITY:
            self._barge_count += 1
        else:
            self._barge_count = max(0, self._barge_count - 1)
        return self._barge_count >= BARGE_IN_FRAMES

    async def _after_speech(self) -> None:
        """Ассистент договорил: сбрасываем всё, что успели услышать за время его речи."""
        self._was_speaking = False
        self._barge_count = 0
        self._barge_frames.clear()
        self.recorder.reset()
        self.wake.reset()
        self.wake.last_text = ""
        self.status.heard = ""
        if not self.aec:
            # Без подавления эха даём ему затихнуть и выбрасываем хвост.
            self.status.set("…")
            await asyncio.sleep(ECHO_TAIL)
            self.audio.drain()

    async def _handle_barge_in(self) -> bool:
        """Похоже, пользователь заговорил поверх ответа. Не замолкаем сразу: говорим тише,
        записываем и проверяем. Эхо или шум — возвращаем громкость и договариваем."""
        initial = list(self._barge_frames)
        self.audio.voice_target = BARGE_VOICE_GAIN
        self.status.set("Слушаю вас?…")
        try:
            self.recorder.reset()
            audio = await self.recorder.record(self._frame, 2.0, initial)
            transcript = None
            if audio is not None:
                transcript = await asyncio.to_thread(self.stt.transcribe_scored, audio)
            text = self._strip_wake_word(transcript.text) if transcript else ""
            if transcript is None or not is_real_interruption(text, transcript, self.echo):
                if text:
                    self.status.print(f"… показалось («{text}») — продолжаю")
                return False
        finally:
            self.audio.voice_target = 1.0
            self._barge_count = 0
            self._barge_frames.clear()
            self.recorder.reset()
            self.wake.reset()
        self.speaker.stop()
        return await self._process_text(text)

    async def _handle_command(
        self, no_speech_timeout: float, initial: list[bytes] | None = None
    ) -> bool:
        """Записывает и обрабатывает команду. Возвращает True, если ассистент ждёт ответа."""
        self._barge_count = 0
        self._barge_frames.clear()
        self.recorder.reset()
        self.status.heard = ""
        self.status.set("🔴 Слушаю команду…")
        self.audio.duck(True)
        audio = await self.recorder.record(self._frame, no_speech_timeout, initial)
        self.wake.reset()
        self.wake.last_text = ""
        if audio is None:
            self.status.print("… команды не услышал")
            return False
        self.status.set("Распознаю…")
        text = self._strip_wake_word(await asyncio.to_thread(self.stt.transcribe, audio))
        if not text:
            self.status.print("… не разобрал слова")
            return False
        if not self.aec and self.echo.is_echo(text):
            self.status.print(f"… («{text}» — это было моё эхо, пропускаю)")
            return False
        return await self._process_text(text)

    async def _process_text(self, text: str) -> bool:
        """Отправляет распознанную команду в ядро и озвучивает ответ."""
        if _is_stop(text):
            self.status.print(f"Вы: {text} — молчу")
            return False
        self.status.print(f"Вы: {text}")
        self.status.set("Думаю…")
        try:
            reply = await self.conn.ask(text)
        except (CoreUnavailable, TimeoutError):
            self._say("Нет связи с ядром. Проверьте, что оно запущено.")
            return False
        if isinstance(reply, Reply) and reply.silent:
            self.status.print(f"✓ {reply.text}")
            self.audio.play(confirm_tone(), 48000)
            return False
        self.status.print(f"{self.conn.assistant_name}: {reply.text}")
        self._say(reply.text)
        return isinstance(reply, ConfirmRequest) or reply.expects_response

    def _strip_wake_word(self, text: str) -> str:
        """«Вальтер, поставь таймер» → «Поставь таймер»."""
        pattern = rf"^\s*{re.escape(self.wake_word)}[\s,.!?:-]*"
        stripped = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
        return stripped[:1].upper() + stripped[1:] if stripped else ""

    def _say(self, text: str) -> None:
        # Речь идёт фоном, чтобы во время неё можно было перебить ассистента.
        self.echo.spoken(text)
        self._speech = asyncio.create_task(self.speaker.say(text))


def is_real_interruption(text: str, transcript, echo: EchoGuard) -> bool:
    """Перебили ли ассистента на самом деле, или это его эхо, шум, галлюцинация Whisper."""
    if not text:
        return False
    if _is_stop(text):
        return True
    if echo.is_echo(text):
        return False
    if transcript.no_speech_prob > MAX_NO_SPEECH or transcript.avg_logprob < MIN_LOGPROB:
        return False
    return len(re.findall(r"[а-яёa-z0-9]+", text.lower())) >= 2


def _is_stop(text: str) -> bool:
    words = re.findall(r"[а-яё]+", text.lower())
    return 0 < len(words) <= 2 and all(w in STOP_WORDS for w in words)


async def run_voice_mode(settings: Settings) -> None:
    await VoiceAssistant(settings).run()
