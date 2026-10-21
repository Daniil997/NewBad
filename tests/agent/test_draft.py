import re

from app.agent.draft import assemble
from app.agent.loop import build_card
from app.agent.loop_context import AgentContext
from app.agent.tools import find_news
from app.corpus.search import NewsItem
from app.llm.router import PlanChoice
from app.profile.build import get_profile

_DIGIT = re.compile(r"\d")


def _plan() -> PlanChoice:
    return PlanChoice(tools=["get_profile"], model="mock", provider="mock", mode="mock")


def _news(title: str, excerpt: str) -> NewsItem:
    return NewsItem(
        id="bare-14",
        url="",
        title=title,
        published_at="2026-09-01",
        excerpt=excerpt,
        event_type="unknown",
        event_title=None,
    )


def _inference_text(card) -> str:
    parts = [claim.text for claim in card.why_for_company.claims]
    parts.extend(claim.text for claim in card.consequences.risks)
    parts.extend(claim.text for claim in card.consequences.opportunities)
    parts.append(card.action.next_step)
    parts.extend(card.action.questions)
    return " ".join(parts)


def test_template_does_not_copy_a_rate_out_of_the_news(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    card = build_card("U-BORROWER", "cbr-rate-14")
    assert card is not None
    assert not _DIGIT.search(_inference_text(card))
    assert "14%" in card.what_happened.claims[0].text
    assert "14%" in card.bank_view.quote
    assert any(item.startswith("Неизвестно:") and "ставк" in item.lower() for item in card.assumptions)
    assert "Неизвестно: Регион" in card.missing_data


def test_a_bare_number_in_the_title_does_not_become_a_rate():
    profile = get_profile("U-BORROWER")
    assert profile is not None
    ctx = AgentContext(
        client_id=profile.client_id,
        event_id="bare-14",
        profile=profile,
        news=_news("14 поставщиков изменили прайс", "В выборке обсуждают поставщиков, решения по ставке нет."),
        plan=_plan(),
        matched=["rate_borrower"],
    )
    card = assemble(ctx)
    assert "14" not in _inference_text(card)
    assert card.bank_view.status == "none"
    assert card.bank_view.quote == ""
    assert card.bank_view.summary == ""
    assert all(claim.layer != "bank_position" for claim in card.all_claims())
    assert "Нет материала банка по теме события." in card.missing_data
    assert all(item.kind != "bank_analytics" for item in card.sources)


def test_unlinked_profile_gets_no_generic_risk():
    profile = get_profile("U-PEER")
    news = find_news("cbr-rate-14")
    assert profile is not None and news is not None
    ctx = AgentContext(
        client_id=profile.client_id,
        event_id=news.id,
        profile=profile,
        news=news,
        plan=_plan(),
        matched=[],
    )
    card = assemble(ctx)
    assert card.consequences.risks == []
    assert card.consequences.opportunities == []
    assert "кредит" not in card.why_for_company.claims[0].text.lower()
    assert any("связь события с профилем" in item for item in card.assumptions)


def test_industry_risk_stays_with_the_profile_and_is_marked_as_assumption():
    profile = get_profile("U-BORROWER")
    news = find_news("cbr-rate-14")
    assert profile is not None and news is not None
    ctx = AgentContext(
        client_id=profile.client_id,
        event_id=news.id,
        profile=profile,
        news=news,
        plan=_plan(),
        matched=["commodity_metals"],
    )
    card = assemble(ctx)
    risk = card.consequences.risks[0].text.lower()
    assert "металл" in risk
    assert "кредит" not in risk
    assert any(item.startswith("Допущение:") and "ОКВЭД" in item for item in card.assumptions)
    assert card.bank_view.status == "none"
