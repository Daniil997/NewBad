"""Business concepts shared by news ranking and bank-source matching."""

import re

CONCEPT_PATTERNS = {
    "rates": r"ключев\w* ставк|денежно.кредитн|(?:стоимост|услови)\w* кредит|кредитован|заемн|заёмн|рефинанс|ruonia|руониа",
    "inflation": r"инфляц|дефляц|потребительск\w* цен|\bипц\b",
    "fuel": r"дизел|бензин|топлив|нефтепродукт|\bнпз\b",
    "oil": r"\bнефт(?:ь|и|я|ян\w*)\b|brent|urals",
    "gas": r"\bгаз(?:а|у|ом|ов\w*)?\b|\bспг\b|\blng\b|новатэк",
    "coal": r"\bуг(?:оль|ля|лю|лем|ольн\w*)\b",
    "metals": r"\bстал(?:ь|ьн\w*)\b|северсталь|металлург|алюмини|\bникел|\bмед(?:ь|и|н\w*)\b",
    "gold": r"золот|драгметалл|драгоценн\w* металл",
    "demand": r"спрос|потреблен|розничн|ритейл|потребительск\w* активност|потребительск\w* расход|продаж\w* магазин",
    "labor": r"рын\w* труд|безработиц|зарплат|заработн\w* плат|кадр|ваканси",
    "fx": r"валют|курс\w* рубл|ослаблен\w* (?:рубл|доллар)|укреплен\w* (?:рубл|доллар)|юан|доллар",
    "trade": r"экспорт|импорт|внешн\w* торгов|внешнеторгов|тамож|пошлин|санкци",
    "bonds": r"облигац|\bофз\b|купон|долгов\w* рын",
    "property": r"недвижим|аренд|кадастр|помещени|торгов\w* центр|строительств",
    "warehousing": r"\bсклад(?:а|у|е|ом|ы|ов|ам|ами|ах|ск\w*)?\b|логистическ\w* центр|пункт\w* выдач|\bпвз\b",
    "logistics": r"логистик|перевоз|транспорт|поставк|груз|порт\w* инфраструкт",
    "tax": r"налог|\bндс\b|\bндфл\b|вычет",
    "insurance": r"страхов|полис|тарифн\w* коридор",
    "macro": r"\bввп\b|макроэконом|макростатистик|экономическ\w* рост|рост\w* экономик|промпроизводств",
}


def concepts(text: str) -> set[str]:
    folded = text.casefold().replace("ё", "е")
    result = {name for name, pattern in CONCEPT_PATTERNS.items() if re.search(pattern, folded)}
    if re.search(r"(?:потреблен|производ|выпуск|цен|рын|прокат|экспорт|импорт)\w*(?:\W+\w+){0,2}\W+стали\b", folded):
        result.add("metals")
    # Consumption of steel and demand for freight are not household retail demand.
    if result & {"metals", "gas", "coal", "oil"} or re.search(r"спрос\w* на (?:автоперевоз|перевоз|груз)", folded):
        result.discard("demand")
    return result


def industry_tags(profile: dict) -> set[str]:
    """Derive missing UI tags from explicit OKVED/theme, without rewriting the profile."""
    tags = set(profile.get("industry_tags") or [])
    theme = str(profile.get("okved_theme") or "")
    code = str(profile.get("okved") or "")
    mappings = {"energy": "energy_raw_materials", "real_estate": "real_estate", "trade": "trade",
                "manufacturing": "manufacturing", "construction": "construction",
                "transport": "transport_logistics", "agriculture": "agriculture"}
    if theme in mappings:
        tags.add(mappings[theme])
    for prefix, tag in (("68", "real_estate"), ("46", "trade"), ("47", "trade"),
                        ("49", "transport_logistics"), ("52", "transport_logistics"),
                        ("41", "construction"), ("62", "it_communications")):
        if code.startswith(prefix):
            tags.add(tag)
    if code.startswith("46.71"):
        tags.add("energy_raw_materials")
    return tags


def exclusion_reason(profile: dict, news) -> str:
    title = news.title.casefold().replace("ё", "е")
    if re.search(r"установлены факты.*(?:манипулирован|инсайдерск)", title):
        return "Частный надзорный случай не подтверждает изменение условий работы этой компании"
    if re.search(r"приглашаем|заявки на участие|состоится.*(?:пресс.конференц|конференц)|"
                 r"онлайн.заняти|провел\w*.*круглый стол|интервью.*радио", title):
        return "Организационный анонс или мероприятие без самостоятельного бизнес-события"
    if re.search(r"семейн\w* ипотек|финансов\w* грамотност.*(?:пенсионер|старш)|бывш\w* жен", title):
        tags = industry_tags(profile)
        description = str(profile.get("industry") or "").casefold()
        if "строитель" not in description and "банков" not in description and "construction" not in tags:
            return "Публикация о личных финансах не подтверждает влияние на операции этой компании"
    return ""
