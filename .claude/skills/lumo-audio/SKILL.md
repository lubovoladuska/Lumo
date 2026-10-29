---
name: lumo-audio
description: Устройство звука в ПК-клиенте Lumo — аудиодвижок с WebRTC AEC, микширование голоса и музыки, приглушение, перебивание с проверкой, wake word, VAD, Whisper, Silero TTS, потоковый плеер Яндекс Музыки и медиапанель Windows. Используй перед правками в desktop/ (audio_engine, voice_mode, player, streaming, recorder, stt, tts, wakeword).
---

# Звук ПК-клиента

## Поток данных
- `EchoCancellingAudio` (`audio_engine.py`): `sd.OutputStream` 48 кГц стерео + `sd.InputStream`
  48 кГц моно, блоки 10 мс (`BLOCK=480`) — размер кадра WebRTC APM.
- Вывод: `mix(voice, music, volume, gain, target)` — голос моно×`voice_gain` в оба канала +
  музыка×`music_volume`×плавное приглушение (`DUCK_LEVEL=0.15`, ~150 мс). Моно-сумма вывода →
  `apm.reverse()` — AEC знает всё, что звучит из колонок (голос И музыку).
- Ввод: `apm.capture()` (AEC + шумодав + AGC) → децимация 48→16 кГц → кадры 512 сэмплов
  int16 в asyncio-очередь (`frame()`). Их едят Vosk, Silero VAD, Whisper.
- Колбэки — в аудиопотоке: только numpy-срезы, никаких await/сети/логов на каждый блок.

## Голосовой цикл (`voice_mode.py`)
- Ожидание: `wake.process(frame)` — Vosk с ПОЛНЫМ словарём, срабатывание по слову в partial
  (грамматика из одного слова давала ложные срабатывания на любую речь).
- Команда: `recorder.record()` (Silero VAD, пауза 1 с, макс 15 с, тишина 5 с) → `stt.transcribe`.
- Перебивание голосом (только при активном AEC, `LUMO_BARGE_IN=voice`): первые 0.8 с фразы не
  слушаем; VAD ≥0.7 на 10 кадрах → голос ассистента ×0.25, запись, `transcribe_scored`;
  `is_real_interruption` (не эхо по `EchoGuard`, logprob ≥ −0.9, no_speech ≤ 0.5, ≥2 слов
  или «стоп») → стоп и команда, иначе громкость обратно и договариваем.
- Тихий ответ (`Reply.silent`) → `confirm_tone()`, без TTS. Музыка приглушается, пока говорим/слушаем.
- Без AEC — запасной режим: пауза 0.6 с после речи + текстовый фильтр эха.

## Реальность пользователя
FxSound — виртуальное устройство с обработкой после AEC-образца → остаточное эхо (отсюда
проверка перебивания). Микрофон обычно `--mic 2` (Realtek массив). Тачпад/громкость — клавишами.

## Музыка
- `player.py`: очередь, `read()` из аудиопотока, `TrackAudio` растёт во время загрузки;
  старт при ≥2 с звука; следующий трек грузится после полной загрузки текущего; слушатели
  `listeners` → строка «♪» и `media_session.py` (SMTC: кнопки/медиа-клавиши → `player.control`).
- `actions/music.py`: `yandex-music` ClientAsync (нужен `aiohttp`), прямая ссылка mp3 ~192 кбит,
  aiohttp-чанки → `ByteStream` → miniaudio `stream_any` в потоке. Моя волна — станция
  `user:onyourwave` + фидбэк; одиночный трек → продолжение радио `track:<id>`.
- `MusicActions._start` ждёт первый трек ≤8 с (ядро ждёт устройство 15 с), иначе «загружаю».

## Модели
Whisper `small` int8 на CUDA (fallback CPU), Silero TTS v4 ru (CPU, лицензия NC), Vosk
small-ru-0.22, Silero VAD — в `models/` (`uv run lumo-download-models`). Текст для TTS —
`speech_text.normalize` (числа и время словами).
