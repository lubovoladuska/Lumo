# Lumo — голосовой ассистент «Вальтер»

Личный ИИ-ассистент в духе Джарвиса для владельца (русскоязычный, Windows 10, GTX 1660 Ti 6 ГБ,
Android-телефон, звук через FxSound → «Наушники Realtek»). Портфолио-проект на GitHub
`lubovoladuska/Lumo`. Бюджет ≈ 0: платим только за DeepSeek API, остальное — open source локально.
Общаемся и пишем всё (код-комментарии, UI, спеки, коммиты — кроме заголовка) по-русски.

**Цель:** голосом управлять всем — ПК, музыкой, умным домом, мессенджерами, звонками; общая
память между ПК и телефоном; приятный голос (позже клонирование).
**Готово:** голосовой цикл на ПК, память, таймеры, управление ПК, Яндекс Музыка, режимы ответов.
**Дальше (по приоритету, обсуждено):** отложенные команды («через минуту подними громкость») →
Android-клиент + Tailscale → мессенджеры (Telegram легально, WhatsApp рискованно) → свой голос
(XTTS/F5) → своё wake word (openWakeWord) → умный дом (Home Assistant).

## Архитектура: ядро думает, клиенты слушают и говорят

```
desktop-клиент ──WebSocket JSON──► ядро (FastAPI) ──► DeepSeek (tool calling)
  мик → Vosk «вальтер» → VAD → Whisper(GPU)   движок диалога, навыки, SQLite-память,
  ◄── ответ → Silero TTS → AEC-аудиодвижок    планировщик, DeviceRouter
  ◄── action_request (pc.*, music.*) ─────────  навыки ядра шлют действия на устройство
```
Ядро работает **только с текстом** и не знает Windows. Всё, что трогает ОС/звук, — действия
клиента (`ActionExecutor`), их вызывает навык ядра через `ctx.device("pc.volume", {...})`.
Клиент объявляет возможности (`pc`, `music`) в `hello`; роутер шлёт действие подходящему.

## Карта кода (`src/lumo/`)

| Файл | За что отвечает |
|---|---|
| `config.py` | все настройки `LUMO_*` из `.env` (pydantic-settings) |
| `protocol/messages.py` | сообщения ядро↔клиент: hello, utterance, reply(silent), confirm_request, event, action_request/result |
| `core/server.py` | `/ws` + `/health`; реплики — отдельной задачей (иначе взаимоблокировка с action_result) |
| `core/engine.py` | персона (`_PERSONA`), история с вызовами навыков, цикл LLM↔навыки (≤5), режимы ответа без 2-го запроса, ловля «выдуманных» действий (`claims_action`) |
| `core/skill_registry.py` | `@skill(name, desc, Params, confirm=, describe=, reply=)`, `SkillContext` |
| `core/skills/*.py` | навыки: clock, memory, timers, pc, music, preferences — автообнаружение |
| `core/storage.py` | SQLite `data/lumo.db`, схема v3 (facts, messages+tool_calls, timers, pending_events, preferences) |
| `core/scheduler.py` | таймеры/напоминания, доставка пропущенных |
| `core/devices.py` | `DeviceRouter`: выбор клиента по возможности, таймаут 15 с |
| `core/confirmation.py` | «да/нет» для опасных навыков решает ядро, не LLM |
| `desktop/voice_mode.py` | голосовой цикл, перебивание с проверкой, тихие ответы, «сейчас играет» |
| `desktop/audio_engine.py` | вывод 48 кГц стерео + микрофон, WebRTC AEC (livekit), канал музыки с приглушением |
| `desktop/actions/` | исполнитель: `pc.py` (регистрация), `apps.py`, `system.py` (громкость клавишами!), `music.py` (Яндекс) |
| `desktop/player.py`, `streaming.py` | плеер с потоковой загрузкой; `media_session.py` — медиапанель Windows |
| `desktop/stt.py` `tts.py` `wakeword.py` `recorder.py` | Whisper, Silero, Vosk, Silero VAD |
| `desktop/text_mode.py` | отладка без микрофона (`--text`) |

`openspec/specs/` — действующие требования; `openspec/changes/` — текущие изменения.
Активные: `pc-control-and-music` (осталось ручное), `reply-styles`.

## Команды

```bash
uv sync --extra core --extra desktop --extra dev   # ВСЕГДА все три extra, иначе удалит ruff/pytest
uv run --no-sync pytest -q -p no:warnings          # ~230 тестов, голосовые — при скачанных моделях
uv run --no-sync ruff format src tests && uv run --no-sync ruff check
uv run lumo-core | uv run lumo-desktop [--text] [--mic N] [--list-mics]
```

## Правила работы

- Новая функциональность — через OpenSpec (`/opsx:propose` → `/opsx:apply` → `/opsx:archive`),
  артефакты на русском. Мелкие багфиксы — сразу кодом + тест.
- Каждое изменение — с тестами; фейковая LLM: `tests/conftest.py::ScriptedLLM`, `tool()`, `text()`.
- **Не трогать** `data/lumo.db` и `.env` пользователя (секреты, история). Проверки — на временной
  БД и отдельном порту, см. скилл `lumo-verify`.
- Пуш на GitHub и архив изменений — только с согласия пользователя.
- Добавить голосовую команду — скилл `lumo-new-command`. Трогаешь звук/AEC/плеер — `lumo-audio`.

## Грабли (уже наступали)

- Пользователь держит ядро запущенным → `uv sync`/`uv run` падают на занятом `lumo-core.exe`:
  используй `uv run --no-sync`, а пользователю — «останови ядро, `uv sync …`».
- Python 3.12 через uv (под системный 3.14 нет torch/ctranslate2). torch — CPU, Whisper — CUDA int8.
- Вывод в pipe на Windows — cp1251: ставь `PYTHONIOENCODING=utf-8`.
- Громкость FxSound меняется только клавишами громкости (`VK_VOLUME_*`), не через pycaw.
- LLM «выполняет» действия на словах, копируя историю → история хранит tool_calls, ядро ловит
  `claims_action`. Короткие фразы («громкость 10») — команды.
- Сеть до Яндекса медленная (~75 КБ/с) → музыка только потоком, следующий трек после текущего.
- `\b` в регэкспах через heredoc-python превращается в backspace — проверяй `pattern`.
