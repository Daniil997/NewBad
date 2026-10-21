from datetime import datetime, timezone

from app.agent.loop_context import AgentContext
from app.profile.build import Feature
from app.schemas.card import (
    Action,
    BankView,
    Card,
    Claim,
    Consequences,
    LlmTrace,
    Source,
    WhatHappened,
    WhyForCompany,
)

WHY = {
    "rate_borrower": (
        "Компания пользуется кредитом или лизингом, поэтому сохранение ставки удерживает стоимость заёмных денег."
    ),
    "rate_saver": "Компания держит депозит, поэтому сохранение ставки поддерживает доходность размещения.",
    "fx_exposure": "У компании есть внешнеторговые операции, поэтому движение курса касается её расчётов.",
    "country_exposure": "В профиле указана страна внешнеторговых операций, поэтому событие касается этой компании.",
    "commodity_metals": "Отрасль компании связана с металлами, поэтому сырьевая цена влияет на её операции.",
    "commodity_energy": "Отрасль компании связана с энергоносителями, поэтому цена сырья влияет на её операции.",
    "commodity_precious": "Отрасль компании связана с драгметаллами, поэтому цена металла влияет на её операции.",
    "consumption": "Вид деятельности связан с торговлей, поэтому спрос касается выручки компании.",
    "sector_regulation": "У компании указан ОКВЭД, поэтому отраслевое регулирование может изменить её нагрузку.",
}

RISK = {
    "rate_borrower": "Стоимость обслуживания кредита не снижается, пока ставка сохранена.",
    "rate_saver": "Доходность размещения пересмотрят только после нового решения по ставке.",
}

OPPORTUNITY = {
    "rate_borrower": "Есть время пересчитать график кредита до следующего решения по ставке.",
    "rate_saver": "Размещение можно продлить, пока доходность не пересмотрена.",
}

STEP = {
    "rate_borrower": "Сверить ближайшие платежи по кредиту со ставкой {rate}.",
    "rate_saver": "Проверить дату окончания депозита и решить, продлевать ли размещение при ставке {rate}.",
}

QUESTION = {
    "rate_borrower": "Какая часть кредитов компании зависит от ключевой ставки?",
    "rate_saver": "На какой срок размещены действующие депозиты?",
}


def assemble(ctx: AgentContext, copy: dict[str, str] | None = None) -> Card:
    profile = ctx.profile
    news = ctx.news
    if profile is None or news is None or ctx.plan is None:
        raise RuntimeError("Черновик собирается после сбора профиля и новости")
    primary = ctx.matched[0] if ctx.matched else None
    feature_refs = _feature_refs(ctx, primary)
    assumptions = _assumptions(ctx)
    if ctx.bank is not None and ctx.bank_note:
        assumptions.append(ctx.bank_note)
    rate = "14%" if "14" in f"{news.title} {news.excerpt}" else "из новости"
    sources = _sources(ctx, feature_refs)
    fact = Claim(
        id="fact-1",
        text=ctx.extracted_fact or news.excerpt,
        layer="confirmed_fact",
        source_refs=[news.id],
        confidence=0.95,
    )
    claims = [fact]
    bank_view = BankView(status="none")
    missing = list(profile.missing_data)
    if ctx.bank is not None:
        bank_view = BankView(
            status="found",
            quote=ctx.bank.quote,
            summary=ctx.bank.theses[0] if ctx.bank.theses else ctx.bank.quote,
            doc_ref=ctx.bank.doc_id,
            page=ctx.bank.page,
        )
        claims.append(
            Claim(
                id="bank-1",
                text=ctx.bank.quote,
                layer="bank_position",
                source_refs=[ctx.bank.doc_id],
                confidence=0.95,
            )
        )
    else:
        missing.append(ctx.bank_note or "Нет материала банка по теме события.")
    why = Claim(
        id="why-1",
        text=(copy or {}).get("why") or _why_text(primary),
        layer="model_inference",
        source_refs=[news.id],
        feature_refs=feature_refs,
        confidence=0.7,
    )
    risk = (copy or {}).get("risk") or RISK.get(primary or "", _fallback_risk())
    chance = (copy or {}).get("opportunity") or OPPORTUNITY.get(primary or "", _fallback_chance())
    question = (copy or {}).get("question") or QUESTION.get(primary or "", "Нужно ли учитывать это событие в ближайшем решении?")
    return Card(
        card_id=f"{profile.client_id}-{news.id}",
        client_id=profile.client_id,
        event_id=news.id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        llm_trace=LlmTrace(
            model=ctx.plan.model,
            provider=ctx.plan.provider,
            mode=ctx.plan.mode,
            tokens=ctx.plan.tokens,
            retries=ctx.retries,
        ),
        what_happened=WhatHappened(claims=claims, assumptions=assumptions),
        why_for_company=WhyForCompany(claims=[why]),
        consequences=Consequences(
            risks=[_inference("risk-1", risk, news.id, feature_refs)],
            opportunities=[_inference("chance-1", chance, news.id, feature_refs)],
            depends_on=assumptions,
        ),
        action=Action(
            next_step=(copy or {}).get("next_step") or _step(primary, rate),
            questions=[question],
        ),
        bank_view=bank_view,
        sources=sources,
        features_triggered=list(ctx.matched),
        missing_data=missing,
        assumptions=assumptions,
    )


def draft_card(ctx: AgentContext) -> Card:
    from app.agent.generate import request_copy

    copy = request_copy(ctx) if ctx.plan is not None and ctx.plan.provider == "local" else None
    return assemble(ctx, copy)


def _why_text(primary: str | None) -> str:
    if primary is None:
        return "Кредит и депозит в профиле не найдены, поэтому событие не привязано к деньгам компании."
    return WHY.get(primary, "Событие сопоставлено с признаком профиля этой компании.")


def _step(primary: str | None, rate: str) -> str:
    template = STEP.get(primary or "")
    if template is None:
        return "Открыть новость и сопоставить её с признаком компании."
    return template.format(rate=rate)


def _fallback_risk() -> str:
    return "Прямого влияния на кредит или депозит компании по профилю не видно."


def _fallback_chance() -> str:
    return "Можно отложить решение, пока не появится связь события с операциями компании."


def _inference(claim_id: str, text: str, news_id: str, feature_refs: list[str]) -> Claim:
    return Claim(
        id=claim_id,
        text=text,
        layer="model_inference",
        source_refs=[news_id],
        feature_refs=feature_refs,
        confidence=0.6,
    )


def _feature_refs(ctx: AgentContext, primary: str | None) -> list[str]:
    profile = ctx.profile
    assert profile is not None
    if primary is not None:
        for exposure in profile.exposures:
            if exposure.id == primary and exposure.feature_refs:
                return list(exposure.feature_refs)
    for feature in profile.features:
        if feature.id == "MAINOKVED_CODE":
            return [feature.id]
    return [profile.features[0].id]


def _assumptions(ctx: AgentContext) -> list[str]:
    profile = ctx.profile
    assert profile is not None
    lines: list[str] = []
    for exposure in profile.exposures:
        if exposure.id in ctx.matched:
            lines.extend(exposure.assumptions)
    return lines


def _sources(ctx: AgentContext, feature_refs: list[str]) -> list[Source]:
    profile = ctx.profile
    news = ctx.news
    assert profile is not None and news is not None
    sources = [
        Source(id=news.id, kind="news", title=news.title, url=news.url or None),
    ]
    if ctx.bank is not None:
        sources.append(Source(id=ctx.bank.doc_id, kind="bank_analytics", title=ctx.bank.title, origin="bank_analytics"))
    by_id = {feature.id: feature for feature in profile.features}
    for feature_id in feature_refs:
        feature = by_id.get(feature_id)
        sources.append(_feature_source(feature_id, feature))
    return sources


def _feature_source(feature_id: str, feature: Feature | None) -> Source:
    if feature is None:
        return Source(id=feature_id, kind="company_feature", title=feature_id, origin="derived")
    return Source(id=feature.id, kind="company_feature", title=feature.label, origin=feature.origin)
