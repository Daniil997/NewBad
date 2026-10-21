from datetime import datetime, timezone

from app.agent.loop_context import AgentContext
from app.profile.build import FIELD_LABELS
from app.schemas.card import (
    Action,
    BankView,
    Card,
    Claim,
    Consequences,
    LlmTrace,
    ProfileFact,
    Source,
    Verification,
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
    "fx_exposure": "Расчёты зависят от курса, потому что в профиле указана доля валютной выручки.",
    "country_exposure": "Событие важно для страны, которая указана во внешней торговле компании.",
    "commodity_metals": "Цена металла может изменить операции, потому что отрасль компании связана с металлами.",
    "commodity_energy": "Цена энергоносителя может изменить операции, потому что отрасль компании связана с энергией.",
    "commodity_precious": "Цена драгметалла может изменить операции, потому что отрасль компании с ними связана.",
    "consumption": "Спрос может изменить выручку, потому что деятельность компании связана с торговлей.",
    "sector_regulation": "Нагрузка может измениться, потому что у компании указан ОКВЭД.",
    "foreign_trade": "Ограничение может затронуть поставки, потому что у компании есть внешняя торговля.",
    "liquidity_factoring": "Ликвидность может измениться, потому что у компании подключён факторинг.",
    "counterparty": "Платежи могут измениться, если новость касается контрагента компании.",
}

OPPORTUNITY = {
    "rate_borrower": "Есть время пересчитать график кредита до следующего решения по ставке.",
    "rate_saver": "Размещение можно продлить, пока доходность не пересмотрена.",
    "fx_exposure": "Можно сверить валютную долю выручки с курсом из новости.",
    "country_exposure": "Можно проверить долю операций со страной из профиля.",
    "commodity_metals": "Можно сверить цену металла с контрактами компании в этой отрасли.",
    "commodity_energy": "Можно сверить цену энергоносителя с контрактами компании в этой отрасли.",
    "commodity_precious": "Можно сверить цену драгметалла с контрактами компании в этой отрасли.",
    "consumption": "Можно оценить, поддерживает ли новость спрос в торговле компании.",
    "sector_regulation": "Можно заранее сверить ОКВЭД компании с темой регулирования.",
    "foreign_trade": "Можно проверить, какие поставки компании попадают под тему новости.",
    "liquidity_factoring": "Можно сверить лимит факторинга с темой новости.",
    "counterparty": "Можно проверить платежи с контрагентом, которого касается новость.",
}

STEP = {
    "rate_borrower": "Сверить ближайшие платежи по кредиту с решением по ставке из новости.",
    "rate_saver": "Проверить дату окончания депозита и решить, продлевать ли размещение.",
    "fx_exposure": "Сверить валютные расчёты с курсом из новости.",
    "country_exposure": "Проверить, касается ли новость страны из профиля.",
    "commodity_metals": "Сопоставить цену металла из новости с операциями компании.",
    "commodity_energy": "Сопоставить цену энергоносителя из новости с операциями компании.",
    "commodity_precious": "Сопоставить цену драгметалла из новости с операциями компании.",
    "consumption": "Оценить, меняет ли новость спрос в торговле компании.",
    "sector_regulation": "Проверить, относится ли регулирование из новости к ОКВЭД компании.",
    "foreign_trade": "Проверить, касается ли новость импорта или экспорта компании.",
    "liquidity_factoring": "Сверить новость с действующим факторингом компании.",
    "counterparty": "Проверить, назван ли в новости контрагент компании.",
}

QUESTION = {
    "rate_borrower": "Какая часть кредитов компании зависит от ключевой ставки?",
    "rate_saver": "На какой срок размещены действующие депозиты?",
    "fx_exposure": "Какая доля выручки компании номинирована в валюте?",
    "country_exposure": "Какая доля операций приходится на страну из профиля?",
    "commodity_metals": "Какая доля операций компании зависит от цены металла?",
    "commodity_energy": "Какая доля операций компании зависит от цены энергоносителя?",
    "commodity_precious": "Какая доля операций компании зависит от цены драгметалла?",
    "consumption": "Как новость может изменить спрос в торговле компании?",
    "sector_regulation": "Какой режим регулирования относится к ОКВЭД компании?",
    "foreign_trade": "Какая доля выручки приходится на внешнюю торговлю?",
    "liquidity_factoring": "Какой объём факторинга нужно сверить с новостью?",
    "counterparty": "Есть ли в новости контрагент из платежей компании?",
}


def assemble(ctx: AgentContext, copy: dict[str, str] | None = None) -> Card:
    profile = ctx.profile
    news = ctx.news
    if profile is None or news is None or ctx.plan is None:
        raise RuntimeError("Черновик собирается после сбора профиля и новости")
    primary = ctx.matched[0] if ctx.matched else None
    feature_refs = _feature_refs(ctx, primary)
    title = _exposure_title(ctx, primary)
    assumptions = _assumptions(ctx)
    if ctx.bank is not None and ctx.bank_note:
        assumptions.append(ctx.bank_note)
    if primary is None:
        assumptions.append("Допущение не используется: связь события с профилем компании не найдена.")
    bank_ok = ctx.bank is not None and ctx.bank.quote in ctx.bank.text
    sources = _sources(ctx, feature_refs, bank_ok)
    fact = Claim(
        id="fact-1",
        text=ctx.extracted_fact or news.excerpt,
        layer="confirmed_fact",
        source_refs=[news.id],
        confidence=0.95,
    )
    claims = [fact]
    bank_claims: list[Claim] = []
    bank_view = BankView(status="none")
    missing = _unknown(profile.missing_data)
    if bank_ok:
        assert ctx.bank is not None
        bank_view = BankView(
            status="found",
            quote=ctx.bank.quote,
            summary=ctx.bank.theses[0] if ctx.bank.theses else ctx.bank.quote,
            doc_ref=ctx.bank.doc_id,
            page=ctx.bank.page,
        )
        bank_claims.append(
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
    risk = (copy or {}).get("risk") or _linked_text(primary, title, RISK)
    chance = (copy or {}).get("opportunity") or _linked_text(primary, title, OPPORTUNITY)
    question = (copy or {}).get("question") or _question(primary, title)
    profile_facts = _profile_facts(profile, feature_refs)
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
        bank_position=bank_claims,
        why_for_company=WhyForCompany(claims=[why]),
        consequences=Consequences(
            risks=[_inference("risk-1", risk, news.id, feature_refs)] if risk else [],
            opportunities=[_inference("chance-1", chance, news.id, feature_refs)] if chance else [],
            depends_on=assumptions,
        ),
        action=Action(
            next_step=(copy or {}).get("next_step") or _step(primary, title),
            questions=[question],
        ),
        bank_view=bank_view,
        sources=sources,
        profile_facts=profile_facts,
        features_triggered=list(ctx.matched),
        missing_data=missing,
        assumptions=assumptions,
        verification=Verification(status="passed"),
        follow_up=[question],
    )


def draft_card(ctx: AgentContext) -> Card:
    from app.agent.generate import request_copy

    copy = request_copy(ctx) if ctx.plan is not None and ctx.plan.provider == "local" else None
    return assemble(ctx, copy)


def _why_text(primary: str | None) -> str:
    if primary is None:
        return "В профиле нет продукта или темы, которые связывают эту новость с деньгами компании."
    return WHY.get(primary, "Событие сопоставлено с признаком профиля этой компании.")


def _exposure_title(ctx: AgentContext, primary: str | None) -> str | None:
    profile = ctx.profile
    if primary is None or profile is None:
        return None
    for exposure in profile.exposures:
        if exposure.id == primary:
            return exposure.title
    return None


def _linked_text(primary: str | None, title: str | None, table: dict[str, str]) -> str | None:
    if primary is None:
        return None
    if primary in table:
        return table[primary]
    if title is None:
        return None
    return f"Вывод относится только к признаку «{title}». Величина влияния в профиле не указана."


def _step(primary: str | None, title: str | None) -> str:
    if primary is None:
        return "Не выбирать действие, пока в профиле не появится признак, связанный с этой новостью."
    written = STEP.get(primary)
    if written is not None:
        return written
    if title is None:
        return "Сопоставить новость с найденным признаком компании."
    return f"Сопоставить новость с признаком «{title}»."


def _question(primary: str | None, title: str | None) -> str:
    if primary is None:
        return "Какой признак компании связывает её с этим событием?"
    written = QUESTION.get(primary)
    if written is not None:
        return written
    if title is None:
        return "Как найденный признак отражается на операциях компании?"
    return f"Как признак «{title}» отражается на операциях компании?"


def _unknown(items: list[str]) -> list[str]:
    labeled: list[str] = []
    for item in items:
        label = FIELD_LABELS.get(item)
        labeled.append(f"Неизвестно: {label}" if label else item)
    return labeled


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
        if exposure.id not in ctx.matched:
            continue
        for line in exposure.assumptions:
            if exposure.basis == "hypothesis":
                prefix = "Допущение: "
            elif "неизвест" in line.lower() or "не указан" in line.lower():
                prefix = "Неизвестно: "
            else:
                prefix = "Ограничение данных: "
            lines.append(f"{prefix}{line}")
    return lines


def _sources(ctx: AgentContext, feature_refs: list[str], bank_ok: bool) -> list[Source]:
    profile = ctx.profile
    news = ctx.news
    assert profile is not None and news is not None
    sources = [
        Source(
            id=news.id,
            kind="news",
            title=news.title,
            url=news.url or None,
            published_at=news.published_at or None,
            quote=news.excerpt,
        ),
    ]
    if bank_ok and ctx.bank is not None:
        sources.append(
            Source(
                id=ctx.bank.doc_id,
                kind="bank_analytics",
                title=ctx.bank.title,
                origin="bank_analytics",
                document=ctx.bank.doc_id,
                published_at=ctx.bank.published_at,
                page=ctx.bank.page,
                quote=ctx.bank.quote,
            )
        )
    by_id = {feature.id: feature for feature in profile.features}
    for feature_id in feature_refs:
        feature = by_id.get(feature_id)
        sources.append(
            Source(
                id=feature.id,
                kind="company_feature",
                title=feature.label,
                origin=feature.origin,
                quote=feature.value,
            )
        )
    return sources


def _profile_facts(profile, feature_refs: list[str]) -> list[ProfileFact]:
    by_id = {item.id: item for item in profile.features}
    facts: list[ProfileFact] = []
    seen: set[str] = set()
    for feature_id in feature_refs:
        if feature_id in seen:
            continue
        seen.add(feature_id)
        feature = by_id[feature_id]
        facts.append(ProfileFact(feature_id=feature.id, label=feature.label, value=feature.value))
    return facts
