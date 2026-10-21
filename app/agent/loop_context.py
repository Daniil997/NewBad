from dataclasses import dataclass, field

from app.corpus.search import BankDocument, NewsItem
from app.llm.router import PlanChoice, max_retries
from app.profile.build import ClientProfile, Counterparty

MAX_FEATURES = 24
MAX_EXPOSURES = 12
MAX_COUNTERPARTIES = 8
MAX_NEWS = 4
MAX_DOCUMENTS = 3
MAX_FRAGMENTS = 6
MAX_EXCERPT_CHARS = 600
MAX_PAGE_CHARS = 1200
MAX_FRAGMENT_CHARS = 400
MAX_REPORTS = 8
MAX_REASONS = 4
MAX_ISSUES = 8
MAX_MESSAGE_CHARS = 240
MAX_CONTEXT_CHARS = 12000


@dataclass
class EvidenceFragment:
    kind: str
    source_id: str
    title: str
    page: int | None
    text: str


@dataclass
class ToolError:
    tool: str
    message: str


@dataclass
class AgentContext:
    client_id: str
    event_id: str
    profile: ClientProfile | None = None
    news: NewsItem | None = None
    bank: BankDocument | None = None
    bank_note: str | None = None
    counterparties: list[Counterparty] = field(default_factory=list)
    planned_tools: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    plan: PlanChoice | None = None
    retries: int = 0
    issues: list[str] = field(default_factory=list)
    extracted_fact: str | None = None
    calls: set[str] = field(default_factory=set)
    tool_reports: list[dict] = field(default_factory=list)
    tool_errors: list[ToolError] = field(default_factory=list)
    search_reasons: list[str] = field(default_factory=list)
    news_items: list[NewsItem] = field(default_factory=list)
    documents: list[BankDocument] = field(default_factory=list)
    fragments: list[EvidenceFragment] = field(default_factory=list)
    finish_reason: str = ""

    @property
    def attempt(self) -> int:
        return self.retries

    def can_retry(self) -> bool:
        return self.retries < max_retries()

    def mark_attempt(self) -> bool:
        if not self.can_retry():
            return False
        self.retries += 1
        return True

    def remember_profile(self, profile: ClientProfile) -> None:
        features = [
            feature.model_copy(update={"value": _clip(feature.value, 120)})
            for feature in profile.features[:MAX_FEATURES]
        ]
        self.profile = profile.model_copy(
            update={
                "features": features,
                "exposures": list(profile.exposures)[:MAX_EXPOSURES],
                "counterparties": list(profile.counterparties)[:MAX_COUNTERPARTIES],
                "missing_data": [_clip(item, 180) for item in profile.missing_data][:MAX_ISSUES],
            }
        )

    def remember_counterparties(self, items: list[Counterparty]) -> None:
        self.counterparties = list(items)[:MAX_COUNTERPARTIES]

    def remember_news(self, item: NewsItem, *, primary: bool) -> None:
        fitted = item.model_copy(update={"excerpt": _clip(item.excerpt, MAX_EXCERPT_CHARS)})
        if primary:
            self.news = fitted
        if any(stored.id == fitted.id for stored in self.news_items):
            return
        if len(self.news_items) >= MAX_NEWS or not self._room_for(fitted.excerpt):
            return
        self.news_items.append(fitted)
        self._add_fragment(
            EvidenceFragment(kind="news", source_id=fitted.id, title=fitted.title, page=None, text=fitted.excerpt)
        )

    def remember_bank(self, document: BankDocument, note: str | None, *, primary: bool) -> None:
        fitted = _fit_bank(document)
        if primary:
            self.bank = fitted
            self.bank_note = _clip(note or "", MAX_MESSAGE_CHARS) or None
        key = (fitted.doc_id, fitted.page)
        if any((item.doc_id, item.page) == key for item in self.documents):
            return
        if len(self.documents) >= MAX_DOCUMENTS:
            if primary:
                self.documents[-1] = fitted
            return
        if not self._room_for(fitted.text):
            return
        self.documents.append(fitted)
        self._add_fragment(
            EvidenceFragment(
                kind="bank",
                source_id=fitted.doc_id,
                title=fitted.title,
                page=fitted.page,
                text=_clip(fitted.quote, MAX_FRAGMENT_CHARS),
            )
        )

    def drop_bank(self, note: str) -> None:
        self.bank = None
        self.bank_note = _clip(note, MAX_MESSAGE_CHARS) or None

    def record_tool(self, tool: str, status: str, message: str) -> None:
        message = _clip(message, MAX_MESSAGE_CHARS)
        if len(self.tool_reports) < MAX_REPORTS:
            self.tool_reports.append({"tool": tool, "status": status, "message": message})
        if status == "error" and len(self.tool_errors) < MAX_REPORTS:
            self.tool_errors.append(ToolError(tool=tool, message=message))

    def record_search_reason(self, reason: str) -> None:
        if len(self.search_reasons) >= MAX_REASONS or not self._room_for(reason):
            return
        self.search_reasons.append(_clip(reason, MAX_MESSAGE_CHARS))

    def record_issues(self, issues: list[str]) -> None:
        self.issues = [_clip(issue, 120) for issue in issues][:MAX_ISSUES]

    def context_chars(self) -> int:
        chunks = [item.excerpt for item in self.news_items]
        chunks.extend(item.text for item in self.documents)
        chunks.extend(item.message for item in self.tool_errors)
        chunks.extend(self.search_reasons)
        chunks.extend(self.issues)
        if self.profile is not None:
            chunks.extend(feature.value for feature in self.profile.features)
        if self.bank_note:
            chunks.append(self.bank_note)
        return sum(len(chunk) for chunk in chunks)

    def _add_fragment(self, fragment: EvidenceFragment) -> None:
        if len(self.fragments) >= MAX_FRAGMENTS:
            return
        self.fragments.append(fragment)

    def _room_for(self, text: str) -> bool:
        return self.context_chars() + len(text) <= MAX_CONTEXT_CHARS


def _fit_bank(document: BankDocument) -> BankDocument:
    return document.model_copy(
        update={
            "text": _window(document.text, document.quote, MAX_PAGE_CHARS),
            "theses": [_clip(item, 300) for item in document.theses[:6]],
        }
    )


def _window(text: str, quote: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    at = text.find(quote)
    if at < 0:
        return quote
    start = max(0, at - (limit - len(quote)) // 2)
    chunk = text[start : start + limit]
    if quote not in chunk:
        return quote
    return chunk


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]
