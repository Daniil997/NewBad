# NewBad — «Что важно для вашей компании»

Прототип сервиса новостной аналитики для руководителей крупного бизнеса (Альфа-Хак 2026, кейс Changellenge × Альфа-Банк). Сервис выбирает внешние события под конкретную компанию и показывает три отдельных слоя: факт из новости, позицию аналитиков банка и вывод модели.

**Статус:** этап 2 готов. Поиск находит новость о ставке 14% и паспорт аналитики банка. Дальше — этап 3 в [docs/ROADMAP.md](docs/ROADMAP.md).

## Быстрый старт

```
python -m venv .venv
. .venv/bin/activate        # Windows PowerShell: .venv/Scripts/Activate.ps1
pip install -r requirements-dev.txt
pytest
.\scripts\dev.ps1          # API :8000 и интерфейс :5173
```

Документация API: http://localhost:8000/docs. Через Docker: `docker compose up --build`. По умолчанию режим `mock`: ни ключ, ни видеокарта не нужны.

## Режимы LLM

Режим задаётся переменной `LLM_MODE` или командой администратора `POST /api/llm/mode`. Модели назначаются на два уровня: `light` (классификация, извлечение) и `heavy` (план, карточка, ответы на вопросы). Настройки: [config/llm.yaml](config/llm.yaml).

| Режим | light | heavy | Облако |
|---|---|---|---|
| `mock` | заглушка | заглушка | нет |
| `local_only` | локальная | локальная | запрещено |
| `local_dual` | локальная A | локальная B | запрещено |
| `hybrid` | локальная | облако, при сбое локальная | нужен `ALLOW_CLOUD=true` |
| `cloud_only` | облако | облако | нужен `ALLOW_CLOUD=true` |
| `replay` | только кэш | только кэш | нет |

Допускаются только модели из приложения 3 кейса, список в `app/llm/registry.py`.

## Переменные окружения

См. [.env.example](.env.example). Секреты (`CASE_LLM_API_KEY`, `ADMIN_TOKEN`) хранятся только в окружении и в репозиторий не попадают.

## Структура

```
app/        api, agent, llm, scoring, profile, ingest, retrieval, tracing, schemas
config/     llm, taxonomy, exposures, scoring, okved_map, prompt_fields
data/       raw (не в git), demo, news, bank, cache
docs/       архитектура, схема карточки, план работ
eval/       набор оценки и метрики
scripts/    служебные скрипты
ui/         интерфейс: TypeScript, React, Vite (этап 5)
models/     веса GGUF (не в git)
tests/
```

## Ветки и версии

Работа идёт в `develop`, в `main` попадают стабильные версии. Перед дедлайном ставим тег `v1.0-submission` и указываем commit hash здесь. Коммиты после дедлайна не учитываются.

## Лицензии и источники

См. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
