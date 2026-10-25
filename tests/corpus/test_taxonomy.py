import yaml

from app.corpus.search import classify
from app.db import ROOT

_TOPICS = {
    "monetary_policy": "Ключевая ставка",
    "inflation_macro": "Инфляция",
    "bonds": "Облигации",
    "metals": "Металлы",
    "macro": "Макроэкономика",
    "consumption_labor": "Потребление",
    "regulation_tax": "Регулирование",
    "unknown": "Неизвестно/неоднозначно",
}


def _rules() -> dict:
    return {
        "classification": {"unknown": "unknown", "min_broad_hits": 2},
        "event_types": {
            "unknown": {"title": "Неизвестно/неоднозначно", "priority": 0, "triggers": []},
            "high": {"priority": 90, "triggers": [{"phrase": "альфа"}]},
            "low": {"priority": 10, "triggers": [{"phrase": "бета"}, {"phrase": "гамма"}]},
            "wide": {"priority": 90, "triggers": [{"phrase": "ставк", "broad": True}]},
        },
    }


def test_categories_cover_the_decision_topics():
    payload = yaml.safe_load((ROOT / "config" / "taxonomy.yaml").read_text(encoding="utf-8"))
    types = payload["event_types"]
    assert payload["classification"]["unknown"] == "unknown"
    for event_type, title in _TOPICS.items():
        assert title in types[event_type]["title"]
        assert "priority" in types[event_type]
    assert types["fx"]["title"]
    assert types["precious_metals"]["title"]
    assert types["commodities_energy"]["title"]
    assert types["sanctions_trade"]["title"]


def test_clear_events_keep_their_category():
    assert classify("Ключевая ставка сохранена на уровне 14%") == "monetary_policy"
    assert classify("Инфляция в августе осталась повышенной") == "inflation_macro"
    assert classify("Доходность ОФЗ снизилась на вторичном рынке") == "bonds"
    assert classify("Сталь подешевела на внутреннем рынке") == "metals"
    assert classify("Оценка роста ВВП за полугодие снижена") == "macro"
    assert classify("Безработица осталась на низком уровне") == "consumption_labor"
    assert classify("Опубликован проект поправок по налогу на прибыль") == "regulation_tax"
    assert classify("Налоговая ставка для компаний вырастет") == "regulation_tax"
    assert classify("Поставки нефти временно остановили на время ремонта") == "commodities_energy"



def test_equal_evidence_is_ambiguous_even_if_priority_differs():
    assert classify("Инфляция ускорилась, а золото обновило максимум") == "unknown"
    assert classify("Доходность ОФЗ снизилась вслед за ключевой ставкой") == "unknown"
    assert classify("Росстат сообщил о росте ВВП и инфляции") == "unknown"
    assert classify("Нефть и сталь подешевели за неделю") == "unknown"
    assert classify("альфа и бета", _rules()) == "unknown"


def test_low_confidence_is_unknown_not_a_guess():
    assert classify("") == "unknown"
    assert classify("Рынок вырос на фоне новостей") == "unknown"
    assert classify("Ставка выросла") == "unknown"
    assert classify("ставка", _rules()) == "unknown"


def test_more_exact_triggers_beat_a_higher_priority():
    assert classify("альфа, бета и гамма", _rules()) == "low"
    assert classify("Ключевая ставка и денежно-кредитная политика. Инфляция упомянута один раз.") == "monetary_policy"
