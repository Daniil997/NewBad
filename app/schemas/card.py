from typing import Literal

from pydantic import BaseModel, Field, model_validator

Layer = Literal["confirmed_fact", "bank_position", "model_inference"]
SourceKind = Literal["news", "bank_analytics", "company_feature"]


class Claim(BaseModel):
    id: str
    text: str
    layer: Layer
    source_refs: list[str] = Field(default_factory=list)
    feature_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class WhatHappened(BaseModel):
    claims: list[Claim]
    assumptions: list[str] = Field(default_factory=list)


class WhyForCompany(BaseModel):
    claims: list[Claim]


class Consequences(BaseModel):
    risks: list[Claim] = Field(default_factory=list)
    opportunities: list[Claim] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class ProductHint(BaseModel):
    text: str
    is_ad: Literal[True]


class Action(BaseModel):
    next_step: str
    questions: list[str] = Field(default_factory=list)
    product_hint: ProductHint | None = None


class BankView(BaseModel):
    status: Literal["found", "none"]
    quote: str = ""
    summary: str = ""
    doc_ref: str | None = None
    page: int | None = None

    @model_validator(mode="after")
    def quote_limit(self) -> "BankView":
        if self.status == "none" and self.quote:
            raise ValueError("Пустая позиция банка не содержит цитату")
        if len(self.quote.split()) > 25:
            raise ValueError("Цитата банка длиннее 25 слов")
        return self


class Source(BaseModel):
    id: str
    kind: SourceKind
    title: str
    origin: str | None = None
    url: str | None = None


class LlmTrace(BaseModel):
    model: str
    provider: str
    mode: str
    tokens: int = 0
    retries: int = 0


class Card(BaseModel):
    schema_version: str = "1"
    card_id: str
    client_id: str
    event_id: str
    generated_at: str
    llm_trace: LlmTrace
    what_happened: WhatHappened
    why_for_company: WhyForCompany
    consequences: Consequences
    action: Action
    bank_view: BankView
    sources: list[Source]
    features_triggered: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_resolve(self) -> "Card":
        by_id = {item.id: item for item in self.sources}
        claims = self.all_claims()
        for claim in claims:
            if claim.layer == "confirmed_fact" and not claim.source_refs:
                raise ValueError(f"Факт {claim.id} без источника")
            if claim.layer == "bank_position" and self.bank_view.status != "found":
                raise ValueError(f"Тезис {claim.id} ссылается на банк, хотя материал не найден")
            for ref in claim.source_refs:
                if ref not in by_id:
                    raise ValueError(f"Источник {ref} не описан")
            for ref in claim.feature_refs:
                source = by_id.get(ref)
                if source is None or source.kind != "company_feature":
                    raise ValueError(f"Признак {ref} не описан")
        for claim in self.why_for_company.claims:
            if not claim.feature_refs:
                raise ValueError(f"Тезис {claim.id} не привязан к компании")
        return self

    def all_claims(self) -> list[Claim]:
        return [
            *self.what_happened.claims,
            *self.why_for_company.claims,
            *self.consequences.risks,
            *self.consequences.opportunities,
        ]
