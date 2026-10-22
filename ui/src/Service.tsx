import { useEffect, useState } from "react";

type ClientSummary = {
  client_id: string;
  title: string;
  money_role: string;
  exposure_ids: string[];
};

type ClientProfile = {
  client_id: string;
  title: string;
  money_role: string;
  cohort_share: number | null;
  cohort_key: string | null;
  missing_data: string[];
  exposures: { id: string }[];
};

type FeedItem = {
  news_id: string;
  title: string;
  score: number;
  reason: string;
};

type Feed = {
  top: FeedItem[];
  less_relevant: FeedItem[];
};

type Claim = {
  id: string;
  text: string;
  layer: string;
};

type Source = {
  id: string;
  kind: string;
  title: string;
  origin: string | null;
  url: string | null;
};

type InsightCard = {
  event_id: string;
  what_happened: { claims: Claim[]; assumptions: string[] };
  why_for_company: { claims: Claim[] };
  consequences: { risks: Claim[]; opportunities: Claim[]; depends_on: string[] };
  action: { next_step: string; questions: string[] };
  bank_view: { status: string; quote: string; summary: string };
  sources: Source[];
  features_triggered: string[];
  missing_data: string[];
  assumptions: string[];
  llm_trace: { mode: string; retries: number; model: string; origin?: string };
};

const ROLE_LABEL: Record<string, string> = {
  borrower: "заёмщик",
  saver: "вкладчик",
  both: "и кредит, и депозит",
  neither: "без кредита и депозита",
};

const ORIGIN_LABEL: Record<string, string> = {
  model: "ответ модели",
  replay: "повтор из кэша",
  template: "шаблон",
};

const LAYER_LABEL: Record<string, string> = {
  confirmed_fact: "Факт",
  bank_position: "Позиция банка",
  model_inference: "Вывод ИИ",
};

export function Service({ onHome, onSettings }: { onHome: () => void; onSettings: () => void }) {
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [profile, setProfile] = useState<ClientProfile | null>(null);
  const [feed, setFeed] = useState<Feed | null>(null);
  const [eventId, setEventId] = useState("");
  const [card, setCard] = useState<InsightCard | null>(null);
  const [steps, setSteps] = useState<string[]>([]);
  const [traceOpen, setTraceOpen] = useState(false);
  const [messages, setMessages] = useState<{ role: "user" | "service"; text: string }[]>([]);
  const [question, setQuestion] = useState("");
  const [pasteTitle, setPasteTitle] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    void fetch("/api/clients")
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Список компаний: ${response.status}`);
        }
        return response.json() as Promise<ClientSummary[]>;
      })
      .then((items) => {
        setClients(items);
        const borrower = items.find((item) => item.money_role === "borrower");
        setSelected((borrower ?? items[0])?.client_id ?? "");
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Не удалось загрузить компании");
      });
  }, []);

  useEffect(() => {
    if (!selected) {
      return;
    }
    const controller = new AbortController();
    setMessages([]);
    void fetch(`/api/clients/${selected}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Профиль: ${response.status}`);
        }
        return response.json() as Promise<ClientProfile>;
      })
      .then(setProfile)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Не удалось загрузить профиль");
        }
      });
    void fetch(`/api/clients/${selected}/feed`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Лента: ${response.status}`);
        }
        return response.json() as Promise<Feed>;
      })
      .then((payload) => {
        setFeed(payload);
        setEventId(payload.top[0]?.news_id ?? "");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Не удалось загрузить ленту");
        }
      });
    return () => controller.abort();
  }, [selected]);

  useEffect(() => {
    if (!selected || !eventId) {
      return;
    }
    const controller = new AbortController();
    void fetch(`/api/clients/${selected}/events/${eventId}/card`, { method: "POST", signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Карточка: ${response.status}`);
        }
        return response.json() as Promise<InsightCard>;
      })
      .then(async (payload) => {
        setCard(payload);
        const stepsResponse = await fetch(`/api/clients/${selected}/events/${eventId}/steps`, {
          signal: controller.signal,
        });
        if (!stepsResponse.ok) {
          setSteps([]);
          return;
        }
        const trace = (await stepsResponse.json()) as { steps: string[] };
        setSteps(trace.steps);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Не удалось собрать карточку");
        }
      });
    return () => controller.abort();
  }, [selected, eventId]);

  async function ask(text: string) {
    const cleaned = text.trim();
    if (!cleaned || !selected || !eventId) {
      return;
    }
    setQuestion("");
    setMessages((current) => [...current, { role: "user", text: cleaned }]);
    const response = await fetch(`/api/clients/${selected}/events/${eventId}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: cleaned }),
    });
    if (!response.ok) {
      setMessages((current) => [...current, { role: "service", text: `Не удалось ответить: ${response.status}` }]);
      return;
    }
    const payload = (await response.json()) as { answer: string };
    setMessages((current) => [...current, { role: "service", text: payload.answer }]);
  }

  async function pasteNews() {
    if (!selected) {
      return;
    }
    const response = await fetch(`/api/clients/${selected}/news`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: pasteTitle, text: pasteText }),
    });
    if (!response.ok) {
      setError(`Своя новость: ${response.status}`);
      return;
    }
    const payload = (await response.json()) as InsightCard;
    setCard(payload);
    setEventId(payload.event_id);
    setPasteTitle("");
    setPasteText("");
  }

  const newsSource = card?.sources.find((item) => item.kind === "news");

  return (
    <div className="bank">
      <header className="bank-bar">
        <button className="bar-button" type="button" onClick={onHome}>
          Альфа-Бизнес
        </button>
        <button className="bar-button" type="button" onClick={onSettings}>
          Настройки
        </button>
      </header>
      <main className="bank-main">
        <h1>Что важно для вашей компании</h1>
        {error ? <p className="error">{error}</p> : null}
        <div className="switcher">
          {clients.map((client) => (
            <button
              key={client.client_id}
              type="button"
              className={client.client_id === selected ? "active" : ""}
              onClick={() => setSelected(client.client_id)}
            >
              {ROLE_LABEL[client.money_role] ?? client.money_role}
              <span>{client.client_id}</span>
            </button>
          ))}
        </div>
        <div className="workspace">
          <section className="card">
            {profile ? (
              <dl>
                <div>
                  <dt>Компания</dt>
                  <dd>{profile.title}</dd>
                </div>
                <div>
                  <dt>Роль</dt>
                  <dd>{ROLE_LABEL[profile.money_role] ?? profile.money_role}</dd>
                </div>
                <div>
                  <dt>Масштаб</dt>
                  <dd>
                    {profile.cohort_share === null
                      ? "нет выручки"
                      : `${Math.round(profile.cohort_share * 100)}% когорты ${profile.cohort_key}`}
                  </dd>
                </div>
                <div>
                  <dt>Не хватает</dt>
                  <dd>{profile.missing_data.join(", ") || "пропусков нет"}</dd>
                </div>
              </dl>
            ) : null}
            <h2>Важно для компании</h2>
            <FeedItems items={feed?.top ?? []} eventId={eventId} onSelect={setEventId} />
            <h2>Менее значимо для вас</h2>
            <FeedItems items={feed?.less_relevant ?? []} eventId={eventId} onSelect={setEventId} />
          </section>
          <section className="card insight">
            {card ? (
              <>
                <CardBody card={card} />
                <p className="meta">
                  {ORIGIN_LABEL[card.llm_trace.origin ?? "template"] ?? "шаблон"}, режим {card.llm_trace.mode},
                  повторов проверки {card.llm_trace.retries}
                </p>
                {newsSource?.url ? (
                  <a className="source-link" href={newsSource.url} target="_blank" rel="noreferrer">
                    Открыть источник
                  </a>
                ) : (
                  <p>У новости нет ссылки.</p>
                )}
                <button className="linkish" type="button" onClick={() => setTraceOpen((open) => !open)}>
                  Как получен вывод
                </button>
                {traceOpen ? <Trace card={card} steps={steps} /> : null}
                <Chat
                  questions={card.action.questions}
                  messages={messages}
                  question={question}
                  onQuestion={setQuestion}
                  onAsk={ask}
                />
              </>
            ) : (
              <p>Собираем карточку…</p>
            )}
          </section>
        </div>
        <section className="card paste">
          <h2>Вставить свою новость</h2>
          <input
            value={pasteTitle}
            onChange={(event) => setPasteTitle(event.target.value)}
            placeholder="Заголовок"
            aria-label="Заголовок своей новости"
          />
          <textarea
            value={pasteText}
            onChange={(event) => setPasteText(event.target.value)}
            placeholder="Текст новости"
            aria-label="Текст своей новости"
            rows={4}
          />
          <button type="button" onClick={() => void pasteNews()}>
            Разобрать
          </button>
        </section>
      </main>
    </div>
  );
}

function FeedItems({
  items,
  eventId,
  onSelect,
}: {
  items: FeedItem[];
  eventId: string;
  onSelect: (newsId: string) => void;
}) {
  if (items.length === 0) {
    return <p>Пусто.</p>;
  }
  return (
    <ul className="hits">
      {items.map((item) => (
        <li key={item.news_id}>
          <button
            type="button"
            className={item.news_id === eventId ? "event active" : "event"}
            onClick={() => onSelect(item.news_id)}
          >
            <strong>{item.title}</strong>
            <span>{item.score.toFixed(2)}</span>
            <p>{item.reason}</p>
          </button>
        </li>
      ))}
    </ul>
  );
}

function CardBody({ card }: { card: InsightCard }) {
  return (
    <>
      <h2>Что произошло</h2>
      <Claims claims={card.what_happened.claims} />
      {card.what_happened.assumptions.length > 0 ? (
        <p className="note">Предположение: {card.what_happened.assumptions.join(" ")}</p>
      ) : null}
      <h2>Почему это важно этой компании</h2>
      <Claims claims={card.why_for_company.claims} />
      <h2>Какие последствия</h2>
      <Claims claims={[...card.consequences.risks, ...card.consequences.opportunities]} />
      {card.consequences.depends_on.length > 0 ? <p className="note">Зависит от: {card.consequences.depends_on.join(" ")}</p> : null}
      <h2>Какое действие уместно</h2>
      <p>
        <Badge layer="model_inference" /> {card.action.next_step}
      </p>
      <h2>Как это оценивает банк</h2>
      {card.bank_view.status === "found" ? (
        <blockquote>
          <Badge layer="bank_position" /> {card.bank_view.quote}
        </blockquote>
      ) : (
        <p>Материала банка нет.</p>
      )}
      <h2>На чём основан вывод</h2>
      <ul className="sources">
        {card.sources.map((source) => (
          <li key={source.id}>
            {source.title}
            {source.origin ? <span>{source.origin}</span> : null}
          </li>
        ))}
      </ul>
    </>
  );
}

function Claims({ claims }: { claims: Claim[] }) {
  if (claims.length === 0) {
    return <p>Нет тезисов.</p>;
  }
  return (
    <>
      {claims.map((claim) => (
        <p key={claim.id}>
          <Badge layer={claim.layer} /> {claim.text}
        </p>
      ))}
    </>
  );
}

function Badge({ layer }: { layer: string }) {
  return <span className={`badge badge-${layer}`}>{LAYER_LABEL[layer] ?? layer}</span>;
}

function Trace({ card, steps }: { card: InsightCard; steps: string[] }) {
  const features = card.sources.filter((item) => item.kind === "company_feature");
  return (
    <div className="trace">
      <p>Признаки: {card.features_triggered.join(", ") || "нет совпадения"}</p>
      <ul>
        {features.map((feature) => (
          <li key={feature.id}>
            {feature.title} · {feature.origin ?? "без происхождения"}
          </li>
        ))}
      </ul>
      <p>Шаги: {steps.length > 0 ? steps.join(" → ") : "журнал шагов ещё пуст"}</p>
      <p>Повторов самопроверки: {card.llm_trace.retries}</p>
      {card.missing_data.length > 0 ? <p>Не хватило: {card.missing_data.join(", ")}</p> : null}
    </div>
  );
}

function Chat({
  questions,
  messages,
  question,
  onQuestion,
  onAsk,
}: {
  questions: string[];
  messages: { role: "user" | "service"; text: string }[];
  question: string;
  onQuestion: (value: string) => void;
  onAsk: (text: string) => Promise<void>;
}) {
  return (
    <div className="chat">
      <h2>Уточнить</h2>
      <div className="quick">
        {questions.map((item) => (
          <button key={item} type="button" onClick={() => void onAsk(item)}>
            {item}
          </button>
        ))}
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void onAsk(question);
        }}
      >
        <input
          value={question}
          onChange={(event) => onQuestion(event.target.value)}
          placeholder="Свой вопрос"
          aria-label="Вопрос по карточке"
        />
        <button type="submit">Спросить</button>
      </form>
      <ul className="messages">
        {messages.map((item, index) => (
          <li key={`${item.role}-${index}`} className={item.role}>
            {item.text}
          </li>
        ))}
      </ul>
    </div>
  );
}
