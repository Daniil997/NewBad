import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { MarkdownText } from "./MarkdownText";
import { BankRecommendation } from "./BankRecommendation";
import {
  formatDateTime, formatPrice, relevanceInfo,
  type BusinessProfile, type ChatMessage, type MarketHistory,
  type RealMarkets, type RealNewsItem, type SiteSuggestion,
} from "./realTypes";

import { NewsAnalysis, NewsArticleHeading, NewsRelevance } from "./NewsWorkspace";

export function isCurrencyRatesNews(item: RealNewsItem): boolean {
  // Курсы валют — отдельный виджет, а не новость в ленте.
  return /курсы? валют/i.test(item.title) || parseCurrencyRates(item.excerpt).length > 0;
}

function parseCurrencyRates(excerpt: string): { name: string; value: string }[] {
  const rates: { name: string; value: string }[] = [];
  const pattern = /(?:^|\s)([А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z .()]*?)\s+-\s+(\d+[,.]\d+)/g;
  for (const match of excerpt.matchAll(pattern)) {
    const name = match[1].trim();
    if (name) rates.push({ name, value: match[2].replace(".", ",") });
  }
  return rates;
}

export function CurrencyRatesPanel({ item }: { item: RealNewsItem }) {
  const rates = parseCurrencyRates(item.excerpt);
  if (!rates.length) return null;
  return (
    <section className="card side-card currency-rates" aria-label="Официальные курсы валют">
      <h2>Курсы валют</h2>
      <p className="hint small">Официальные курсы Банка России на дату публикации. Значения указаны в рублях по номиналу, установленному для каждой валюты.</p>
      <ul className="market-list">
        {rates.map((rate) => (
          <li className="market-row" key={rate.name}>
            <div className="market-top">
              <div><strong>{rate.name}</strong></div>
              <div className="market-price"><b>{rate.value} RUB</b></div>
            </div>
            <div className="market-foot">
              <small>{item.source}</small>
            </div>
          </li>
        ))}
      </ul>
      {item.url ? <p className="hint small">Источник: <a href={item.url} target="_blank" rel="noreferrer">Банк России — курсы валют</a></p> : null}
    </section>
  );
}

export function NewsDetail({ item, reportBusy, chatBusy, onReport, onChat }: {
  item: RealNewsItem; reportBusy: boolean; chatBusy: boolean;
  onReport: (refresh: boolean) => void; onChat: () => void;
}) {
  const analysis = item.analysis;
  const rel = analysis?.relevant ? relevanceInfo(analysis.score) : null;
  const report = item.report;
  const score = analysis ? Math.round(analysis.score * 100) : null;

  return (
    <article className="detail real-insight-card">
      <NewsArticleHeading title={item.title} category={item.event_title} date={item.published_at} source={item.source} url={item.url} />
      <NewsRelevance label={rel?.label || (analysis ? "Связь не подтверждена" : "Оценка ещё не готова")} level={rel?.level}>
        {analysis ? (
          <>
            <ul className="real-link-signals">{analysis.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>
            <details className="tech">
              <summary>Как оценена связь</summary>
              <p>Индекс связи: {score}/100. Метод: {analysis.method}. Оценка от {formatDateTime(analysis.analyzed_at)}.</p>
              <p>Индекс отражает признаки связи, а не вероятность влияния события.</p>
              <p>Слабая связь: 30–54, есть связь: 55–79, сильная связь: 80–100. Ниже 30 связь не подтверждена.</p>
            </details>
          </>
        ) : <p className="hint">Нажмите «Обновить подборку», чтобы сопоставить публикацию с профилем.</p>}
      </NewsRelevance>

      <NewsAnalysis
        facts={item.excerpt ? (
          item.excerpt.length > 560 ? <details className="real-source-text">
            <summary>{item.excerpt.slice(0, 560).trimEnd() + "…"}</summary>
            <div className="real-source-full"><p>{item.excerpt}</p></div>
          </details> : <p className="real-source-text real-source-short">{item.excerpt}</p>
        ) : <p className="hint">Текст публикации не загружен.</p>}
        meaning={report ? <>
          <div className="real-card-lead"><MarkdownText text={report.summary} /></div>
          <div className="real-report-meaning"><h4>Как событие может повлиять на бизнес</h4>
            <MarkdownText text={report.relevance} />
            {report.impact_channel ? <p className="hint small">Канал влияния: {report.impact_channel}</p> : null}
          </div>
        </> : <div className="real-report-start">
            <p className="hint">Подготовим разбор по вашему профилю: механизм влияния, риски и возможности, следующий шаг и вопросы для уточнения.</p>
            <button type="button" className="btn btn-primary" onClick={() => onReport(false)} disabled={reportBusy}>
              {reportBusy ? "Анализируем…" : "Разобрать для моей компании"}
            </button>
          </div>}
        risks={report ? <MarkdownText text={report.risks} /> : undefined}
        opportunities={report ? <MarkdownText text={report.opportunities} /> : undefined}
        nextStep={report ? <MarkdownText text={report.next_step || "Проверьте, затрагивают ли изменения ваши договоры, расходы или клиентов, прежде чем принимать решения."} /> : undefined}
      >
        {report ? <>
            {report.questions.length > 0 ? (
              <div className="real-questions">
                <h4>Что уточнить для более точного вывода</h4>
                <ul className="reasons">{report.questions.map((question, index) => <li key={index}>{question}</li>)}</ul>
              </div>
            ) : null}
            <details className="real-report-evidence">
              <summary>Источники и ограничения анализа</summary>
              <p className="hint small">{report.origin === "model" ? "Модель сформировала оценку; числа проверены по переданным материалам." :
                report.origin === "rules" ? "Вывод по правилам профиля — гипотезы для проверки, не факты о компании." :
                "Аналитический вывод следует проверить по источнику и данным компании."}</p>
              {report.citations.length ? <ul className="sources">{report.citations.map((citation, index) =>
                <li key={index}>{citation.url
                  ? <a href={citation.url} target="_blank" rel="noreferrer">{citation.title}</a>
                  : citation.title}
                  {citation.published_at ? " · " + citation.published_at : ""}
                </li>)}</ul> : null}
              {report.evidence_ids.length ? <p className="hint small">Основания: {report.evidence_ids.join(", ")}</p> : null}
            </details>
            <p className="report-foot">Разбор от {formatDateTime(report.created_at)} · <button type="button" className="btn-link" onClick={() => onReport(true)} disabled={reportBusy}>{reportBusy ? "Обновляем…" : "Пересчитать разбор"}</button></p>
        </> : null}
      </NewsAnalysis>
      <BankRecommendation recommendation={item.bank_recommendation} />
      <div className="detail-actions">
        <button type="button" className="btn btn-ghost" onClick={onChat} disabled={chatBusy}>
          {chatBusy ? "Открываем чат…" : "Спросить об этой новости"}
        </button>
      </div>
    </article>
  );
}

/* ───────────── Чат ───────────── */

export function ChatPanel({ sessionId, ownerId, csrf, companyName, titleOverride, onUpdated }: {
  sessionId: string; ownerId: string; csrf: string; companyName: string; titleOverride?: string; onUpdated: () => void;
}) {
  const draftKey = `business-chat-draft:${ownerId}:${sessionId}`;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState(() => { try { return localStorage.getItem(draftKey) || ""; } catch { return ""; } });
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [title, setTitle] = useState("Общий чат по бизнесу");
  const [focused, setFocused] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  const alive = useRef(true);
  const sending = useRef(false);

  async function load(signal?: AbortSignal) {
    const response = await fetch(`/api/real/chat/sessions/${encodeURIComponent(sessionId)}`, { signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Не удалось открыть диалог");
    if (!alive.current || signal?.aborted) return;
    setMessages(data.messages as ChatMessage[]);
    setTitle(data.title || "Общий чат по бизнесу");
    setFocused(Boolean(data.focused_news_id));
    setLoading(false);
    return data as { messages: ChatMessage[] };
  }

  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    void load(controller.signal).catch((reason: unknown) => {
      if (!controller.signal.aborted) { setError(reason instanceof Error ? reason.message : "Не удалось загрузить диалог"); setLoading(false); }
    });
    return () => { alive.current = false; controller.abort(); };
  }, [sessionId]);

  useEffect(() => {
    try { if (question) localStorage.setItem(draftKey, question); else localStorage.removeItem(draftKey); } catch { /* drafts are optional */ }
  }, [draftKey, question]);

  const pending = messages.some((message) => message.request_status === "pending");
  useEffect(() => {
    if (!pending || busy) return;
    const timer = window.setInterval(() => { void load().catch(() => {}); }, 2500);
    return () => window.clearInterval(timer);
  }, [pending, busy]);

  useEffect(() => {
    const node = listRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages.length, busy]);

  async function ask(raw: string, retryId?: string) {
    const text = raw.trim();
    if (!text || sending.current || loading || pending) return;
    const requestId = retryId || crypto.randomUUID();
    const optimistic: ChatMessage = { role: "user", text, citations: [], created_at: new Date().toISOString(), request_id: requestId };
    sending.current = true;
    setBusy(true); setError("");
    if (!retryId) { setQuestion(""); setMessages((current) => [...current, optimistic]); }
    try {
      const response = await fetch(`/api/real/chat/sessions/${encodeURIComponent(sessionId)}/messages`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ question: text, request_id: requestId }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Не удалось получить ответ");
      if (alive.current) await load();
      onUpdated();
    } catch (reason) {
      if (!alive.current) return;
      // Fetch the durable server state: even a lost HTTP response may have
      // completed successfully. Retrying the same request never duplicates it.
      try {
        const saved = await load();
        const turn = saved?.messages.find((message) => message.request_id === requestId);
        if (turn?.request_status === "completed") { onUpdated(); return; }
        if (!turn && !retryId) setQuestion((current) => current || text);
      } catch {
        setMessages((current) => current.map((message) => message.request_id === requestId ? { ...message, request_status: "failed" } : message));
      }
      setError(reason instanceof Error ? reason.message : "Не удалось связаться с чатом");
      onUpdated();
    } finally { sending.current = false; if (alive.current) setBusy(false); }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault(); void ask(question);
    }
  }

  const quick = focused
    ? ["Что это значит для нашей компании?", "Какие здесь риски?", "Что стоит проверить в первую очередь?"]
    : ["Какие свежие новости важнее всего для нас?", "Что из новостей может затронуть наши поставки?"];

  return <section className="chat-panel" aria-label="Чат по бизнесу" aria-busy={loading || busy || pending}>
    <h2 className="detail-title">{titleOverride || title}</h2>
    <p className="hint">{companyName || "Ваш бизнес"}. {focused ? "Обсуждаем выбранную новость." : "Обсуждаем бизнес и свежие новости."} Сообщите детали прямо здесь — они будут учтены в этом диалоге.</p>
    <div className="chat-list" ref={listRef} aria-live="polite">
      {loading ? <p role="status">Открываем сохранённый разговор…</p> : null}
      {messages.length === 0 && !loading && !busy ? <div className="chat-empty">
        <p className="hint">Задайте вопрос своими словами или выберите готовый:</p>
        <div className="chips">{quick.map((text) => <button key={text} type="button" className="chip-btn" onClick={() => void ask(text)}>{text}</button>)}</div>
      </div> : null}
      {messages.map((message, index) => <article className={`chat-msg ${message.role}`} key={message.request_id || `${message.created_at}-${index}`}>
        <span className="who">{message.role === "user" ? "Вы" : "Помощник"}</span>
        <MarkdownText text={message.text} />
        {message.citations.length ? <p className="chat-cites">Материалы: {message.citations.map((citation, citationIndex) => <span key={`${citation.url}-${citationIndex}`}>
          {citationIndex ? " · " : ""}{citation.url ? <a href={citation.url} target="_blank" rel="noreferrer">{citation.title}</a> : citation.kind === "user_statement" ? "Сообщение в этом диалоге" : citation.title}
          {citation.published_at ? ` (${citation.published_at})` : ""}
        </span>)}</p> : null}
        {message.request_status === "failed" ? <div className="chat-retry"><small>Сообщение сохранено, ответ не получен.</small><button type="button" className="btn-link" disabled={busy || pending} onClick={() => void ask(message.text, message.request_id)}>Повторить ответ</button></div> : null}
      </article>)}
      {busy || pending ? <article className="chat-msg assistant"><span className="who">Помощник</span><p className="hint">Готовим ответ… Можно переключиться на другой чат.</p></article> : null}
    </div>
    {error ? <div role="alert"><p className="inline-error">{error}</p><button type="button" className="btn-link" onClick={() => { void load().then(() => setError("")).catch(() => {}); }}>Обновить диалог</button></div> : null}
    <form className="chat-form" onSubmit={(event) => { event.preventDefault(); void ask(question); }}>
      <label htmlFor="real-chat-question" className="sr-only">Ваш вопрос</label>
      <textarea className="input" id="real-chat-question" rows={3} maxLength={2000} value={question}
        onChange={(event) => setQuestion(event.target.value)} onKeyDown={onKeyDown}
        placeholder="Задайте вопрос или уточните данные: например, у нас кредит с плавающей ставкой…" />
      <button type="submit" className="btn btn-primary" disabled={loading || busy || pending || !question.trim()}>{busy || pending ? "Отвечаем…" : "Отправить"}</button>
    </form>
    <p className="hint small">Диалог сохраняется автоматически. Enter — отправить, Shift+Enter — новая строка.</p>
  </section>;
}

/* ───────────── Обзор и рынки ───────────── */

/* ───────────── Рынки ───────────── */

export function MarketsPanel({ markets, histories, historyBusy, onToggle }: {
  markets: RealMarkets | null; histories: Record<string, MarketHistory>; historyBusy: string;
  onToggle: (category: string, instrument: string) => void;
}) {
  const items = markets?.snapshot.items ?? [];
  return (
    <section className="card side-card" id="real-markets">
      <h2>Рынки</h2>
      {items.length ? (
        <ul className="market-list">
          {items.map((item) => {
            const key = `${item.category}:${item.instrument}`;
            const history = histories[key];
            const trend = item.change_pct === null ? "" : item.change_pct > 0 ? "up" : item.change_pct < 0 ? "down" : "flat";
            return (
              <li key={key} className="market-row">
                <div className="market-top">
                  <div><strong>{item.name}</strong> <small>{item.instrument}</small></div>
                  <div className="market-price">
                    <b>{formatPrice(item.price)} {item.currency}</b>
                    {item.change_pct !== null ? <span className={`chip chip-${trend}`}>{item.change_pct > 0 ? "+" : ""}{item.change_pct}%</span> : null}
                  </div>
                </div>
                {item.profile_reason ? <p className="market-reason">{item.profile_reason}</p> : null}
                <div className="market-foot">
                  <small>MOEX · {item.source_timestamp} · <a href={item.source_url} target="_blank" rel="noreferrer">источник</a></small>
                  <button type="button" className="btn-link" onClick={() => onToggle(item.category, item.instrument)} disabled={historyBusy === key}>
                    {historyBusy === key ? "Загружаем…" : history ? "Скрыть историю" : "История"}
                  </button>
                </div>
                {history ? (
                  <div className="market-history">
                    <p>Изменение за сохранённый период: {history.change_over_period_pct === null ? "недостаточно точек" : `${history.change_over_period_pct}%`}</p>
                    <ol>{history.items.map((point, index) => (
                      <li key={`${point.source_timestamp}-${index}`}>{point.source_timestamp}: {formatPrice(point.price)} {point.currency} · <a href={point.source_url} target="_blank" rel="noreferrer">MOEX</a></li>
                    ))}</ol>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="empty">{markets?.latest ? "Котировок пока нет. Запустите сбор в настройках разработки." : "Сбор рыночных данных ещё не запускался. Запустить его можно в настройках разработки."}</p>
      )}
      <p className="hint small">
        Источник: Московская биржа (MOEX ISS){markets?.delayed ? ", данные задержаны" : ""}.
        {markets?.latest ? ` Последний сбор: ${formatDateTime(markets.latest.finished_at)}.` : ""}
      </p>
    </section>
  );
}

/* ───────────── Предложения по сайту ───────────── */

export function SuggestionsPanel({ suggestions, warnings, busy, dirty, label, fieldLabels, onDecide, onAcceptAll }: {
  suggestions: SiteSuggestion[]; warnings: string[]; busy: boolean; dirty: boolean;
  label: (field: string, value: string | string[]) => string;
  fieldLabels: Record<string, string>;
  onDecide: (suggestion: SiteSuggestion, accept: boolean) => void;
  onAcceptAll: () => void;
}) {
  if (!suggestions.length && !warnings.length) return null;
  return (
    <section className="card site-suggestions">
      <div className="site-suggestions-heading">
        <div>
          <p className="eyebrow">Результаты изучения сайта</p>
          <h2>Проверьте найденные данные{suggestions.length ? ` · ${suggestions.length}` : ""}</h2>
          <p className="hint">Нейросеть предлагает значения только для пустых полей. Каждый фрагмент подкреплён цитатой и ссылкой на страницу — подтвердите его после проверки.</p>
        </div>
        {suggestions.length > 1 ? (
          <button type="button" className="btn btn-primary small" onClick={onAcceptAll} disabled={busy || dirty}>
            {busy ? "Применяем…" : `Принять все ${suggestions.length}`}
          </button>
        ) : null}
      </div>
      {dirty ? <p className="site-warning">Сохраните ручные изменения перед подтверждением сведений с сайта.</p> : null}
      {warnings.map((warning, index) => <p className="site-warning" key={`${index}-${warning}`}>{warning}</p>)}
      {suggestions.map((suggestion) => (
        <article className="site-suggestion" key={`${suggestion.field_name}:${suggestion.source_url}`}>
          <h3>{fieldLabels[suggestion.field_name] ?? suggestion.field_name}</h3>
          <p><strong>Предложение:</strong> {label(suggestion.field_name, suggestion.value)}</p>
          <blockquote>{suggestion.evidence}</blockquote>
          <p className="hint small">
            <a href={suggestion.source_url} target="_blank" rel="noreferrer">{suggestion.source_title || "Страница-источник"}</a>
            {` · уверенность модели: ${suggestion.confidence >= 0.8 ? "высокая" : "средняя"} (проверьте самостоятельно)`}
          </p>
          <div className="suggestion-actions">
            <button type="button" className="btn btn-primary small" onClick={() => onDecide(suggestion, true)} disabled={busy || dirty}>Подтвердить</button>
            <button type="button" className="btn btn-ghost small" onClick={() => onDecide(suggestion, false)} disabled={busy || dirty}>Отклонить</button>
          </div>
        </article>
      ))}
    </section>
  );
}

export function ProfileSourcesPanel({ sources, profile, fieldLabels, label }: {
  sources: SiteSuggestion[]; profile: BusinessProfile; fieldLabels: Record<string, string>;
  label: (field: string, value: string | string[]) => string;
}) {
  // If an accepted value was edited manually, its original source no longer
  // proves the current value and should not appear as attribution.
  const current = sources.filter((source) => (
    JSON.stringify((profile as unknown as Record<string, unknown>)[source.field_name])
    === JSON.stringify(source.value)
  ));
  if (!current.length) return null;
  return (
    <section className="card site-provenance">
      <details>
        <summary>Подтвержденные данные с сайта · {current.length}</summary>
        <p className="hint small">Эти значения были ранее приняты вами. Источники показываются только пока значение поля совпадает с подтверждённым.</p>
        <ul className="site-provenance-list">
          {current.map((source) => (
            <li key={source.field_name + source.source_url}>
              <strong>{fieldLabels[source.field_name] ?? source.field_name}</strong>
              <span>{label(source.field_name, source.value)}</span>
              <small><a href={source.source_url} target="_blank" rel="noreferrer">
                {source.source_title || "Открыть источник"}
              </a> · {formatDateTime(source.extracted_at)}</small>
              <blockquote>{source.evidence}</blockquote>
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
