from pathlib import Path

import yaml
from pydantic import BaseModel

from app.db import ROOT
from app.profile.sources import companies_csv, payments_csv, read_csv

FLAG_ON = {"1", "1.0", "true", "y", "yes", "да"}
FLAG_OFF = {"0", "0.0", "false", "n", "no", "нет"}
FLAG_FIELDS = {
    "NEGATIVEMARK",
    "CL_PRODUCT_RKO_MARK",
    "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK",
    "CL_PRODUCT_LEA_MARK",
    "CL_PRODUCT_DEP_MARK",
    "CL_PRODUCT_ZPP_MARK",
    "FTS_FLAG",
    "FACTORING_MARK",
}

FIELD_LABELS = {
    "ORG_TYPE": "Тип организации",
    "OKFS_NAME": "Форма собственности",
    "COMPANY_AGE_YEARS_GRADATION": "Возраст компании",
    "ECO_MARK_LABEL": "Стадия развития",
    "MAINOKVED_CODE": "ОКВЭД",
    "MAINOKVED_NAME": "Вид деятельности",
    "NEGATIVEMARK": "Признак ключевого собственника",
    "REVENUE_COMPANY": "Годовая выручка",
    "GROUP_ID": "Группа компаний",
    "AMOUNT_WORKERS_FNS_GRADATION": "Численность по ФНС",
    "AMOUNT_WORKERS_SPARK_GRADATION": "Численность по СПАРК",
    "CL_TYPE": "Тип клиента",
    "CITY": "Город",
    "REGION": "Регион",
    "CL_PRODUCT_RKO_MARK": "Расчётно-кассовое обслуживание",
    "CL_PRODUCT_GAR_MARK": "Банковские гарантии",
    "CL_PRODUCT_LOA_MARK": "Кредитные продукты",
    "CL_PRODUCT_LEA_MARK": "Лизинг",
    "CL_PRODUCT_DEP_MARK": "Депозиты",
    "CL_PRODUCT_ZPP_MARK": "Зарплатный проект",
    "FTS_FLAG": "Есть данные ФТС",
    "FTS_COUNTRY_NAME": "Страна в данных ФТС",
    "FACTORING_MARK": "Факторинг",
}

WATCHED_FIELDS = list(FIELD_LABELS)


class Feature(BaseModel):
    id: str
    label: str
    value: str
    origin: str
    source_fields: list[str]


class Exposure(BaseModel):
    id: str
    title: str
    basis: str
    source: str
    confidence: str
    strength: float
    feature_refs: list[str]
    assumptions: list[str]


class Counterparty(BaseModel):
    client_id: str
    direction: str
    pay_amt: float | None
    pay_cnt: int | None
    same_group: bool


class ClientProfile(BaseModel):
    client_id: str
    title: str
    money_role: str
    cohort_key: str | None
    cohort_share: float | None
    features: list[Feature]
    missing_data: list[str]
    exposures: list[Exposure]
    counterparties: list[Counterparty]


class ClientSummary(BaseModel):
    client_id: str
    title: str
    money_role: str
    exposure_ids: list[str]


def load_profiles() -> list[ClientProfile]:
    if _using_case():
        from app.profile.case_data import load_case_profiles

        return load_case_profiles()
    company_path = companies_csv()
    if not company_path.is_file():
        raise FileNotFoundError(f"Нет файла компаний: {company_path}")
    companies = read_csv(company_path)
    payments = read_csv(payments_csv()) if payments_csv() else []
    rules = _load_yaml(ROOT / "config" / "exposures.yaml")["exposures"]
    prefixes, ambiguous = _okved_rules()
    return _build_profiles(companies, payments, rules, prefixes, ambiguous)


def get_profile(client_id: str) -> ClientProfile | None:
    if _using_case():
        from app.profile.case_data import case_profile

        return case_profile(client_id)
    for profile in load_profiles():
        if profile.client_id == client_id:
            return profile
    return None


def _using_case() -> bool:
    from app.profile.sources import using_case_extract

    return using_case_extract()


def _build_profiles(
    companies: list[dict[str, str]],
    payments: list[dict[str, str]],
    rules: dict,
    prefixes: dict[str, str],
    ambiguous: list[str] | None = None,
) -> list[ClientProfile]:
    by_id = {_text(row.get("SYNTHETIC_U")): row for row in companies if _text(row.get("SYNTHETIC_U"))}
    cohort_revenue = _sum_by(
        by_id.values(),
        lambda row: _cohort_key(row),
        lambda row: _money(row.get("REVENUE_COMPANY")),
    )
    group_revenue = _sum_by(
        by_id.values(),
        lambda row: _text(row.get("GROUP_ID")),
        lambda row: _money(row.get("REVENUE_COMPANY")),
    )
    group_size: dict[str, int] = {}
    for row in by_id.values():
        group = _text(row.get("GROUP_ID"))
        if group:
            group_size[group] = group_size.get(group, 0) + 1
    links = _links(payments, by_id)

    profiles: list[ClientProfile] = []
    for client_id, row in by_id.items():
        features: list[Feature] = []
        missing: list[str] = []
        for field in WATCHED_FIELDS:
            raw = row.get(field)
            if field in FLAG_FIELDS:
                value = _mark(raw)
            elif _blank(raw):
                value = None
            else:
                value = _text(raw)
            if value is None:
                if _counts_as_missing(field, row):
                    missing.append(field)
                continue
            features.append(
                Feature(
                    id=field,
                    label=FIELD_LABELS[field],
                    value=value,
                    origin="bank_data",
                    source_fields=[field],
                )
            )

        revenue = _money(row.get("REVENUE_COMPANY"))
        cohort = _cohort_key(row)
        share = None
        if revenue is not None and cohort and cohort_revenue.get(cohort):
            share = round(revenue / cohort_revenue[cohort], 4)
            features.append(
                Feature(
                    id="cohort_share",
                    label="Доля выручки в когорте ОКВЭД среди клиентов банка",
                    value=str(share),
                    origin="derived",
                    source_fields=["REVENUE_COMPANY", "MAINOKVED_CODE"],
                )
            )

        group = _text(row.get("GROUP_ID"))
        if group and revenue is not None and group_revenue.get(group):
            rank = _group_rank(client_id, group, by_id)
            features.append(
                Feature(
                    id="group_rank",
                    label="Место в группе по выручке",
                    value=str(rank),
                    origin="derived",
                    source_fields=["GROUP_ID", "REVENUE_COMPANY"],
                )
            )
            features.append(
                Feature(
                    id="group_size",
                    label="Компаний в группе",
                    value=str(group_size.get(group, 1)),
                    origin="derived",
                    source_fields=["GROUP_ID"],
                )
            )

        theme = _theme(_text(row.get("MAINOKVED_CODE")), prefixes, ambiguous)
        if theme:
            features.append(
                Feature(
                    id="okved_theme",
                    label="Товарная тема по ОКВЭД",
                    value=theme,
                    origin="derived",
                    source_fields=["MAINOKVED_CODE"],
                )
            )

        role = _money_role(row)
        features.append(
            Feature(
                id="money_role",
                label="Роль по стоимости денег",
                value=role,
                origin="derived",
                source_fields=["CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK"],
            )
        )

        counterparties = links.get(client_id, [])
        exposures = _exposures(row, rules, theme, counterparties)
        title = _text(row.get("MAINOKVED_NAME")) or client_id
        profiles.append(
            ClientProfile(
                client_id=client_id,
                title=title,
                money_role=role,
                cohort_key=cohort,
                cohort_share=share,
                features=features,
                missing_data=missing,
                exposures=exposures,
                counterparties=counterparties,
            )
        )
    return sorted(profiles, key=lambda item: item.client_id)


def apply_case_stats(
    profile: ClientProfile,
    *,
    cohort_share: float | None,
    group_rank: int | None,
    group_size: int | None,
    persons: int,
) -> ClientProfile:
    """Подменяет доли, посчитанные по всей выгрузке, а не по одной строке."""
    drop = {"cohort_share", "group_rank", "group_size", "related_persons"}
    features = [feature for feature in profile.features if feature.id not in drop]
    if cohort_share is not None:
        features.append(
            Feature(
                id="cohort_share",
                label="Доля выручки в когорте ОКВЭД среди клиентов банка",
                value=str(cohort_share),
                origin="derived",
                source_fields=["REVENUE_COMPANY", "MAINOKVED_CODE"],
            )
        )
    if group_rank is not None:
        features.append(
            Feature(
                id="group_rank",
                label="Место в группе по выручке",
                value=str(group_rank),
                origin="derived",
                source_fields=["GROUP_ID", "REVENUE_COMPANY"],
            )
        )
    if group_size is not None:
        features.append(
            Feature(
                id="group_size",
                label="Компаний в группе",
                value=str(group_size),
                origin="derived",
                source_fields=["GROUP_ID"],
            )
        )
    if persons:
        features.append(
            Feature(
                id="related_persons",
                label="Связанные физлица",
                value=str(persons),
                origin="bank_data",
                source_fields=["SYNTHETIC_F"],
            )
        )
    return profile.model_copy(update={"cohort_share": cohort_share, "features": features})


def _exposures(row: dict[str, str], rules: dict, theme: str | None, counterparties: list[Counterparty]) -> list[Exposure]:
    found: list[Exposure] = []
    for exposure_id, rule in rules.items():
        title, basis, source, confidence = _exposure_meta(exposure_id, rule)
        when = rule.get("when") or {}
        refs: list[str] = []
        if "any_flag" in when and any(_flag(row.get(field)) for field in when["any_flag"]):
            refs.extend(field for field in when["any_flag"] if _flag(row.get(field)))
        elif "feature_present" in when and any(not _blank(row.get(field)) for field in when["feature_present"]):
            refs.extend(field for field in when["feature_present"] if not _blank(row.get(field)))
        elif "derived_theme" in when and theme in when["derived_theme"]:
            refs.append("okved_theme")
        elif when.get("derived") == "counterparty_match" and counterparties:
            refs.append("counterparties")
        else:
            continue
        found.append(
            Exposure(
                id=exposure_id,
                title=title,
                basis=basis,
                source=source,
                confidence=confidence,
                strength=float(rule.get("strength", 0)),
                feature_refs=refs,
                assumptions=list(rule.get("assumptions") or []),
            )
        )
    return found


def _exposure_meta(exposure_id: str, rule: dict) -> tuple[str, str, str, str]:
    title = str(rule.get("title") or "").strip()
    basis = str(rule.get("basis") or "").strip()
    source = str(rule.get("source") or "").strip()
    confidence = str(rule.get("confidence") or "").strip()
    known_basis = basis in {"confirmed", "hypothesis"}
    known_source = source in {"bank_data", "derived", "payments", "client_input"}
    known_confidence = confidence in {"high", "medium", "low"}
    if not title or not known_basis or not known_source or not known_confidence:
        raise ValueError(f"У экспозиции {exposure_id} не заданы название, основание, источник или уверенность")
    if basis == "hypothesis" and not list(rule.get("assumptions") or []):
        raise ValueError(f"Отраслевая гипотеза {exposure_id} без оговорки")
    return title, basis, source, confidence


def _links(payments: list[dict[str, str]], companies: dict[str, dict[str, str]]) -> dict[str, list[Counterparty]]:
    """Одна пара плательщик–получатель — одна связь. Совпадающие строки не суммируются."""
    grouped: dict[tuple[str, str, str], list[tuple[float | None, int | None]]] = {}
    for row in payments:
        payer = _text(row.get("PAYER"))
        beneficiary = _text(row.get("BENEFICIARY"))
        if not payer or not beneficiary or payer == beneficiary:
            continue
        amount = _money(row.get("PAY_AMT"))
        count_value = _money(row.get("PAY_CNT"))
        count = int(count_value) if count_value is not None else None
        grouped.setdefault((payer, beneficiary, "out"), []).append((amount, count))
        grouped.setdefault((beneficiary, payer, "in"), []).append((amount, count))

    result: dict[str, list[Counterparty]] = {}
    for (owner, other, direction), parts in grouped.items():
        if owner not in companies:
            continue
        owner_group = _text(companies[owner].get("GROUP_ID"))
        other_group = _text(companies.get(other, {}).get("GROUP_ID"))
        result.setdefault(owner, []).append(
            Counterparty(
                client_id=other,
                direction=direction,
                pay_amt=_agreed_number(amount for amount, _count in parts),
                pay_cnt=_agreed_number(count for _amount, count in parts),
                same_group=bool(owner_group and owner_group == other_group),
            )
        )
    for items in result.values():
        items.sort(key=lambda item: (item.pay_amt is None, -(item.pay_amt or 0), item.client_id))
    return result


def _agreed_number(values) -> float | int | None:
    found = [value for value in values if value is not None]
    unique = set(found)
    if len(unique) == 1:
        return unique.pop()
    return None


def _counts_as_missing(field: str, row: dict[str, str]) -> bool:
    if field == "FTS_COUNTRY_NAME" and not _flag(row.get("FTS_FLAG")):
        return False
    return True


def _money_role(row: dict[str, str]) -> str:
    borrower = _flag(row.get("CL_PRODUCT_LOA_MARK")) or _flag(row.get("CL_PRODUCT_LEA_MARK"))
    saver = _flag(row.get("CL_PRODUCT_DEP_MARK"))
    if borrower and saver:
        return "both"
    if borrower:
        return "borrower"
    if saver:
        return "saver"
    return "neither"


def _cohort_key(row: dict[str, str]) -> str | None:
    code = _text(row.get("MAINOKVED_CODE"))
    if not code:
        return None
    return code.split(".", 1)[0]


def _okved_rules() -> tuple[dict[str, str], list[str]]:
    payload = _load_yaml(ROOT / "config" / "okved_map.yaml")
    prefixes = {str(prefix): str(theme) for prefix, theme in payload["prefixes"].items()}
    ambiguous = [str(item) for item in payload.get("ambiguous") or []]
    return prefixes, ambiguous


def _theme(code: str, prefixes: dict[str, str], ambiguous: list[str] | None = None) -> str | None:
    """Самый длинный цифровой префикс. Неоднозначный код длиннее родителя тему не получает."""
    digits = _okved_digits(code)
    if not digits:
        return None
    theme_length, theme = _longest_okved(digits, prefixes)
    blocked_length, _blocked = _longest_okved(digits, {item: "" for item in ambiguous or []})
    if theme is None or blocked_length >= theme_length:
        return None
    return theme


def _longest_okved(digits: str, prefixes: dict[str, str]) -> tuple[int, str | None]:
    best_length = -1
    best: str | None = None
    for prefix, theme in prefixes.items():
        needle = _okved_digits(prefix)
        if needle and digits.startswith(needle) and len(needle) > best_length:
            best = theme
            best_length = len(needle)
    return best_length, best


def _okved_digits(value: str) -> str:
    return "".join(character for character in str(value) if character.isdigit())


def _group_rank(client_id: str, group: str, companies: dict[str, dict[str, str]]) -> int:
    members = []
    for cid, row in companies.items():
        if _text(row.get("GROUP_ID")) != group:
            continue
        revenue = _money(row.get("REVENUE_COMPANY"))
        if revenue is None:
            continue
        members.append((cid, revenue))
    members.sort(key=lambda item: item[1], reverse=True)
    for index, (cid, _revenue) in enumerate(members, start=1):
        if cid == client_id:
            return index
    return len(members)


def _sum_by(rows, key_of, value_of) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in rows:
        key = key_of(row)
        value = value_of(row)
        if not key or value is None:
            continue
        totals[key] = totals.get(key, 0.0) + value
    return totals


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _mark(value: str | None) -> str | None:
    """Пустая ячейка — не факт. Явный 0 остаётся «нет», а не пропуском и не нулём."""
    text = _text(value).lower().replace(",", ".")
    if not text:
        return None
    if _flag(value):
        return "есть"
    if text in FLAG_OFF:
        return "нет"
    try:
        if float(text) == 0.0:
            return "нет"
    except ValueError:
        return None
    return None


def _flag(value: str | None) -> bool:
    text = _text(value).lower().replace(",", ".")
    if text in FLAG_ON:
        return True
    try:
        return float(text) == 1.0
    except ValueError:
        return False


def _blank(value: str | None) -> bool:
    return _text(value) == ""


def _text(value: str | None) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _money(value: str | None) -> float | None:
    text = _text(value).replace(" ", "").replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
