"""Голосовой цикл: ожидание имени → сигнал → запись → ядро → озвучивание."""

import asyncio
import logging

from lumo.config import Settings
from lumo.desktop.audio import Microphone, beep, describe_input_device
from lumo.desktop.connection import CoreConnection, CoreUnavailable
from lumo.desktop.cuda import register_nvidia_dlls
from lumo.desktop.download_models import silero_tts_path, vosk_path, whisper_root
from lumo.desktop.echo import EchoGuard
from lumo.desktop.status import StatusLine
from lumo.protocol.messages import ConfirmRequest, Event

log = logging.getLogger(__name__)

COMMAND_NO_SPEECH = 5.0
FOLLOW_UP_WINDOW = 8.0
# Сколько ждать после конца речи, пока в комнате затихнет эхо.
ECHO_TAIL = 0.6


class VoiceAssistant:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mic = Microphone(settings.mic)
        self.status = StatusLine()
        self.conn = CoreConnection(
            settings.core_url,
            settings.token.get_secret_value(),
            settings.device_id,
            self.on_event,
            self.on_status,
        )
        self._speech: asyncio.Task | None = None
        self.echo = EchoGuard()
        self._was_speaking = False
        self.idle_state = f"Жду «{settings.wake_word.capitalize()}»"

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
        self.speaker = Speaker(tts)

    # --- события ядра ---

    async def on_event(self, event: Event) -> None:
        text = event.text
        if event.missed and event.scheduled_for:
            local = event.scheduled_for.astimezone()
            text = f"Пропущенное в {local:%H:%M}. {text}"
        self.status.print(f"⏰ {text}")
        beep(660)
        await asyncio.sleep(0.2)
        self.echo.spoken(text)
        await self.speaker.say(text)

    async def on_status(self, connected: bool) -> None:
        self.status.print("✓ Связь с ядром есть" if connected else "✗ Нет связи с ядром")

    # --- основной цикл ---

    async def _frame(self) -> bytes:
        frame = await self.mic.frame()
        self.status.push_level(frame)
        return frame

    def _idle(self) -> None:
        if not self.conn.connected:
            self.status.set("Нет связи с ядром (запущен ли lumo-core?)")
        elif self.speaker.speaking:
            self.status.set("Говорю… (скажите имя, чтобы перебить)")
        else:
            self.status.set(self.idle_state)

    async def run(self) -> None:
        await self.load()
        runner = asyncio.create_task(self.conn.run_forever())
        self.mic.start()
        print(f"Микрофон: {describe_input_device(self.settings.mic)}")
        print(
            f"Готов. Скажите «{self.settings.wake_word.capitalize()}» и команду. Ctrl+C — выход.\n"
        )
        self.status.start()
        follow_up = False
        try:
            while True:
                if runner.done():
                    runner.result()
                if follow_up:
                    await self._wait_speech()
                    await self._settle_echo()
                    follow_up = await self._handle_command(FOLLOW_UP_WINDOW)
                    continue
                if self._was_speaking and not self.speaker.speaking:
                    await self._settle_echo()
                self._was_speaking = self.speaker.speaking
                self._idle()
                frame = await self._frame()
                detected = self.wake.process(frame)
                self.status.heard = self.wake.last_text
                if not detected:
                    continue
                if self.speaker.speaking:
                    if self.settings.wake_word in self.speaker.current_text.lower():
                        continue  # ассистент сам произнёс своё имя
                    self.speaker.stop()
                beep()
                await asyncio.sleep(0.15)
                self.mic.drain()
                follow_up = await self._handle_command(COMMAND_NO_SPEECH)
        finally:
            self.status.stop()
            runner.cancel()
            self.mic.stop()

    async def _handle_command(self, no_speech_timeout: float) -> bool:
        """Записывает и обрабатывает команду. Возвращает True, если ассистент ждёт ответа."""
        self.status.heard = ""
        self.status.set("🔴 Слушаю команду…")
        audio = await self.recorder.record(self._frame, no_speech_timeout)
        self.wake.last_text = ""
        if audio is None:
            self.status.print("… команды не услышал")
            return False
        self.status.set("Распознаю…")
        text = await asyncio.to_thread(self.stt.transcribe, audio)
        if not text:
            self.status.print("… не разобрал слова")
            return False
        if self.echo.is_echo(text):
            self.status.print(f"… («{text}» — это было моё эхо, пропускаю)")
            return False
        self.status.print(f"Вы: {text}")
        self.status.set("Думаю…")
        try:
            reply = await self.conn.ask(text)
        except (CoreUnavailable, TimeoutError):
            self._say("Нет связи с ядром. Проверьте, что оно запущено.")
            return False
        self.status.print(f"{self.conn.assistant_name}: {reply.text}")
        self._say(reply.text)
        expects = isinstance(reply, ConfirmRequest) or reply.expects_response
        if expects:
            self.status.set("Жду ответа…")
        return expects

    def _say(self, text: str) -> None:
        # Речь идёт фоном, чтобы во время неё можно было перебить ассистента по имени.
        self.echo.spoken(text)
        self._speech = asyncio.create_task(self.speaker.say(text))

    async def _settle_echo(self) -> None:
        """После своей речи ждёт, пока затихнет эхо, и забывает всё, что успел услышать."""
        self.status.set("…")
        await asyncio.sleep(ECHO_TAIL)
        self.mic.drain()
        self.wake.reset()
        self.wake.last_text = ""
        self.status.heard = ""
        self._was_speaking = False

    async def _wait_speech(self) -> None:
        if self._speech is not None:
            await asyncio.gather(self._speech, return_exceptions=True)


async def run_voice_mode(settings: Settings) -> None:
    await VoiceAssistant(settings).run()
