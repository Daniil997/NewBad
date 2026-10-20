from dataclasses import dataclass, field

from app.corpus.search import BankDocument, NewsItem
from app.llm.router import PlanChoice
from app.profile.build import ClientProfile, Counterparty


@dataclass
class AgentContext:
    client_id: str
    event_id: str
    profile: ClientProfile | None = None
    news: NewsItem | None = None
    bank: BankDocument | None = None
    counterparties: list[Counterparty] = field(default_factory=list)
    planned_tools: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    plan: PlanChoice | None = None
    retries: int = 0
