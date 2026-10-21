from app.agent.draft import draft_card
from app.agent.loop import _gather, _plan, build_card
from app.agent.loop_context import AgentContext
from app.agent.verify import verify_card
from app.schemas.card import Claim, Source


def _ready(monkeypatch, tmp_path) -> tuple[AgentContext, object]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ctx = AgentContext(client_id="U-BORROWER", event_id="cbr-rate-14")
    _plan(ctx)
    _gather(ctx)
    card = draft_card(ctx)
    return ctx, card


def test_rate_card_passes_and_keeps_confirmed_confidence(monkeypatch, tmp_path):
    ctx, card = _ready(monkeypatch, tmp_path)
    assert verify_card(card, ctx) == []
    why = card.why_for_company.claims[0]
    assert why.layer == "model_inference"
    assert why.confidence == 0.7
    built = build_card("U-BORROWER", "cbr-rate-14")
    assert built is not None
    assert built.why_for_company.claims[0].confidence == 0.7


def test_unknown_source_and_wrong_page_are_rejected(monkeypatch, tmp_path):
    ctx, card = _ready(monkeypatch, tmp_path)
    card.sources.append(Source(id="missing-doc", kind="bank_analytics", title="Нет такого"))
    card.bank_view.page = 9
    issues = verify_card(card, ctx)
    assert "source:missing-doc" in issues
    assert "page" in issues
    assert "quote" not in issues


def test_company_fact_must_match_the_profile(monkeypatch, tmp_path):
    ctx, card = _ready(monkeypatch, tmp_path)
    card.features_triggered = ["no-such-exposure"]
    feature = next(item for item in ctx.profile.features if item.id == "MAINOKVED_CODE")
    card.what_happened.claims[0] = card.what_happened.claims[0].model_copy(
        update={"feature_refs": [feature.id], "text": "Код деятельности 00.00"}
    )
    card.sources.append(Source(id=feature.id, kind="company_feature", title=feature.label, origin=feature.origin))
    issues = verify_card(card, ctx)
    assert "profile:no-such-exposure" in issues
    assert f"profile:{feature.id}" in issues


def test_fact_and_inference_stay_in_their_layers(monkeypatch, tmp_path):
    ctx, card = _ready(monkeypatch, tmp_path)
    card.what_happened.claims[0] = card.what_happened.claims[0].model_copy(update={"layer": "model_inference"})
    card.why_for_company.claims[0] = card.why_for_company.claims[0].model_copy(update={"layer": "confirmed_fact"})
    issues = verify_card(card, ctx)
    assert "layer:fact-1" in issues
    assert "layer:why-1" in issues


def test_opposite_sources_are_a_conflict(monkeypatch, tmp_path):
    ctx, card = _ready(monkeypatch, tmp_path)
    ctx.news = ctx.news.model_copy(update={"excerpt": "Рост цен на металлы ускорился и остаётся высоким."})
    card.what_happened.claims[0] = card.what_happened.claims[0].model_copy(
        update={"text": "Рост цен на металлы ускорился и остаётся высоким."}
    )
    quote = "Снижение цен на металлы замедлило выручку компаний."
    ctx.bank = ctx.bank.model_copy(update={"text": f"{quote}. Материал банка.", "quote": quote, "theses": []})
    card.bank_view.quote = quote
    card.what_happened.claims[1] = card.what_happened.claims[1].model_copy(update={"text": quote})
    assert "conflict" in verify_card(card, ctx)


def test_bare_inference_is_refused_and_hypothesis_lowers_confidence(monkeypatch, tmp_path):
    ctx, card = _ready(monkeypatch, tmp_path)
    bare = Claim(id="risk-bare", text="Это ничем не подтверждено.", layer="model_inference", confidence=0.9)
    card.consequences.risks.append(bare)
    card.features_triggered = ["commodity_metals"]
    card.why_for_company.claims[0].confidence = 0.9
    issues = verify_card(card, ctx)
    assert "evidence:risk-bare" in issues
    assert card.consequences.risks[-1].confidence == 0.3
    assert card.why_for_company.claims[0].confidence == 0.5
