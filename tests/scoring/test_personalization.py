from datetime import date

import yaml

from app.db import ROOT
from app.profile.build import WATCHED_FIELDS, _build_profiles, _load_yaml, _okved_rules, get_profile
from app.scoring.feed import _score_feed, build_feed

_TODAY = date(2026, 10, 21)


def _cases() -> dict:
    return yaml.safe_load((ROOT / "eval" / "personalization_cases.yaml").read_text(encoding="utf-8"))


def _profiles(payload: dict) -> dict:
    rules = _load_yaml(ROOT / "config" / "exposures.yaml")["exposures"]
    prefixes, ambiguous = _okved_rules()
    found = {}
    for company in payload["companies"]:
        if company["source"] == "demo":
            found[company["id"]] = get_profile(company["id"])
            continue
        row = {field: "" for field in WATCHED_FIELDS}
        row.update(SYNTHETIC_U=company["id"], **{key: str(value) for key, value in company["fields"].items()})
        found[company["id"]] = _build_profiles([row], [], rules, prefixes, ambiguous)[0]
    return found


def _feeds(profiles: dict) -> dict:
    feeds = {}
    for client_id, profile in profiles.items():
        assert profile is not None
        if client_id in {"U-BORROWER", "U-SAVER", "U-PEER"}:
            feeds[client_id] = build_feed(client_id, today=_TODAY)
        else:
            feeds[client_id] = _score_feed(profile, _TODAY, "eval")
        assert feeds[client_id] is not None
    return feeds


def _item(feed, news_id: str):
    for item in feed.top + feed.less_relevant:
        if item.news_id == news_id:
            return item
    raise AssertionError(news_id)


def _text(value: str) -> str:
    return " ".join(value.split())


def _values(item) -> dict[str, float]:
    return {factor.id: factor.value for factor in item.factors}


def test_one_news_has_different_scores_for_different_profiles():
    feeds = _feeds(_profiles(_cases()))
    scores = {client_id: _item(feed, "cbr-rate-14").score for client_id, feed in feeds.items()}
    assert len(set(scores.values())) == len(scores)
    assert scores["U-BORROWER"] > scores["U-SAVER"] > scores["U-GAPS"] > scores["U-PEER"] > scores["U-UNKNOWN"]


def test_score_gap_is_explained_by_named_features():
    feeds = _feeds(_profiles(_cases()))
    borrower = _item(feeds["U-BORROWER"], "cbr-rate-14")
    saver = _item(feeds["U-SAVER"], "cbr-rate-14")
    assert "подтверждено: Заёмное финансирование" in borrower.reason
    assert "подтверждено: Депозиты" in saver.reason
    assert "Депозиты" not in borrower.reason
    assert "Заёмное финансирование" not in saver.reason
    changed = {
        factor_id
        for factor_id in set(_values(borrower)) | set(_values(saver))
        if _values(borrower).get(factor_id, 0) != _values(saver).get(factor_id, 0)
    }
    assert changed == {"exposure", "scale", "gap"}
    delta = sum(_values(borrower).get(factor_id, 0) - _values(saver).get(factor_id, 0) for factor_id in changed)
    assert round(borrower.score - saver.score, 4) == round(delta, 4)

    gas_metal = _item(feeds["U-BORROWER"], "gas-price")
    gas_energy = _item(feeds["U-SAVER"], "gas-price")
    assert "отрасль: Металлы" in gas_metal.reason
    assert "отрасль: Энергия" in gas_energy.reason
    assert gas_metal.score != gas_energy.score


def test_unknown_exposure_does_not_create_false_confidence():
    feeds = _feeds(_profiles(_cases()))
    unknown_rate = _item(feeds["U-UNKNOWN"], "cbr-rate-14")
    unknown_gas = _item(feeds["U-UNKNOWN"], "gas-price")
    unknown_tax = _item(feeds["U-UNKNOWN"], "tax-draft")
    gaps_tax = _item(feeds["U-GAPS"], "tax-draft")
    currency = _item(feeds["U-BORROWER"], "usd-rub")

    assert unknown_rate.band == "low"
    assert "rate_borrower" not in unknown_rate.matched_features
    assert "подтверждено" not in unknown_rate.reason
    assert unknown_gas.band == "low"
    assert unknown_gas.matched_features == []
    assert "commodity_metals" not in unknown_gas.matched_features
    assert "commodity_energy" not in unknown_gas.matched_features
    assert unknown_tax.band == "low"
    assert "exposure" not in _values(unknown_tax)
    assert gaps_tax.matched_features == []
    assert gaps_tax.band == "low"
    assert "fx_exposure" not in currency.matched_features
    assert currency.band == "low"
    assert "Валютная позиция" not in currency.reason


def test_duplicates_do_not_take_several_feed_slots():
    feeds = _feeds(_profiles(_cases()))
    for feed in feeds.values():
        visible = [item for item in feed.top + feed.less_relevant if item.band in {"high", "extra"}]
        types = [item.event_type for item in visible]
        assert len(types) == len(set(types))
        order = [item.news_id for item in feed.top + feed.less_relevant]
        if "gas-price" in order and "brent-oil" in order:
            assert order.index("gas-price") < order.index("brent-oil")
            assert _item(feed, "brent-oil").band == "low"


def test_rerun_keeps_the_same_order():
    payload = _cases()
    profiles = _profiles(payload)
    for client_id, profile in profiles.items():
        assert profile is not None
        first = _score_feed(profile, _TODAY, "eval")
        second = _score_feed(profile, _TODAY, "eval")
        assert [(item.news_id, item.score, item.band) for item in first.top] == [
            (item.news_id, item.score, item.band) for item in second.top
        ]
        assert [(item.news_id, item.score, item.band) for item in first.less_relevant] == [
            (item.news_id, item.score, item.band) for item in second.less_relevant
        ]

    feeds = _feeds(profiles)
    for grade in payload["grades"]:
        item = _item(feeds[grade["company"]], grade["news"])
        assert item.band == grade["band"]
        assert item.score == grade["score"]
        assert [factor.id for factor in item.factors] == grade["factors"]
        assert _text(item.reason) == _text(grade["explanation"])
        assert item.reason
