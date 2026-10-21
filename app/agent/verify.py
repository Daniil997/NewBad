import re

from app.agent.loop_context import AgentContext
from app.corpus.search import BankDocument

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_UP = ("рост", "повыш", "выше", "ускор", "увелич")
_DOWN = ("сниж", "ниже", "замедл", "паден", "сокращ", "дефляц")
_INFERENCE_CAP = 0.5
_BARE_CAP = 0.3


def verify_card(card, ctx: AgentContext) -> list[str]:
    issues: list[str] = []
    issues.extend(_source_ids(card, ctx))
    issues.extend(_quote_and_page(card, ctx))
    issues.extend(_profile_facts(card, ctx))
    issues.extend(_layers(card, ctx))
    if _sources_conflict(card):
        issues.append("conflict")
    issues.extend(_unsupported(card, ctx))
    _lower_confidence(card, ctx)
    known = _numbers(_corpus(ctx))
    for number in _numbers(_card_text(card)):
        if number not in known:
            issues.append(f"number:{number}")
    return issues


def _source_ids(card, ctx: AgentContext) -> list[str]:
    issues = []
    for source in card.sources:
        if not _known_source(source.id, source.kind, ctx):
            issues.append(f"source:{source.id}")
    for claim in card.all_claims():
        for ref in claim.source_refs:
            kind = _kind_of(card, ref)
            if not _known_source(ref, kind, ctx):
                issues.append(f"source:{ref}")
    if card.bank_view.status == "found" and card.bank_view.doc_ref:
        if _page_document(ctx, card.bank_view.doc_ref, None) is None:
            issues.append(f"source:{card.bank_view.doc_ref}")
    return _unique(issues)


def _quote_and_page(card, ctx: AgentContext) -> list[str]:
    if card.bank_view.status != "found":
        return []
    same_page = _page_document(ctx, card.bank_view.doc_ref, card.bank_view.page)
    if same_page is None:
        return ["page"] if _page_document(ctx, card.bank_view.doc_ref, None) is not None else ["quote"]
    if card.bank_view.quote not in same_page.text:
        return ["quote"]
    issues = []
    for claim in card.all_claims():
        if claim.layer == "bank_position" and claim.text not in same_page.text:
            issues.append("quote")
    return _unique(issues)


def _profile_facts(card, ctx: AgentContext) -> list[str]:
    profile = ctx.profile
    if profile is None:
        return [f"profile:{card.client_id}"]
    features = {item.id: item for item in profile.features}
    exposures = {item.id: item for item in profile.exposures}
    issues = []
    for exposure_id in card.features_triggered:
        if exposure_id not in exposures:
            issues.append(f"profile:{exposure_id}")
    for claim in card.all_claims():
        for ref in claim.feature_refs:
            feature = features.get(ref)
            if feature is None:
                issues.append(f"profile:{ref}")
                continue
            if claim.layer == "confirmed_fact" and not _text_agrees(claim.text, feature.value):
                issues.append(f"profile:{ref}")
    return _unique(issues)


def _layers(card, ctx: AgentContext) -> list[str]:
    issues = []
    for claim in card.what_happened.claims:
        if claim.layer == "model_inference":
            issues.append(f"layer:{claim.id}")
        elif claim.layer == "confirmed_fact" and not _fact_in_sources(claim, card, ctx):
            issues.append(f"evidence:{claim.id}")
        elif claim.layer == "bank_position" and not _in_bank(claim.text, card, ctx):
            if _page_document(ctx, card.bank_view.doc_ref, card.bank_view.page) is None:
                issues.append("page")
            else:
                issues.append("quote")
    for claim in card.why_for_company.claims:
        if claim.layer != "model_inference":
            issues.append(f"layer:{claim.id}")
    for claim in (*card.consequences.risks, *card.consequences.opportunities):
        if claim.layer != "model_inference":
            issues.append(f"layer:{claim.id}")
    return _unique(issues)


def _unsupported(card, ctx: AgentContext) -> list[str]:
    issues = []
    for claim in card.all_claims():
        if claim.layer != "model_inference":
            continue
        if claim.source_refs or claim.feature_refs:
            continue
        claim.confidence = min(claim.confidence, _BARE_CAP)
        issues.append(f"evidence:{claim.id}")
    if ctx.news is None:
        issues.append("evidence:news")
    return issues


def _lower_confidence(card, ctx: AgentContext) -> None:
    if ctx.profile is None:
        return
    hypothesis = {item.id for item in ctx.profile.exposures if item.basis == "hypothesis"}
    if not hypothesis.intersection(card.features_triggered):
        return
    for claim in card.all_claims():
        if claim.layer == "model_inference" and claim.confidence > _INFERENCE_CAP:
            claim.confidence = _INFERENCE_CAP


def _sources_conflict(card) -> bool:
    fact = " ".join(claim.text for claim in card.what_happened.claims if claim.layer == "confirmed_fact")
    bank = card.bank_view.quote
    fact_lean = _lean(fact)
    bank_lean = _lean(bank)
    if fact_lean and bank_lean and fact_lean != bank_lean:
        return True
    return _lean(fact) == "both" or _lean(bank) == "both"


def _fact_in_sources(claim, card, ctx: AgentContext) -> bool:
    parts = []
    for ref in claim.source_refs:
        kind = _kind_of(card, ref)
        if kind == "news" and ctx.news is not None and ref in {ctx.news.id, *(item.id for item in ctx.news_items)}:
            news = ctx.news if ctx.news.id == ref else next(item for item in ctx.news_items if item.id == ref)
            parts.append(f"{news.title}. {news.excerpt}")
        elif kind == "company_feature" and ctx.profile is not None:
            for feature in ctx.profile.features:
                if feature.id == ref:
                    parts.append(feature.value)
        elif kind == "bank_analytics":
            document = _page_document(ctx, ref, card.bank_view.page)
            if document is not None:
                parts.append(document.text)
    if not parts and ctx.news is not None:
        parts.append(f"{ctx.news.title}. {ctx.news.excerpt}")
    return any(_contains(claim.text, part) for part in parts)


def _contains(text: str, source: str) -> bool:
    if text in source:
        return True
    folded = _plain(text)
    return bool(folded) and folded in _plain(source)


def _plain(text: str) -> str:
    return re.sub(r"[^\w%]+", " ", text.lower().replace("ё", "е")).strip()


def _in_bank(text: str, card, ctx: AgentContext) -> bool:
    document = _page_document(ctx, card.bank_view.doc_ref, card.bank_view.page)
    return document is not None and text in document.text


def _known_source(source_id: str, kind: str | None, ctx: AgentContext) -> bool:
    if kind == "company_feature":
        return ctx.profile is not None and any(item.id == source_id for item in ctx.profile.features)
    if kind == "bank_analytics":
        return _page_document(ctx, source_id, None) is not None
    if kind == "news":
        ids = {item.id for item in ctx.news_items}
        if ctx.news is not None:
            ids.add(ctx.news.id)
        return source_id in ids
    return False


def _page_document(ctx: AgentContext, doc_id: str | None, page: int | None) -> BankDocument | None:
    if not doc_id:
        return None
    found = [ctx.bank, *ctx.documents]
    for document in found:
        if document is None or document.doc_id != doc_id:
            continue
        if page is None or document.page == page:
            return document
    return None


def _kind_of(card, source_id: str) -> str | None:
    for source in card.sources:
        if source.id == source_id:
            return source.kind
    return None


def _text_agrees(claim: str, value: str) -> bool:
    if not value:
        return False
    if value in claim or claim in value:
        return True
    return _numbers(claim) <= _numbers(value) and bool(_numbers(claim))


def _corpus(ctx: AgentContext) -> str:
    parts = []
    if ctx.news is not None:
        parts.extend([ctx.news.title, ctx.news.excerpt])
    for item in ctx.news_items:
        parts.extend([item.title, item.excerpt])
    if ctx.bank is not None:
        parts.extend([ctx.bank.text, ctx.bank.quote, *ctx.bank.theses])
    for document in ctx.documents:
        parts.extend([document.text, document.quote, *document.theses])
    if ctx.profile is not None:
        parts.extend(feature.value for feature in ctx.profile.features)
    return " ".join(parts)


def _card_text(card) -> str:
    parts = [claim.text for claim in card.all_claims()]
    parts.append(card.action.next_step)
    parts.extend(card.action.questions)
    parts.append(card.bank_view.summary)
    parts.append(card.bank_view.quote)
    return " ".join(parts)


def _lean(text: str) -> str | None:
    folded = text.lower().replace("ё", "е")
    up = any(stem in folded for stem in _UP)
    down = any(stem in folded for stem in _DOWN)
    if up and down:
        return "both"
    if up:
        return "up"
    if down:
        return "down"
    return None


def _numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", ".") for match in _NUMBER.finditer(text)}


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
