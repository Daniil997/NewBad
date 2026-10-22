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
  exposures: { id: string; title: string }[];
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
  page: number | null;
  quote: string | null;
};

type InsightCard = {
  event_id: string;
  confidence: number;
  what_happened: { claims: Claim[]; assumptions: string[] };
  why_for_company: { claims: Claim[] };
  consequences: { risks: Claim[]; opportunities: Claim[]; depends_on: string[] };
  action: { next_step: string; questions: string[] };
  bank_view: { status: string; quote: string; summary: string; page: number | null };
  sources: Source[];
  features_triggered: string[];
  missing_data: string[];
  assumptions: string[];
  llm_trace: { mode: string; retries: number; model: string; origin?: string };
};

type Phase = "loading" | "ready" | "empty" | "error";

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
  const [clientsPhase, setClientsPhase] = useState<Phase>("loading");
  const [selected, setSelected] = useState("");
  const [profile, setProfile] = useState<ClientProfile | null>(null);
  const [feed, setFeed] = useState<Feed | null>(null);
  const [feedPhase, setFeedPhase] = useState<Phase>("loading");
  const [eventId, setEventId] = useState("");
  const [card, setCard] = useState<InsightCard | null>(null);
  const [cardPhase, setCardPhase] = useState<Phase>("loading");
  const [steps, setSteps] = useState<string[]>([]);
  const [traceOpen, setTraceOpen] = useState(false);
  const [messages, setMessages] = useState<{ role: "user" | "service"; text: string }[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [failedQuestion, setFailedQuestion] = useState("");
  const [pasteTitle, setPasteTitle] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [pasteBusy, setPasteBusy] = useState(false);
  const [pasteError, setPasteError] = useState("");
  const [listError, setListError] = useState("");
  const [profileError, setProfileError] = useState("");
  const [feedError, setFeedError] = useState("");
  const [cardError, setCardError] = useState("");
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setClientsPhase("loading");
    setListError("");
    void fetch("/api/clients", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Список компаний: ${response.status}`);
        }
        return response.json() as Promise<ClientSummary[]>;
      })
      .then((items) => {
        setClients(items);
        setClientsPhase(items.length === 0 ? "empty" : "ready");
        const borrower = items.find((item) => item.money_role === "borrower");
        setSelected((current) => current || (borrower ?? items[0])?.client_id || "");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setClientsPhase("error");
          setFeedPhase("empty");
          setCardPhase("empty");
          setListError(reason instanceof Error ? reason.message : "Не удалось загрузить компании");
        }
      });
    return () => controller.abort();
  }, [attempt]);

  useEffect(() => {
    if (!selected) {
      return;
    }
    const controller = new AbortController();
    setMessages([]);
    setFailedQuestion("");
    setProfile(null);
    setFeed(null);
    setFeedPhase("loading");
    setFeedError("");
    setProfileError("");
    setCard(null);
    setCardPhase("loading");
    setEventId("");
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
          setProfileError(reason instanceof Error ? reason.message : "Не удалось загрузить профиль");
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
        const next = payload.top[0]?.news_id ?? "";
        setEventId(next);
        setFeedPhase(payload.top.length + payload.less_relevant.length === 0 ? "empty" : "ready");
        if (!next) {
          setCardPhase("empty");
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setFeedPhase("error");
          setFeedError(reason instanceof Error ? reason.message : "Не удалось загрузить ленту");
        }
      });
    return () => controller.abort();
  }, [selected, attempt]);

  useEffect(() => {
    if (!selected || !eventId) {
      return;
    }
    const controller = new AbortController();
    setMessages([]);
    setFailedQuestion("");
    setCard(null);
    setCardPhase("loading");
    setCardError("");
    setFeedbackSent(false);
    void fetch(`/api/clients/${selected}/events/${eventId}/card`, { method: "POST", signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Карточка: ${response.status}`);
        }
        return response.json() as Promise<InsightCard>;
      })
      .then(async (payload) => {
        setCard(payload);
        setCardPhase("ready");
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
          setCardPhase("error");
          setCardError(reason instanceof Error ? reason.message : "Не удалось собрать карточку");
        }
      });
    return () => controller.abort();
  }, [selected, eventId, attempt]);

  async function ask(text: string) {
    const cleaned = text.trim();
    if (!cleaned || !selected || !eventId || asking) {
      return;
    }
    setQuestion("");
    setFailedQuestion("");
    setAsking(true);
    setMessages((current) => [...current, { role: "user", text: cleaned }]);
    try {
      const response = await fetch(`/api/clients/${selected}/events/${eventId}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: cleaned }),
      });
      if (!response.ok) {
        setFailedQuestion(cleaned);
        setMessages((current) => [...current, { role: "service", text: `Не удалось ответить: ${response.status}` }]);
        return;
      }
      const payload = (await response.json()) as { answer: string };
      setMessages((current) => [...current, { role: "service", text: payload.answer }]);
    } catch {
      setFailedQuestion(cleaned);
      setMessages((current) => [...current, { role: "service", text: "Нет ответа от сервера" }]);
    } finally {
      setAsking(false);
    }
  }

  async function pasteNews() {
    if (!selected || pasteBusy) {
      return;
    }
    setPasteBusy(true);
    setPasteError("");
    try {
      const response = await fetch(`/api/clients/${selected}/news`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: pasteTitle, text: pasteText }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
        setPasteError(payload?.detail || `Своя новость: ${response.status}`);
        return;
      }
      const payload = (await response.json()) as InsightCard;
      setCard(payload);
      setCardPhase("ready");
      setEventId(payload.event_id);
      setPasteTitle("");
      setPasteText("");
    } catch {
      setPasteError("Нет ответа от сервера");
    } finally {
      setPasteBusy(false);
    }
  }

  async function sendFeedback(helpful: boolean) {
    if (!selected || !eventId || feedbackSent) return;
    const response = await fetch(`/api/clients/${selected}/events/${eventId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ helpful }),
    });
    if (response.ok) setFeedbackSent(true);
  }

  const problem = listError || profileError || feedError || cardError;

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
        {problem ? (
          <p className="error">
            {problem}{" "}
            <button className="linkish" type="button" onClick={() => setAttempt((current) => current + 1)}>
              Повторить
            </button>
          </p>
        ) : null}
        <div className="switcher">
          {clientsPhase === "loading" ? <p>Загружаем компании…</p> : null}
          {clientsPhase === "empty" ? <p>Компаний нет.</p> : null}
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
            {selected && !profile ? (
              <p>{profileError ? "Профиль не загрузился." : "Загружаем профиль…"}</p>
            ) : null}
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
            <h2>Сигналы</h2>
            {profile && profile.exposures.length > 0 ? (
              <ul className="signals">
                {profile.exposures.map((item) => (
                  <li key={item.id}>{item.title}</li>
                ))}
              </ul>
            ) : (
              <p>{profile ? "Сигналов нет." : "Сигналы появятся вместе с профилем."}</p>
            )}
            {selected && feedPhase === "loading" ? <p>Собираем ленту…</p> : null}
            {feedPhase === "error" ? <p>Лента не загрузилась.</p> : null}
            {feed ? (
              <>
                <h2>Важно для компании</h2>
                <FeedItems items={feed.top} eventId={eventId} onSelect={setEventId} />
                <h2>Менее значимо для вас</h2>
                <FeedItems items={feed.less_relevant} eventId={eventId} onSelect={setEventId} />
              </>
            ) : null}
          </section>
          <section className="card insight">
            {card && cardPhase === "ready" ? (
              <>
                <CardBody card={card} />
                <p className="meta">
                  Уверенность {Math.round(card.confidence * 100)}%.{" "}
                  {ORIGIN_LABEL[card.llm_trace.origin ?? "template"] ?? "шаблон"}, режим {card.llm_trace.mode},
                  повторов проверки {card.llm_trace.retries}
                </p>
                <p className="note">Не хватает: {card.missing_data.join(", ") || "пропусков нет"}</p>
                <button className="linkish" type="button" onClick={() => setTraceOpen((open) => !open)}>
                  Как получен вывод
                </button>
                {traceOpen ? <Trace card={card} steps={steps} /> : null}
                <Chat
                  questions={card.action.questions}
                  messages={messages}
                  question={question}
                  asking={asking}
                  failedQuestion={failedQuestion}
                  onQuestion={setQuestion}
                  onAsk={ask}
                />
                <div className="feedback" aria-live="polite">
                  {feedbackSent ? (
                    <p>Спасибо, оценка учтена.</p>
                  ) : (
                    <>
                      <span>Карточка была полезна?</span>{" "}
                      <button type="button" onClick={() => void sendFeedback(true)}>Да</button>{" "}
                      <button type="button" onClick={() => void sendFeedback(false)}>Нет</button>
                    </>
                  )}
                </div>
              </>
            ) : (
              <p>
                {!selected
                  ? "Выберите компанию."
                  : cardPhase === "error"
                    ? "Не удалось собрать карточку."
                    : cardPhase === "empty" || (feedPhase === "ready" && !eventId) || feedPhase === "empty"
                      ? "В ленте нет новости для карточки."
                      : "Собираем карточку…"}
              </p>
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
          {pasteError ? <p className="error">{pasteError}</p> : null}
          <button type="button" onClick={() => void pasteNews()} disabled={pasteBusy || !selected}>
            {pasteBusy ? "Разбираем…" : "Разобрать"}
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
      <h2>Риск</h2>
      <Claims claims={card.consequences.risks} />
      <h2>Возможность</h2>
      <Claims claims={card.consequences.opportunities} />
      {card.consequences.depends_on.length > 0 ? <p className="note">Зависит от: {card.consequences.depends_on.join(" ")}</p> : null}
      <h2>Какое действие уместно</h2>
      <p>
        <Badge layer="model_inference" /> {card.action.next_step}
      </p>
      <h2>Как это оценивает банк</h2>
      {card.bank_view.status === "found" ? (
        <blockquote>
          <Badge layer="bank_position" /> {card.bank_view.quote}
          {card.bank_view.page ? <span className="meta"> стр. {card.bank_view.page}</span> : null}
        </blockquote>
      ) : (
        <p>Материала банка нет.</p>
      )}
      <h2>На чём основан вывод</h2>
      <ul className="sources">
        {card.sources.map((source) => (
          <SourceItem key={source.id} source={source} />
        ))}
      </ul>
    </>
  );
}

function SourceItem({ source }: { source: Source }) {
  const title = source.url ? (
    <a href={source.url} target="_blank" rel="noreferrer">
      {source.title}
    </a>
  ) : (
    source.title
  );
  return (
    <li>
      {title}
      {source.kind === "bank_analytics" && source.page ? <span>стр. {source.page}</span> : null}
      {source.kind === "bank_analytics" && source.quote ? <q>{source.quote}</q> : null}
      {source.kind === "news" && !source.url ? <span>ссылки нет</span> : null}
      {source.kind === "company_feature" && source.origin ? <span>{source.origin}</span> : null}
    </li>
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
  asking,
  failedQuestion,
  onQuestion,
  onAsk,
}: {
  questions: string[];
  messages: { role: "user" | "service"; text: string }[];
  question: string;
  asking: boolean;
  failedQuestion: string;
  onQuestion: (value: string) => void;
  onAsk: (text: string) => Promise<void>;
}) {
  return (
    <div className="chat">
      <h2>Уточнить</h2>
      <div className="quick">
        {questions.map((item) => (
          <button key={item} type="button" onClick={() => void onAsk(item)} disabled={asking}>
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
        <button type="submit" disabled={asking}>
          {asking ? "Отвечаем…" : "Спросить"}
        </button>
      </form>
      {failedQuestion ? (
        <button className="linkish" type="button" onClick={() => void onAsk(failedQuestion)}>
          Повторить вопрос
        </button>
      ) : null}
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
