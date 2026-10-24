import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import "./real.css";
import "./demo.css";
import { DemoProfile } from "./DemoProfile";
import { MarkdownText } from "./MarkdownText";
import { ModeSwitch, type AppMode } from "./ModeSwitch";
import {
  LAYER_LABEL, ORIGIN_LABEL, ROLE_LABEL, bandInfo,
  type ClientProfile, type ClientSummary, type Feed, type FeedItem, type NewsMeta,
} from "./demoTypes";
import { formatDay } from "./realTypes";

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
type ChatMessage = { role: "user" | "service"; text: string };

export function Service({
  clientId,
  mode,
  onModeChange,
  onHome,
  onLogout,
  onSwitch,
}: {
  clientId: string;
  mode: AppMode;
  onModeChange: (mode: AppMode) => void;
  onHome: () => void;
  onLogout: () => void;
  onSwitch: (clientId: string) => void;
}) {
  const selected = clientId;
  const [profile, setProfile] = useState<ClientProfile | null>(null);
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [newsMeta, setNewsMeta] = useState<Record<string, NewsMeta>>({});
  const [feed, setFeed] = useState<Feed | null>(null);
  const [feedPhase, setFeedPhase] = useState<Phase>("loading");
  const [view, setView] = useState<"news" | "profile">("news");
  const [tab, setTab] = useState<"top" | "all">("top");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [eventId, setEventId] = useState("");
  const [card, setCard] = useState<InsightCard | null>(null);
  const [cardPhase, setCardPhase] = useState<Phase>("loading");
  const [steps, setSteps] = useState<string[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [failedQuestion, setFailedQuestion] = useState("");
  const [pasteTitle, setPasteTitle] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [pasteBusy, setPasteBusy] = useState(false);
  const [pasteError, setPasteError] = useState("");
  const [profileError, setProfileError] = useState("");
  const [feedError, setFeedError] = useState("");
  const [cardError, setCardError] = useState("");
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const detailRef = useRef<HTMLElement | null>(null);

  // Справочники, не зависящие от выбранной компании: список компаний для переключателя и даты/источники новостей.
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/clients", { signal: controller.signal })
      .then((response) => (response.ok ? (response.json() as Promise<ClientSummary[]>) : []))
      .then((list) => setClients(list))
      .catch(() => undefined);
    void fetch("/api/news", { signal: controller.signal })
      .then((response) => (response.ok ? (response.json() as Promise<NewsMeta[]>) : []))
      .then((list) => setNewsMeta(Object.fromEntries(list.map((item) => [item.id, item]))))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!selected) {
      return;
    }
    const controller = new AbortController();
    setView("news");
    setTab("top");
    setQuery("");
    setCategory("all");
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
        // Если «важных» новостей нет, открываем первую из остальных, а не пустой экран.
        const next = payload.top[0]?.news_id ?? payload.less_relevant[0]?.news_id ?? "";
        setEventId(next);
        setTab(payload.top.length > 0 ? "top" : "all");
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

  function selectEvent(id: string) {
    setEventId(id);
    setView("news");
    if (window.matchMedia("(max-width: 860px)").matches) {
      window.setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
    }
  }

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
        setMessages((current) => [
          ...current,
          { role: "service", text: "Не получилось ответить. Нажмите «Повторить вопрос»." },
        ]);
        return;
      }
      const payload = (await response.json()) as { answer: string };
      setMessages((current) => [...current, { role: "service", text: plainAnswer(payload.answer) }]);
    } catch {
      setFailedQuestion(cleaned);
      setMessages((current) => [
        ...current,
        { role: "service", text: "Сервер не ответил. Нажмите «Повторить вопрос»." },
      ]);
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

  const problem = profileError || feedError || cardError;
  const allItems = feed ? [...feed.top, ...feed.less_relevant] : [];
  const categories = [
    ...new Map(
      allItems
        .filter((item) => item.event_type)
        .map((item): [string, string] => [
          item.event_type as string,
          newsMeta[item.news_id]?.event_title || (item.event_type as string),
        ]),
    ).entries(),
  ];
  const needle = query.trim().toLocaleLowerCase("ru-RU");
  const visible = (tab === "top" ? feed?.top ?? [] : allItems).filter((item) => {
    if (category !== "all" && item.event_type !== category) return false;
    if (!needle) return true;
    const meta = newsMeta[item.news_id];
    return `${item.title} ${meta?.excerpt ?? ""} ${meta?.source ?? ""}`.toLocaleLowerCase("ru-RU").includes(needle);
  });
  const selectedItem = allItems.find((item) => item.news_id === eventId) ?? null;
  const meta = eventId ? newsMeta[eventId] : undefined;
  const newsSource = card?.sources.find((source) => source.kind === "news");
  const detailTitle = selectedItem?.title ?? meta?.title ?? newsSource?.title ?? "Своя новость";
  const publishedAt = selectedItem?.published_at ?? meta?.published_at ?? "";
  const sourceName = meta?.source || newsSource?.origin || "";
  const sourceUrl = meta?.url || newsSource?.url || "";

  const header = (
    <header className="bank-bar">
      <button className="bar-button" type="button" onClick={onHome}>Альфа-Бизнес</button>
      <ModeSwitch mode={mode} onChange={onModeChange} />
      <div className="bar-user">
        <button className="bar-button bar-small" type="button" onClick={onLogout}>
          Выйти
        </button>
      </div>
    </header>
  );

  const banner = problem ? (
    <div className="banner banner-error" role="alert">
      <span>{problem}</span>
      <button className="btn-link" type="button" onClick={() => setAttempt((current) => current + 1)}>
        Повторить
      </button>
    </div>
  ) : null;

  if (view === "profile" && profile) {
    return (
      <div className="bank real">
        {header}
        <main className="bank-main form-main">
          {banner}
          <DemoProfile profile={profile} onBack={() => setView("news")} />
        </main>
      </div>
    );
  }

  return (
    <div className="bank real">
      {header}
      <main className="bank-main">
        <div className="real-head">
          <div>
            <h1>Что важно для вашей компании</h1>
            {profile ? (
              <p className="session-line">
                Тестовые данные · {profile.title}, {ROLE_LABEL[profile.money_role] ?? profile.money_role}.
                {profile.exposures.length > 0
                  ? ` Учитываем: ${profile.exposures.map((item) => item.title.toLocaleLowerCase("ru")).join(", ")}.`
                  : ""}
              </p>
            ) : (
              <p className="session-line">{profileError ? "Профиль не загрузился." : "Загружаем профиль…"}</p>
            )}
          </div>
          <div className="real-head-actions">
            {clients.length > 1 ? (
              <label className="company-switch">
                <span className="sr-only">Компания</span>
                <select
                  className="input"
                  value={selected}
                  onChange={(event) => onSwitch(event.target.value)}
                  aria-label="Сменить компанию"
                >
                  {clients.map((client) => (
                    <option key={client.client_id} value={client.client_id}>
                      {client.title} · {ROLE_LABEL[client.money_role] ?? client.money_role}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <button type="button" className="btn btn-primary" onClick={() => setView("profile")} disabled={!profile}>
              Профиль компании
            </button>
          </div>
        </div>
        {banner}

        <div className="workspace real-workspace">
          <section className="card feed-card" aria-label="Новости">
            <div className="seg-tabs" role="tablist" aria-label="Какие новости показывать">
              <button type="button" role="tab" aria-selected={tab === "top"} onClick={() => setTab("top")}>
                Для вас{feed ? <span className="count">{feed.top.length}</span> : null}
              </button>
              <button type="button" role="tab" aria-selected={tab === "all"} onClick={() => setTab("all")}>
                Все новости{feed ? <span className="count">{allItems.length}</span> : null}
              </button>
            </div>
            {feedPhase === "loading" ? <p className="hint" role="status">Собираем ленту…</p> : null}
            {feedPhase === "error" ? <p className="inline-error">Лента не загрузилась.</p> : null}
            {feed ? (
              <p className="hint small">
                {tab === "top"
                  ? "Новости с заметной связью с вашей компанией, самые важные выше."
                  : "Все новости тестового набора. Чем выше, тем важнее для этой компании."}
              </p>
            ) : null}
            <div className="feed-tools">
              <input
                className="input"
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Поиск по теме или источнику"
                aria-label="Поиск по новостям"
              />
            </div>
            <details className="filters" open={category !== "all"}>
              <summary>Фильтры{category !== "all" ? " · включены" : ""}</summary>
              <div className="filters-grid">
                <label className="field">
                  <span className="field-label">Тип события</span>
                  <select className="input" value={category} onChange={(event) => setCategory(event.target.value)}>
                    <option value="all">Все типы</option>
                    {categories.map(([type, title]) => (
                      <option key={type} value={type}>{title}</option>
                    ))}
                  </select>
                </label>
              </div>
            </details>
            <FeedList
              items={visible}
              selectedId={eventId}
              meta={newsMeta}
              onSelect={selectEvent}
              emptyText={
                !feed
                  ? ""
                  : query || category !== "all"
                    ? "По вашему запросу ничего не найдено."
                    : tab === "top"
                      ? "Пока нет новостей с заметной связью с компанией. Загляните во «Все новости»."
                      : "В тестовом наборе нет новостей."
              }
            />
          </section>

          <section className="card real-detail" ref={detailRef} aria-label="Разбор">
            {card && cardPhase === "ready" ? (
              <article className="detail">
                <p className="eyebrow">
                  {meta?.event_title || "Новость"}
                  {publishedAt ? ` · ${formatDay(publishedAt)}` : ""}
                </p>
                <h2 className="detail-title">{detailTitle}</h2>
                {sourceName || sourceUrl ? (
                  <p className="detail-source">
                    {sourceName}
                    {sourceUrl ? (
                      <>
                        {sourceName ? " · " : ""}
                        <a href={sourceUrl} target="_blank" rel="noreferrer">Читать первоисточник ↗</a>
                      </>
                    ) : null}
                  </p>
                ) : null}
                {meta?.excerpt ? <p className="detail-excerpt">{meta.excerpt}</p> : null}

                {selectedItem ? <WhyBlock item={selectedItem} /> : null}
                <CardBody card={card} />
                {card.missing_data.length > 0 ? (
                  <p className="note">Чтобы сказать точнее, в профиле нет: {humanList(card.missing_data)}.</p>
                ) : null}
                <details className="tech">
                  <summary>Как получен вывод</summary>
                  <Trace card={card} steps={steps} />
                </details>

                <div className="detail-block">
                  <DemoChat
                    questions={card.action.questions}
                    messages={messages}
                    question={question}
                    asking={asking}
                    failedQuestion={failedQuestion}
                    onQuestion={setQuestion}
                    onAsk={ask}
                  />
                </div>
                <div className="feedback" aria-live="polite">
                  {feedbackSent ? (
                    <p>Спасибо, оценка учтена.</p>
                  ) : (
                    <>
                      <span>Этот разбор полезен?</span>
                      <button type="button" className="btn btn-ghost small" onClick={() => void sendFeedback(true)}>Да</button>
                      <button type="button" className="btn btn-ghost small" onClick={() => void sendFeedback(false)}>Нет</button>
                    </>
                  )}
                </div>
              </article>
            ) : (
              <p className="empty" role="status">
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

        <details className="card paste">
          <summary>Разобрать свою новость</summary>
          <p className="hint">Вставьте текст. Разбор будет таким же: новость, банк и вывод для компании.</p>
          <input
            className="input"
            value={pasteTitle}
            onChange={(event) => setPasteTitle(event.target.value)}
            placeholder="Заголовок"
            aria-label="Заголовок своей новости"
          />
          <textarea
            className="input"
            value={pasteText}
            onChange={(event) => setPasteText(event.target.value)}
            placeholder="Текст новости"
            aria-label="Текст своей новости"
            rows={4}
          />
          {pasteError ? <p className="inline-error">{pasteError}</p> : null}
          <button type="button" className="btn btn-primary" onClick={() => void pasteNews()} disabled={pasteBusy || !selected}>
            {pasteBusy ? "Разбираем…" : "Разобрать"}
          </button>
        </details>
      </main>
    </div>
  );
}

function FeedList({
  items,
  selectedId,
  meta,
  onSelect,
  emptyText,
}: {
  items: FeedItem[];
  selectedId: string;
  meta: Record<string, NewsMeta>;
  onSelect: (newsId: string) => void;
  emptyText: string;
}) {
  if (items.length === 0) {
    return emptyText ? <p className="empty">{emptyText}</p> : null;
  }
  return (
    <ul className="news-list">
      {items.map((item) => {
        const info = bandInfo(item.band);
        const record = meta[item.news_id];
        const active = item.news_id === selectedId;
        return (
          <li key={item.news_id}>
            <button
              type="button"
              className={`news-row${active ? " active" : ""}`}
              aria-current={active ? "true" : undefined}
              onClick={() => onSelect(item.news_id)}
            >
              <span className="news-row-meta">
                {record?.event_title ? `${record.event_title} · ` : ""}
                {item.published_at ? formatDay(item.published_at) : ""}
              </span>
              <span className="news-row-title">{item.title}</span>
              <span className="news-row-foot">
                <span className="news-row-source">{record?.source ?? ""}</span>
                <span className={`chip chip-${info.level}`}>{info.label}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function WhyBlock({ item }: { item: FeedItem }) {
  const info = bandInfo(item.band);
  const reasons = item.reason.split(";").map((part) => part.trim()).filter(Boolean);
  const factors = item.factors ?? [];
  return (
    <section className="detail-block">
      <h3>Почему эта новость в вашей ленте</h3>
      <p><span className={`chip chip-${info.level}`}>{info.label}</span></p>
      {reasons.length > 0 ? (
        <ul className="reasons">
          {reasons.map((reason, index) => <li key={`${index}-${reason}`}>{reason}</li>)}
        </ul>
      ) : null}
      <details className="tech">
        <summary>Как посчитано</summary>
        <p>Итоговый балл: {item.score.toFixed(2)}.</p>
        <ul className="reasons">
          {factors.map((factor) => (
            <li key={factor.id}>
              {factor.label}: {factor.value > 0 ? "+" : ""}{factor.value.toFixed(2)}
              {factor.note ? ` — ${factor.note}` : ""}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}

function CardBody({ card }: { card: InsightCard }) {
  const caveats = [...card.what_happened.assumptions, ...card.consequences.depends_on];
  return (
    <>
      <p className="legend">
        <span>
          <Badge layer="confirmed_fact" /> из новости
        </span>
        <span>
          <Badge layer="bank_position" /> слова аналитиков банка
        </span>
        <span>
          <Badge layer="model_inference" /> вывод для вашей компании
        </span>
      </p>
      <section className="detail-block">
        <h3>Что произошло</h3>
        <Claims claims={card.what_happened.claims} />
      </section>
      <section className="detail-block">
        <h3>Что это значит для вас</h3>
        <Claims claims={card.why_for_company.claims} />
        <div className="report">
          <div className="report-cols">
            <div className="report-col risk">
              <h4>Риск</h4>
              <Claims claims={card.consequences.risks} />
            </div>
            <div className="report-col chance">
              <h4>Возможность</h4>
              <Claims claims={card.consequences.opportunities} />
            </div>
          </div>
        </div>
      </section>
      <section className="detail-block">
        <h3>Что сделать</h3>
        <p>
          <Badge layer="model_inference" /> {card.action.next_step}
        </p>
        <Caveats lines={caveats} />
      </section>
      <section className="detail-block">
        <h3>Что говорят аналитики банка</h3>
        {card.bank_view.status === "found" ? (
          <blockquote>
            <Badge layer="bank_position" /> {card.bank_view.quote}
            {card.bank_view.page ? <span className="meta"> стр. {card.bank_view.page}</span> : null}
          </blockquote>
        ) : (
          <p className="hint">По этой новости материала банка нет. Вывод выше опирается на новость и профиль компании.</p>
        )}
      </section>
      <section className="detail-block">
        <h3>Откуда это</h3>
        <ul className="sources">
          {card.sources
            .filter((source) => source.kind !== "company_feature")
            .map((source) => (
              <SourceItem key={source.id} source={source} />
            ))}
        </ul>
      </section>
    </>
  );
}

function Caveats({ lines }: { lines: string[] }) {
  const unique = [...new Set(lines.map((line) => line.trim()).filter(Boolean))];
  const gaps = unique.filter((line) => line.startsWith("Неизвестно:"));
  const dates = unique.filter((line) => line.startsWith("Даты источников"));
  const rest = unique.filter((line) => !gaps.includes(line) && !dates.includes(line));
  if (unique.length === 0) {
    return null;
  }
  return (
    <div className="caveat">
      {gaps.length > 0 ? <p>{gapSentence(gaps)}</p> : null}
      {rest.map((line) => (
        <p key={line}>{line.replace(/^Допущение:\s*/, "")}</p>
      ))}
      {dates.length > 0 ? (
        <details>
          <summary>Почему даты материалов разные</summary>
          {dates.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </details>
      ) : null}
    </div>
  );
}

function gapSentence(gaps: string[]): string {
  const bodies = humanList(gaps);
  return `${bodies.charAt(0).toLocaleUpperCase("ru")}${bodies.slice(1)}. В выводе этого нет, потому что в профиле так не указано.`;
}

function plainAnswer(text: string): string {
  const unknown = text.match(/^Неизвестно:\s*(.+)$/i);
  if (unknown) {
    const body = unknown[1].replace(/\.$/, "");
    return `${body}. В профиле этого нет, поэтому так и отвечаем.`;
  }
  const missing = text.match(/^Не хватает данных:\s*(.+)$/i);
  if (missing) {
    return `Чтобы сказать точнее, в профиле нет: ${missing[1]}`;
  }
  return text;
}

function humanList(items: string[]): string {
  return items
    .map((item) => {
      const cleaned = item.replace(/^Неизвестно:\s*/i, "").replace(/\.$/, "");
      return cleaned.charAt(0).toLocaleLowerCase("ru") + cleaned.slice(1);
    })
    .filter(Boolean)
    .join("; ");
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
      {source.kind === "bank_analytics" ? (
        <span>{` · материал банка${source.page ? `, стр. ${source.page}` : ""}`}</span>
      ) : null}
      {source.kind === "news" && !source.url ? <span>ссылки нет</span> : null}
    </li>
  );
}

function Claims({ claims }: { claims: Claim[] }) {
  if (claims.length === 0) {
    return <p className="hint">По этому пункту вывода нет. Причину видно в «Как получен вывод».</p>;
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
      <p>
        {ORIGIN_LABEL[card.llm_trace.origin ?? "template"] ?? "шаблон"}, режим {card.llm_trace.mode}, повторов{" "}
        {card.llm_trace.retries}, уверенность {Math.round(card.confidence * 100)}%.
      </p>
      {card.missing_data.length > 0 ? <p>Не хватило: {card.missing_data.join(", ")}</p> : null}
    </div>
  );
}

function DemoChat({
  questions,
  messages,
  question,
  asking,
  failedQuestion,
  onQuestion,
  onAsk,
}: {
  questions: string[];
  messages: ChatMessage[];
  question: string;
  asking: boolean;
  failedQuestion: string;
  onQuestion: (value: string) => void;
  onAsk: (text: string) => Promise<void>;
}) {
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const node = listRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages.length, asking]);

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void onAsk(question);
    }
  }

  return (
    <section className="chat-panel demo-chat" aria-label="Вопросы по карточке">
      <h3>Спросить</h3>
      <p className="hint">Спросите своими словами. Ответ строится по этой карточке и материалам банка.</p>
      <div className="chips">
        {questions.map((item) => (
          <button key={item} className="chip-btn" type="button" onClick={() => void onAsk(item)} disabled={asking}>
            {item}
          </button>
        ))}
      </div>
      {messages.length === 0 && !asking ? <p className="hint">Ответ появится здесь.</p> : null}
      <div className="chat-list" ref={listRef} aria-live="polite">
        {messages.map((item, index) => (
          <article key={`${item.role}-${index}`} className={`chat-msg ${item.role === "user" ? "user" : "assistant"}`}>
            <span className="who">{item.role === "user" ? "Вы" : "Ответ"}</span>
            {item.role === "service" ? <MarkdownText text={item.text} /> : <p>{item.text}</p>}
          </article>
        ))}
        {asking ? (
          <article className="chat-msg assistant">
            <span className="who">Ответ</span>
            <p className="hint">Смотрим карточку…</p>
          </article>
        ) : null}
      </div>
      {failedQuestion ? (
        <button className="btn-link" type="button" onClick={() => void onAsk(failedQuestion)}>
          Повторить вопрос
        </button>
      ) : null}
      <form
        className="chat-form"
        onSubmit={(event) => {
          event.preventDefault();
          void onAsk(question);
        }}
      >
        <label htmlFor="demo-chat-question" className="sr-only">Вопрос по карточке</label>
        <textarea
          id="demo-chat-question"
          className="input"
          rows={2}
          maxLength={2000}
          value={question}
          onChange={(event) => onQuestion(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Например: что сделать по этой новости?"
        />
        <button type="submit" className="btn btn-primary" disabled={asking || !question.trim()}>
          {asking ? "Отвечаем…" : "Спросить"}
        </button>
      </form>
      <p className="hint small">Enter — отправить, Shift+Enter — новая строка.</p>
    </section>
  );
}
