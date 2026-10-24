import { useEffect, useState } from "react";
import { ModeSwitch, type AppMode } from "./ModeSwitch";

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
  confirmed_fact: "Из новости",
  bank_position: "Банк",
  model_inference: "Для вас",
};

export function Service({
  clientId,
  mode,
  onModeChange,
  onHome,
  onLogout,
}: {
  clientId: string;
  mode: AppMode;
  onModeChange: (mode: AppMode) => void;
  onHome: () => void;
  onLogout: () => void;
}) {
  const selected = clientId;
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
  const [profileError, setProfileError] = useState("");
  const [feedError, setFeedError] = useState("");
  const [cardError, setCardError] = useState("");
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [attempt, setAttempt] = useState(0);

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

  return (
    <div className="bank">
      <header className="bank-bar">
        <button className="bar-button" type="button" onClick={onHome}>Альфа-Бизнес</button>
        <ModeSwitch mode={mode} onChange={onModeChange} />
        <button className="bar-button" type="button" onClick={onLogout}>
          Выйти
        </button>
      </header>
      <main className="bank-main">
        <h1>Что важно для вашей компании</h1>
        {profile ? (
          <p className="session-line">
            Вы вошли как {profile.title}, {ROLE_LABEL[profile.money_role] ?? profile.money_role}.
            {profile.exposures.length > 0
              ? ` Учитываем: ${profile.exposures.map((item) => item.title.toLocaleLowerCase("ru")).join(", ")}.`
              : ""}
          </p>
        ) : (
          <p>{profileError ? "Профиль не загрузился." : "Загружаем профиль…"}</p>
        )}
        {problem ? (
          <p className="error">
            {problem}{" "}
            <button className="linkish" type="button" onClick={() => setAttempt((current) => current + 1)}>
              Повторить
            </button>
          </p>
        ) : null}
        <div className="workspace">
          <section className="card">
            {selected && feedPhase === "loading" ? <p>Собираем ленту…</p> : null}
            {feedPhase === "error" ? <p>Лента не загрузилась.</p> : null}
            {feed ? (
              <>
                <h2>Важные новости</h2>
                <p className="hint">Нажмите новость. Справа будет, что она значит для компании.</p>
                <FeedItems items={feed.top} eventId={eventId} onSelect={setEventId} />
                {feed.less_relevant.length > 0 ? (
                  <details>
                    <summary>Остальные новости</summary>
                    <FeedItems items={feed.less_relevant} eventId={eventId} onSelect={setEventId} />
                  </details>
                ) : null}
              </>
            ) : null}
          </section>
          <section className="card insight">
            {card && cardPhase === "ready" ? (
              <>
                <CardBody card={card} />
                {card.missing_data.length > 0 ? (
                  <p className="note">Чтобы сказать точнее, в профиле нет: {humanList(card.missing_data)}.</p>
                ) : null}
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
                      <span>Этот разбор полезен?</span>{" "}
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
        <details className="card paste">
          <summary>Разобрать свою новость</summary>
          <p className="hint">Вставьте текст. Разбор будет таким же: новость, банк и вывод для компании.</p>
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
        </details>
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
          </button>
        </li>
      ))}
    </ul>
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
      <h2>Что произошло</h2>
      <Claims claims={card.what_happened.claims} />
      <h2>Что это значит для вас</h2>
      <Claims claims={card.why_for_company.claims} />
      <h2>Риск</h2>
      <Claims claims={card.consequences.risks} />
      <h2>Возможность</h2>
      <Claims claims={card.consequences.opportunities} />
      <h2>Что сделать</h2>
      <p>
        <Badge layer="model_inference" /> {card.action.next_step}
      </p>
      <Caveats lines={caveats} />
      <h2>Что говорят аналитики банка</h2>
      {card.bank_view.status === "found" ? (
        <blockquote>
          <Badge layer="bank_position" /> {card.bank_view.quote}
          {card.bank_view.page ? <span className="meta"> стр. {card.bank_view.page}</span> : null}
        </blockquote>
      ) : (
        <p>По этой новости материала банка нет. Вывод выше опирается на новость и профиль компании.</p>
      )}
      <h2>Откуда это</h2>
      <ul className="sources">
        {card.sources
          .filter((source) => source.kind !== "company_feature")
          .map((source) => (
            <SourceItem key={source.id} source={source} />
          ))}
      </ul>
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
    return <p>По этому пункту вывода нет.</p>;
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
      <h2>Спросить</h2>
      <p className="hint">Спросите своими словами. Ответ строится по этой карточке и материалам банка.</p>
      <div className="quick">
        {questions.map((item) => (
          <button key={item} className="chip" type="button" onClick={() => void onAsk(item)} disabled={asking}>
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
          placeholder="Например: что сделать по этой новости?"
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
      {messages.length === 0 && !asking ? <p className="hint">Ответ появится здесь.</p> : null}
      <ul className="messages">
        {messages.map((item, index) => (
          <li key={`${item.role}-${index}`} className={item.role}>
            <span className="who">{item.role === "user" ? "Вы" : "Ответ"}</span>
            {item.role === "service" ? <AnswerText text={item.text} /> : <p>{item.text}</p>}
          </li>
        ))}
        {asking ? (
          <li className="service">
            <span className="who">Ответ</span>
            Смотрим карточку…
          </li>
        ) : null}
      </ul>
    </div>
  );
}


type AnswerBlock =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "list"; ordered: boolean; items: string[] };

function AnswerText({ text }: { text: string }) {
  const blocks: AnswerBlock[] = [];
  let paragraph: string[] = [];
  let listItems: string[] = [];
  let listOrdered: boolean | null = null;

  const flushParagraph = () => {
    const value = paragraph.join(" ").trim();
    if (value) blocks.push({ kind: "paragraph", text: value });
    paragraph = [];
  };
  const flushList = () => {
    if (listItems.length && listOrdered !== null) {
      blocks.push({ kind: "list", ordered: listOrdered, items: listItems });
    }
    listItems = [];
    listOrdered = null;
  };

  for (const rawLine of text.replace(/\r/g, "").split("\n")) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({
        kind: "heading",
        level: Math.min(heading[1].length, 4),
        text: heading[2].replace(/\s+#+$/, ""),
      });
      continue;
    }
    const ordered = line.match(/^\d+[.)]\s+(.+)$/);
    const unordered = line.match(/^[-*+]\s+(.+)$/);
    if (ordered || unordered) {
      flushParagraph();
      const isOrdered = Boolean(ordered);
      if (listOrdered !== null && listOrdered !== isOrdered) flushList();
      listOrdered = isOrdered;
      listItems.push((ordered ?? unordered)![1]);
      continue;
    }
    flushList();
    paragraph.push(line);
  }
  flushParagraph();
  flushList();

  return (
    <div className="answer-content">
      {blocks.map((block, index) => {
        if (block.kind === "heading") {
          const Tag = `h${block.level}` as "h1" | "h2" | "h3" | "h4";
          return <Tag key={index}>{inlineMarkdown(block.text)}</Tag>;
        }
        if (block.kind === "list") {
          const List = block.ordered ? "ol" : "ul";
          return (
            <List key={index}>
              {block.items.map((item, itemIndex) => <li key={itemIndex}>{inlineMarkdown(item)}</li>)}
            </List>
          );
        }
        return <p key={index}>{inlineMarkdown(block.text)}</p>;
      })}
    </div>
  );
}

function inlineMarkdown(text: string) {
  return text.split(/(\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g).map((part, index) => {
    if ((part.startsWith("**") && part.endsWith("**")) || (part.startsWith("__") && part.endsWith("__"))) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) {
      return <em key={index}>{part.slice(1, -1)}</em>;
    }
    return part;
  });
}
