import re

from app.agent.loop_context import AgentContext

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def verify_card(card, ctx: AgentContext) -> list[str]:
    issues: list[str] = []
    if card.bank_view.status == "found":
        if ctx.bank is None or card.bank_view.quote not in ctx.bank.text:
            issues.append("quote")
    known = _numbers(_corpus(ctx))
    for number in _numbers(_card_text(card)):
        if number not in known:
            issues.append(f"number:{number}")
    return issues


def _corpus(ctx: AgentContext) -> str:
    parts = [ctx.news.title, ctx.news.excerpt]
    if ctx.bank is not None:
        parts.extend([ctx.bank.text, ctx.bank.quote, *ctx.bank.theses])
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


def _numbers(text: str) -> set[str]:
    return {match.group(0).replace(",", ".") for match in _NUMBER.finditer(text)}
