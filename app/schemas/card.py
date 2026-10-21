from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Layer = Literal["confirmed_fact", "bank_position", "model_inference"]
SourceKind = Literal["news", "bank_analytics", "company_feature"]
_CONFIG = ConfigDict(extra="forbid")


class Claim(BaseModel):
    model_config = _CONFIG

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    layer: Layer
    source_refs: list[str] = Field(default_factory=list)
    feature_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class WhatHappened(BaseModel):
    model_config = _CONFIG

    claims: list[Claim] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)


class WhyForCompany(BaseModel):
    model_config = _CONFIG

    claims: list[Claim] = Field(min_length=1)


class Consequences(BaseModel):
    model_config = _CONFIG

    risks: list[Claim] = Field(default_factory=list)
    opportunities: list[Claim] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class ProductHint(BaseModel):
    model_config = _CONFIG

    text: str = Field(min_length=1)
    is_ad: Literal[True]


class Action(BaseModel):
    model_config = _CONFIG

    next_step: str = Field(min_length=1)
    questions: list[str] = Field(min_length=1)
    product_hint: ProductHint | None = None


class BankView(BaseModel):
    model_config = _CONFIG

    status: Literal["found", "none"]
    quote: str = ""
    summary: str = ""
    doc_ref: str | None = None
    page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def quote_limit(self) -> "BankView":
        if self.status == "none" and (self.quote or self.doc_ref or self.page is not None):
            raise ValueError("Пустая позиция банка не содержит цитату")
        if self.status == "found" and (not self.quote or not self.doc_ref or self.page is None):
            raise ValueError("Позиция банка без цитаты, документа или страницы")
        if len(self.quote.split()) > 25:
            raise ValueError("Цитата банка длиннее 25 слов")
        return self


class Source(BaseModel):
    model_config = _CONFIG

    id: str = Field(min_length=1)
    kind: SourceKind
    title: str = Field(min_length=1)
    origin: str | None = None
    url: str | None = None
    document: str | None = None
    published_at: str | None = None
    page: int | None = Field(default=None, ge=1)
    quote: str | None = None


class ProfileFact(BaseModel):
    model_config = _CONFIG

    feature_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value: str


class Verification(BaseModel):
    model_config = _CONFIG

    status: Literal["passed", "failed"]
    issues: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def status_matches_issues(self) -> "Verification":
        if self.status == "passed" and self.issues:
            raise ValueError("Пройденная проверка не содержит замечаний")
        if self.status == "failed" and not self.issues:
            raise ValueError("Непройденная проверка без замечаний")
        return self


class LlmTrace(BaseModel):
    model_config = _CONFIG

    model: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    tokens: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)


class Card(BaseModel):
    model_config = _CONFIG

    schema_version: Literal["1"] = "1"
    card_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)
    llm_trace: LlmTrace
    what_happened: WhatHappened
    bank_position: list[Claim] = Field(default_factory=list)
    why_for_company: WhyForCompany
    consequences: Consequences
    action: Action
    bank_view: BankView
    sources: list[Source] = Field(min_length=1)
    profile_facts: list[ProfileFact] = Field(default_factory=list)
    features_triggered: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    verification: Verification = Field(default_factory=lambda: Verification(status="passed"))
    follow_up: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def references_resolve(self) -> "Card":
        self._layers()
        self._sources()
        self._profile_facts()
        if self.follow_up != self.action.questions:
            raise ValueError("Follow-up не совпадает с вопросами карточки")
        self.confidence = min(claim.confidence for claim in self.all_claims())
        return self

    def all_claims(self) -> list[Claim]:
        return [
            *self.what_happened.claims,
            *self.bank_position,
            *self.why_for_company.claims,
            *self.consequences.risks,
            *self.consequences.opportunities,
        ]

    def sync_confidence(self) -> None:
        self.confidence = min(claim.confidence for claim in self.all_claims())

    def _layers(self) -> None:
        for claim in self.what_happened.claims:
            if claim.layer != "confirmed_fact":
                raise ValueError(f"В фактах лежит {claim.layer}")
            if not claim.source_refs:
                raise ValueError(f"Факт {claim.id} без источника")
        for claim in self.bank_position:
            if claim.layer != "bank_position":
                raise ValueError(f"В позиции банка лежит {claim.layer}")
        if self.bank_view.status == "found":
            if len(self.bank_position) != 1 or self.bank_position[0].text != self.bank_view.quote:
                raise ValueError("Позиция банка не совпадает с цитатой")
        elif self.bank_position:
            raise ValueError("Позиция банка есть, хотя материал не найден")
        for claim in (*self.why_for_company.claims, *self.consequences.risks, *self.consequences.opportunities):
            if claim.layer != "model_inference":
                raise ValueError(f"В выводе лежит {claim.layer}")
            if not claim.feature_refs:
                raise ValueError(f"Тезис {claim.id} не привязан к компании")

    def _sources(self) -> None:
        if len({item.id for item in self.sources}) != len(self.sources):
            raise ValueError("Повторяющийся id источника")
        by_id = {item.id: item for item in self.sources}
        for claim in self.all_claims():
            for ref in claim.source_refs:
                if ref not in by_id:
                    raise ValueError(f"Источник {ref} не описан")
            for ref in claim.feature_refs:
                source = by_id.get(ref)
                if source is None or source.kind != "company_feature":
                    raise ValueError(f"Признак {ref} не описан")
        for source in self.sources:
            if source.kind == "bank_analytics":
                if source.document != source.id or source.page is None or not source.quote or not source.published_at:
                    raise ValueError(f"Источник банка {source.id} без документа, даты, страницы или цитаты")
                if source.quote != self.bank_view.quote or source.page != self.bank_view.page:
                    raise ValueError(f"Источник банка {source.id} не совпадает с позицией банка")
            if source.kind == "news" and source.quote is None:
                raise ValueError(f"Источник новости {source.id} без цитаты")

    def _profile_facts(self) -> None:
        cited = [ref for claim in self.all_claims() for ref in claim.feature_refs]
        unique = list(dict.fromkeys(cited))
        found = [item.feature_id for item in self.profile_facts]
        if found != unique:
            raise ValueError("Факты профиля не совпадают с использованными признаками")
