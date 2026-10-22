# Данные

Демо-набор уже лежит в репозитории и запускается при `PROFILE_SOURCE=demo`. Закрытая выгрузка кейса и PDF банка лежат **вне** каталога `NewBad` и в Git не коммитятся.

Проверка демо без сети и без PDF:

```powershell
python scripts\prepare.py
```

Ожидаемый текст: «демо-набор на месте, ключ и видеокарта не нужны».

## Три CSV

Положите файлы в один каталог, по умолчанию рядом с репозиторием:

```text
../DATA/company_profile/df_Uid_data_hack.csv
../DATA/company_profile/df_Uid_payment_hack.csv
../DATA/company_profile/df_Fid_Uid_link_hack.csv
```

Имена фиксированы. Кодировка UTF-8 или CP1251, разделитель определяется по заголовку: точка с запятой или запятая. Пустая ячейка остаётся пустой.

В `df_Uid_data_hack.csv` нужны столбцы: `SYNTHETIC_U`, `ORG_TYPE`, `OKFS_NAME`, `COMPANY_AGE_YEARS_GRADATION`, `ECO_MARK_LABEL`, `MAINOKVED_CODE`, `MAINOKVED_NAME`, `NEGATIVEMARK`, `REVENUE_COMPANY`, `GROUP_ID`, `AMOUNT_WORKERS_FNS_GRADATION`, `AMOUNT_WORKERS_SPARK_GRADATION`, `CL_TYPE`, `CITY`, `REGION`, `CL_PRODUCT_RKO_MARK`, `CL_PRODUCT_GAR_MARK`, `CL_PRODUCT_LOA_MARK`, `CL_PRODUCT_LEA_MARK`, `CL_PRODUCT_DEP_MARK`, `CL_PRODUCT_ZPP_MARK`, `FTS_FLAG`, `FTS_COUNTRY_NAME`, `FACTORING_MARK`. `REVENUE_COMPANY` — выручка этой компании в рублях.

В `df_Uid_payment_hack.csv`: `PAYER`, `BENEFICIARY`, `PAY_AMT`, `PAY_CNT`.

В `df_Fid_Uid_link_hack.csv`: `SYNTHETIC_F`, `SYNTHETIC_U`.

Чтобы приложение читало эту папку, в `.env` задайте:

```text
PROFILE_SOURCE=case
CASE_DATA_DIR=../DATA/company_profile
```

`PROFILE_SOURCE=demo` оставляет три синтетические компании из `data/demo/`, даже если `CASE_DATA_DIR` заполнен. При `PROFILE_SOURCE=case` список в интерфейсе берётся из `config/case_clients.yaml`: `U_26030`, `U_99802`, `U_408263`. Показатели читаются из CSV. Если переменная `CASE_DATA_DIR` не задана, код ищет `df_Uid_data_hack.csv` в `../DATA/company_profile`, затем в `../DATA`, затем в `../Doc/Данные данные`.

Docker Compose монтирует `../DATA/company_profile` только для чтения и внутри контейнера задаёт `CASE_DATA_DIR=/srv/input/company_profile`. Образ по умолчанию всё равно стартует с `PROFILE_SOURCE=demo`.

## PDF и импорт

Девять PDF аналитики банка положите в `../DATA/bank_analytics`. Имена файлов перечислены в `config/bank_appendix.yaml`. Переменная:

```text
BANK_PDF_DIR=../DATA/bank_analytics
```

Если `BANK_PDF_DIR` задан, используется только этот путь. Если переменная пустая, код ищет `../DATA/bank_analytics`, затем `../Doc/Данные данные`.

Команды из каталога `NewBad`:

```powershell
python scripts\prepare.py news
python scripts\prepare.py bank
```

`news` скачивает RSS и пишет результат в `data/news/imported/`. `bank` читает PDF и пишет текст в `data/bank/extracted/<id>/pages.json`. Рядом для распознанной страницы лежит `ocr/page-001.txt`. Исходный PDF не изменяется. Для скана нужен Tesseract в `PATH` или `C:\Program Files\Tesseract-OCR\tesseract.exe` и файл `data/tessdata/rus.traineddata`. Все страницы читаются; одна страница сверяется повторным распознаванием. Если папки PDF нет, команда печатает «папка PDF не найдена, импорт банка пропущен» и завершается с кодом 1.

Для демо на хосте страницы уже распознаны, повторный `prepare.py bank` не нужен. В образ Docker каталог `data/bank/extracted` не копируется: в контейнере цитата ставки берётся из паспорта `data/bank/key_rate.json`.

## Подготовленные файлы и индекс

| Файл | Смысл |
|---|---|
| `data/demo/*.csv` | Синтетические компании. Лежат в Git, их читает режим `demo` |
| `data/news/*.json` | Демо-новости. Обязательны `id`, `url`, `title`, `published_at` (`ГГГГ-ММ-ДД`) и `excerpt` |
| `data/news/imported/*.json` | Новости после RSS, тот же формат. `import.json` — отчёт партии, в ленту не входит |
| `data/bank/*.json` | Паспорт: `doc_id`, `title`, `published_at`, `topic`, `page`, `source_type`, `theses`, `text`, `quote`. Цитата — подстрока `text`, не длиннее 25 слов |
| `data/bank/extracted/<id>/pages.json` | Результат чтения PDF: `processed`, `errors`, список страниц с `page`, `text`, `kind`, `error` |
| `data/journal.sqlite3` | Журнал шагов, чат, кэш ленты `feed_cache`, кэш ответов `llm_replay` |

Отдельного файла поискового индекса нет. Поиск новостей и банка собирается в памяти при запросе из этих JSON. Запись со статусом `duplicate`, без адреса, даты или текста в корпус не входит. Страница с полем `error`, слишком коротким текстом или чужим `kind` в индекс не входит.

Кэш ленты хранит порядок для пары «компания + версия корпуса». Версия меняется, когда меняется набор id и дат новостей, набор id и дат банковских документов или `config/scoring.yaml`. Правка только текста при тех же id и дате оставляет сохранённый порядок ленты. Чтобы собрать ленту заново, остановите API и удалите `data/journal.sqlite3`. Вместе с кэшем ленты удалятся шаги агента, реплики чата и кэш `replay`. Поиск при следующем запросе снова читает JSON.

## Что не коммитить

В Git не добавляют закрытые исходники и локальные производные:

- три CSV выгрузки и любые копии с реальными или кейсовыми клиентскими полями;
- PDF из `DATA/bank_analytics` и резервной папки;
- `.env` и ключ `CASE_LLM_API_KEY`;
- `data/bank/extracted/`, `data/news/imported/`, `data/raw/`, `data/cache/`, `data/tessdata/`;
- `data/*.sqlite3`, включая журнал и кэш ленты;
- веса в `models/`.

Эти пути перечислены в `.gitignore`. В репозитории остаются демо-CSV, демо-новости и паспорта в `data/bank/*.json`.
