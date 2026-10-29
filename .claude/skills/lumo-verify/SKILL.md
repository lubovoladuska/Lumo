---
name: lumo-verify
description: Как проверять изменения в Lumo — тесты, линтер, сквозная проверка с настоящим DeepSeek на отдельном ядре и временной БД, смоук голосового клиента, подсчёт запросов к LLM. Используй перед тем, как сказать пользователю «готово».
---

# Проверка изменений Lumo

## Быстро (всегда)
```bash
uv run --no-sync ruff format src tests && uv run --no-sync ruff check
timeout 300 uv run --no-sync pytest -q -p no:warnings          # ~70 с с голосовыми
timeout 200 uv run --no-sync pytest -q -p no:warnings --ignore=tests/test_voice_pipeline.py  # ~10 с
```
`--no-sync` — потому что у пользователя обычно запущено ядро и `uv` не может заменить exe.

## С настоящим DeepSeek (поведение LLM, выбор навыков)
Никогда не используй `data/lumo.db` и не запускай на порту 8765 — там живое ядро пользователя.
```bash
export LUMO_DATA_DIR="$TEMP/lumo-smoke-X" LUMO_PORT=87NN LUMO_CORE_URL=ws://127.0.0.1:87NN/ws PYTHONIOENCODING=utf-8
rm -rf "$LUMO_DATA_DIR"
(timeout 90 uv run --no-sync lumo-core > "$TEMP/coreX.log" 2>&1 &)
for i in $(seq 1 20); do curl -s http://127.0.0.1:87NN/health >/dev/null && break; sleep 1; done
(sleep 3; echo "Громкость 30"; sleep 15; echo "выход") | timeout 60 uv run --no-sync lumo-desktop --text 2>&1 | tr '\r' '\n' | grep -E "Вальтер:|✓"
grep -c chat/completions "$TEMP/coreX.log"   # 1 на тихую/короткую команду, 2 — навык + полный ответ
```
- Ключ берётся из `.env` пользователя (переменные выше переопределяют только своё).
- 1 запрос и текстовый ответ о действии = LLM выдумала действие (навык не вызывался).
- «Отравленную» историю воспроизводи, засеяв временную БД через `Storage.add_message` до старта.
- Реальные действия на ПК делай обратимыми и небольшими (громкость ±2–4 % с возвратом,
  Win+M → Win+Shift+M) и предупреждай пользователя о побочных эффектах (скриншоты в
  `Изображения/Lumo`).

## Голосовой клиент (смоук без микрофона)
```bash
(timeout 50 uv run --no-sync lumo-core > "$TEMP/c.log" 2>&1 &)
timeout 35 uv run --no-sync lumo-desktop 2>&1 | tr '\r' '\n' | sed 's/\x1b\[K//g' | sed -E 's/🎤 [█░]+ +-?[0-9]+ dB │ //' | uniq | tail
```
Ожидаемо: «Распознавание речи: whisper-small на cuda», «Подавление эха: включено», «✓ Связь с ядром есть»,
«Жду «Вальтер»». Пропущенное событие можно подложить через `Storage.add_pending_event` — клиент его озвучит.

## Что смотреть у пользователя
`mcp__terminal__list_terminal_tabs` → `read_terminal` (ядро и клиент в разных вкладках).
В логе ядра на каждую реплику видно запросы к DeepSeek и подключения клиента с возможностями.
