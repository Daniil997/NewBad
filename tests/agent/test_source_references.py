import pytest

from app.agent.generate import _check_report
from app.agent.source_references import clean_source_references, display_report


SOURCE = "bank:inflation:reviewed:1"


@pytest.mark.parametrize("marker", [f"({SOURCE})", f"[{SOURCE}]", f"`{SOURCE}`", SOURCE, f"( {SOURCE} )"])
def test_internal_reference_is_removed_without_removing_claim_or_numbers(marker):
    text = f"По оценке Альфа-Банка {marker}, рост цен на 10% может повысить расходы."
    assert clean_source_references(text, [SOURCE]) == "По оценке Альфа-Банка, рост цен на 10% может повысить расходы."


def test_reference_groups_links_and_markdown_layout():
    ids = [SOURCE, "news:rate-change"]
    text = f"**Риск**\n\n- Затраты ({SOURCE}; news:rate-change).\n- См. [Инфляция]({SOURCE})."
    assert clean_source_references(text, ids) == "**Риск**\n\n- Затраты.\n- См. Инфляция."
    assert clean_source_references(f"См. {SOURCE}.", ids) == "См."


def test_unknown_ids_prefixes_urls_and_normal_numbers_are_not_changed():
    text = f"Оценка (1): ставка 14,0%. https://example.org/{SOURCE} {SOURCE}9 bank:unknown:reviewed:2."
    assert clean_source_references(text, [SOURCE]) == text
    assert clean_source_references(text, []) == text


def test_display_report_preserves_structured_citations_and_original_snapshot():
    report = {"risks": f"Повышение расходов ({SOURCE}).", "questions": [f"Каковы расходы [{SOURCE}]?"],
              "evidence_ids": [SOURCE], "bank_sources": [{"evidence_id": SOURCE, "text": "Текст банка", "title": "Инфляция"}]}
    result = display_report(report)
    assert result["risks"] == "Повышение расходов."
    assert result["questions"] == ["Каковы расходы?"]
    assert result["evidence_ids"] == [SOURCE] and result["bank_sources"] == report["bank_sources"]
    assert SOURCE in report["risks"] and SOURCE in report["questions"][0]


def test_report_cleans_reference_before_numeric_validation_but_still_rejects_invented_numbers():
    source_id = "bank:inflation:reviewed:999"
    news_id = "news:inflation"
    allowed = {news_id: {"text": "Цены на топливо выросли, что повышает расходы предприятий."},
               source_id: {"kind": "bank", "text": "Рост цен на топливо является проинфляционным фактором."}}
    payload = {"summary": allowed[news_id]["text"],
               "relevance": "Рост закупочных цен может повысить затраты компании и сократить маржу.",
               "risks": f"Рост цен на топливо может снизить рентабельность компании ({source_id}).",
               "opportunities": "Если удастся согласовать цены заранее, это может ограничить рост затрат компании.",
               "evidence_ids": [news_id, source_id]}
    report, reason = _check_report(payload, allowed, news_id, True)
    assert not reason and report and source_id not in report["risks"]
    assert source_id in report["evidence_ids"]
    report, reason = _check_report({**payload, "risks": "Затраты предприятия вырастут на 999 процентов из-за роста цен на топливо."}, allowed, news_id, True)
    assert report is None and "числа" in reason
