# Схема карточки

Реализация: `app/schemas/card.py`. Интерфейс читает только эту структуру.

| Поле | Содержимое |
|---|---|
| `schema_version`, `card_id`, `client_id`, `event_id`, `generated_at` | служебные поля |
| `llm_trace` | модель, провайдер, режим, токены, число повторов |
| `what_happened` | `claims[]` и `assumptions[]`: подтверждённое отдельно от предположений |
| `why_for_company` | `claims[]`, у каждого есть `feature_refs` |
| `consequences` | `risks[]`, `opportunities[]` (claims) и `depends_on[]` |
| `action` | `next_step`, `questions[]`, `product_hint` только с `is_ad=true` |
| `bank_view` | `status` (`found` или `none`), `quote` до 25 слов, `summary`, `doc_ref`, `page` |
| `sources` | новости, материалы банка, признаки компании с происхождением |
| `features_triggered`, `missing_data`, `assumptions` | прозрачность персонализации |

## Claim

`id`, `text`, `layer`, `source_refs[]`, `feature_refs[]`, `confidence` (0..1).

## Проверки в схеме

- `confirmed_fact` требует хотя бы один `source_refs`.
- `bank_position` допустим только при `bank_view.status = found`.
- Все `source_refs` и `feature_refs` ссылаются на запись из `sources` (для признаков вид `company_feature`).
- В `why_for_company` у каждого claim есть `feature_refs`.
- Цитата банка не длиннее 25 слов. Проверка, что она подстрока документа, делается на шаге VERIFY агента.
