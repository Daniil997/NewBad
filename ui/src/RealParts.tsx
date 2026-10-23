import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import {
  formatDateTime, formatDay, formatPrice, profileProgress, relevanceInfo,
  type BusinessProfile, type ChatMessage, type Interest, type MarketHistory, type ProfileOptions,
  type RealFeed, type RealMarkets, type RealNewsItem, type SiteSuggestion,
} from "./realTypes";

/* ───────────── Лента ───────────── */

export function NewsList({ items, selectedId, onSelect, emptyText }: {
  items: RealNewsItem[]; selectedId: string; onSelect: (id: string) => void; emptyText: string;
}) {
  if (!items.length) return <p className="empty">{emptyText}</p>;
  return (
    <ul className="news-list">
      {items.map((item) => {
        const rel = item.analysis?.relevant ? relevanceInfo(item.analysis.score) : null;
        const active = item.id === selectedId;
        return (
          <li key={item.id}>
            <button type="button" className={`news-row${active ? " active" : ""}`} aria-current={active ? "true" : undefined} onClick={() => onSelect(item.id)}>
              <span className="news-row-meta">{item.event_title ? `${item.event_title} · ` : ""}{formatDay(item.published_at)}</span>
              <span className="news-row-title">{item.title}</span>
              <span className="news-row-foot">
                <span className="news-row-source">{item.source}</span>
                {rel ? <span className={`chip chip-${rel.level}`}>{rel.label}</span> : null}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function isCurrencyRatesNews(item: RealNewsItem): boolean {
  return item.event_type.toLowerCase() === "fx"
    || /курсы? валют/i.test(item.title)
    // Некоторые источники не заполняют event_type и не упоминают валюты в заголовке.
    // Если в тексте есть строки с курсами, всё равно показываем структурированный блок.
    || parseCurrencyRates(item.excerpt).length > 0;
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

function CurrencyRatesPanel({ item }: { item: RealNewsItem }) {
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
  return (
    <article className="detail">
      <p className="eyebrow">{item.event_title || "Новость"} · {formatDay(item.published_at)}</p>
      <h2 className="detail-title">{item.title}</h2>
      <p className="detail-source">
        {item.source}
        {item.url ? <> · <a href={item.url} target="_blank" rel="noreferrer">Читать первоисточник ↗</a></> : null}
      </p>
      {isCurrencyRatesNews(item) ? <CurrencyRatesPanel item={item} /> : item.excerpt ? <p className="detail-excerpt">{item.excerpt}</p> : null}

      <section className="detail-block">
        <h3>Почему это важно для вас</h3>
        {!analysis ? (
          <p className="hint">Эта новость ещё не сопоставлена с профилем вашей компании. Нажмите «Обновить подборку» в списке слева.</p>
        ) : (
          <>
            {rel ? <p><span className={`chip chip-${rel.level}`}>{rel.label}</span></p>
              : <p className="hint">Прямой связи с профилем вашей компании не найдено.</p>}
            {analysis.reasons.length ? <ul className="reasons">{analysis.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : null}
            <details className="tech">
              <summary>Как это определено</summary>
              <p>Метод: {analysis.method}. Совпадение с профилем: {Math.round(analysis.score * 100)}%. Оценка от {formatDateTime(analysis.analyzed_at)}.</p>
            </details>
          </>
        )}
      </section>

      <section className="detail-block">
        <h3>Разбор для вашего бизнеса</h3>
        {report ? (
          <div className="report">
            <p className="report-summary">{report.summary}</p>
            <h4>Что это значит для вас</h4>
            <p>{report.relevance}</p>
            <div className="report-cols">
              <div className="report-col risk"><h4>Риски</h4><p>{report.risks}</p></div>
              <div className="report-col chance"><h4>Возможности</h4><p>{report.opportunities}</p></div>
            </div>
            {report.questions.length ? (
              <>
                <h4>Что стоит уточнить</h4>
                <ul className="reasons">{report.questions.map((question) => <li key={question}>{question}</li>)}</ul>
              </>
            ) : null}
            {report.citations.length ? (
              <p className="report-cites">Основание: {report.citations.map((citation, index) => (
                <span key={`${citation.url}-${index}`}>{index ? " · " : ""}
                  {citation.url ? <a href={citation.url} target="_blank" rel="noreferrer">{citation.title}</a> : citation.title}
                  {citation.published_at ? ` (${citation.published_at})` : ""}
                </span>
              ))}</p>
            ) : null}
            <p className="report-foot">
              Сохранён {formatDateTime(report.created_at)}.{" "}
              <button type="button" className="btn-link" onClick={() => onReport(true)} disabled={reportBusy}>{reportBusy ? "Обновляем…" : "Обновить разбор"}</button>
            </p>
          </div>
        ) : (
          <>
            <p className="hint">Коротко: что произошло, как это может коснуться вашей компании, какие риски и возможности.</p>
            <button type="button" className="btn btn-primary" onClick={() => onReport(false)} disabled={reportBusy}>
              {reportBusy ? "Готовим разбор…" : "Разобрать для моей компании"}
            </button>
          </>
        )}
      </section>

      <div className="detail-actions">
        <button type="button" className="btn btn-ghost" onClick={onChat} disabled={chatBusy}>
          {chatBusy ? "Открываем чат…" : "Спросить об этой новости"}
        </button>
      </div>
    </article>
  );
}

/* ───────────── Чат ───────────── */

export function ChatPanel({ sessionId, csrf, companyName, onBack, backLabel }: {
  sessionId: string; csrf: string; companyName: string; onBack: () => void; backLabel: string;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [title, setTitle] = useState("Общий чат по бизнесу");
  const [focused, setFocused] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMessages([]);
    setError("");
    void fetch(`/api/real/chat/sessions/${encodeURIComponent(sessionId)}`).then(async (response) => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Не удалось открыть диалог");
      if (!cancelled) {
        setMessages(data.messages as ChatMessage[]);
        setTitle(data.title || "Общий чат по бизнесу");
        setFocused(Boolean(data.focused_news_id));
      }
    }).catch((reason: unknown) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "Не удалось загрузить диалог");
    });
    return () => { cancelled = true; };
  }, [sessionId]);

  useEffect(() => {
    const node = listRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages.length, busy]);

  async function ask(raw: string) {
    const text = raw.trim();
    if (!text || busy) return;
    const optimistic: ChatMessage = { role: "user", text, citations: [], created_at: new Date().toISOString() };
    setBusy(true); setError(""); setQuestion("");
    setMessages((current) => [...current, optimistic]);
    try {
      const response = await fetch(`/api/real/chat/sessions/${encodeURIComponent(sessionId)}/messages`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ question: text }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Не удалось получить ответ");
      setMessages((current) => [...current, data as ChatMessage]);
    } catch (reason) {
      // Сервер не сохранил вопрос — убираем его из ленты и возвращаем в поле, чтобы не набирать заново.
      setMessages((current) => current.filter((message) => message !== optimistic));
      setQuestion(text);
      setError(reason instanceof Error ? reason.message : "Не удалось связаться с чатом");
    } finally { setBusy(false); }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void ask(question);
    }
  }

  const quick = focused
    ? ["Что это значит для нашей компании?", "Какие здесь риски?", "Что стоит сделать в ближайшее время?"]
    : ["Какие свежие новости важнее всего для нас?", "Что из новостей может затронуть наши поставки?"];

  return (
    <section className="chat-panel" aria-label="Чат по бизнесу">
      <button type="button" className="btn-link" onClick={onBack}>← {backLabel}</button>
      <h2 className="detail-title">{title}</h2>
      <p className="hint">
        Контекст: {companyName || "профиль пока не заполнен"}.{" "}
        {focused ? "Разговор строится вокруг выбранной новости." : "Можно спрашивать о любых свежих официальных новостях."}
      </p>
      <div className="chat-list" ref={listRef} aria-live="polite">
        {messages.length === 0 && !busy ? (
          <div className="chat-empty">
            <p className="hint">Задайте вопрос своими словами или выберите готовый:</p>
            <div className="chips">
              {quick.map((text) => <button key={text} type="button" className="chip-btn" onClick={() => void ask(text)}>{text}</button>)}
            </div>
          </div>
        ) : null}
        {messages.map((message, index) => (
          <article className={`chat-msg ${message.role}`} key={`${message.created_at}-${index}`}>
            <span className="who">{message.role === "user" ? "Вы" : "Помощник"}</span>
            <p>{message.text}</p>
            {message.citations.length ? (
              <p className="chat-cites">Материалы: {message.citations.map((citation, citationIndex) => (
                <span key={`${citation.url}-${citationIndex}`}>{citationIndex ? " · " : ""}
                  {citation.url ? <a href={citation.url} target="_blank" rel="noreferrer">{citation.title}</a> : `${citation.title} (данные профиля)`}
                  {citation.published_at ? ` (${citation.published_at})` : ""}
                </span>
              ))}</p>
            ) : null}
          </article>
        ))}
        {busy ? <article className="chat-msg assistant"><span className="who">Помощник</span><p className="hint">Готовим ответ…</p></article> : null}
      </div>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      <form className="chat-form" onSubmit={(event) => { event.preventDefault(); void ask(question); }}>
        <label htmlFor="real-chat-question" className="sr-only">Ваш вопрос</label>
        <textarea
          id="real-chat-question" rows={2} maxLength={2000} value={question}
          onChange={(event) => setQuestion(event.target.value)} onKeyDown={onKeyDown}
          placeholder="Например: какие новости могут затронуть наши поставки?"
        />
        <button type="submit" className="btn btn-primary" disabled={busy || !question.trim()}>{busy ? "Отвечаем…" : "Отправить"}</button>
      </form>
      <p className="hint small">Enter — отправить, Shift+Enter — новая строка. Ответ отделяет факт публикации от оценки для вашей компании.</p>
    </section>
  );
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

export function SuggestionsPanel({ suggestions, warnings, busy, dirty, label, fieldLabels, onDecide }: {
  suggestions: SiteSuggestion[]; warnings: string[]; busy: boolean; dirty: boolean;
  label: (field: string, value: string | string[]) => string;
  fieldLabels: Record<string, string>;
  onDecide: (suggestion: SiteSuggestion, accept: boolean) => void;
}) {
  if (!suggestions.length && !warnings.length) return null;
  return (
    <section className="card site-suggestions">
      <h2>Проверьте найденные данные</h2>
      <p className="hint">Это предложения модели по тексту вашего сайта. Они не попадут в профиль без вашего подтверждения.</p>
      {dirty ? <p className="hint">Сначала сохраните или отмените изменения профиля — тогда кнопки станут доступны.</p> : null}
      {warnings.map((warning, index) => <p className="site-warning" key={`${index}-${warning}`}>{warning}</p>)}
      {suggestions.map((suggestion) => (
        <article className="site-suggestion" key={`${suggestion.field_name}:${suggestion.source_url}`}>
          <h3>{fieldLabels[suggestion.field_name] ?? suggestion.field_name}</h3>
          <p><strong>Предложение:</strong> {label(suggestion.field_name, suggestion.value)}</p>
          <blockquote>{suggestion.evidence}</blockquote>
          <p className="hint small">
            <a href={suggestion.source_url} target="_blank" rel="noreferrer">{suggestion.source_title || "Страница-источник"}</a>
            {` · оценка модели: ${suggestion.confidence >= 0.8 ? "высокая" : "средняя"} (не гарантия точности)`}
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

/* ───────────── Профиль ───────────── */

type InterestKey =
  | "credit_interest" | "deposit_interest" | "currency_interest" | "export_import_interest"
  | "leasing_interest" | "guarantees_interest" | "factoring_interest" | "settlements_interest";

const INTERESTS: { key: InterestKey; label: string; hint: string }[] = [
  { key: "credit_interest", label: "Кредиты и ставки", hint: "Новости о ключевой ставке и условиях займов" },
  { key: "deposit_interest", label: "Депозиты и свободные деньги", hint: "Где выгодно держать остатки" },
  { key: "currency_interest", label: "Валюта и конвертация", hint: "Курсы и валютные операции" },
  { key: "export_import_interest", label: "Экспорт и импорт", hint: "Внешняя торговля, пошлины, логистика" },
  { key: "leasing_interest", label: "Лизинг", hint: "Техника и оборудование в лизинг" },
  { key: "guarantees_interest", label: "Банковские гарантии", hint: "Тендеры и госконтракты" },
  { key: "factoring_interest", label: "Факторинг", hint: "Деньги за отгрузку без ожидания оплаты" },
  { key: "settlements_interest", label: "Расчёты и зарплата", hint: "Платежи, зарплатные проекты" },
];

function Field({ label, hint, required, children }: { label: string; hint?: string; required?: boolean; children: React.ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}{required ? <em> *</em> : null}</span>
      {children}
      {hint ? <small className="field-hint">{hint}</small> : null}
    </label>
  );
}

function InterestField({ label, hint, value, onChange }: {
  label: string; hint: string; value: Interest; onChange: (value: Interest) => void;
}) {
  const options: [Interest, string][] = [["unknown", "Не знаю"], ["interested", "Интересно"], ["not_relevant", "Неактуально"]];
  return (
    <div className="interest">
      <div className="interest-text"><strong>{label}</strong><small>{hint}</small></div>
      <div className="segmented" role="radiogroup" aria-label={label}>
        {options.map(([option, text]) => (
          <button key={option} type="button" role="radio" aria-checked={value === option} className={value === option ? "active" : ""} onClick={() => onChange(option)}>{text}</button>
        ))}
      </div>
    </div>
  );
}

function TagPicker({ legend, tags, selected, onToggle }: {
  legend: string; tags: { id: string; label: string; description: string }[]; selected: string[];
  onToggle: (id: string, checked: boolean) => void;
}) {
  if (!tags.length) return null;
  return (
    <fieldset className="tag-picker">
      <legend>{legend}</legend>
      <div className="tag-grid">
        {tags.map((tag) => (
          <label className={`tag-toggle${selected.includes(tag.id) ? " on" : ""}`} key={tag.id}>
            <input type="checkbox" checked={selected.includes(tag.id)} onChange={(event) => onToggle(tag.id, event.target.checked)} />
            <span><strong>{tag.label}</strong><small>{tag.description}</small></span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export type ProfileFormProps = {
  profile: BusinessProfile; options: ProfileOptions; busy: boolean; dirty: boolean;
  siteConsent: boolean; siteBusy: boolean;
  onConsent: (value: boolean) => void; onAnalyzeSite: () => void;
  onChange: <K extends keyof BusinessProfile>(key: K, value: BusinessProfile[K]) => void;
  onToggleTag: (key: "industry_tags" | "focus_topics", id: string, checked: boolean) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
};

function parseGeographies(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

export function ProfileForm(props: ProfileFormProps) {
  const { profile, options, busy, dirty, siteConsent, siteBusy, onConsent, onAnalyzeSite, onChange, onToggleTag, onSubmit } = props;
  const progress = profileProgress(profile);
  const percent = Math.round((progress.done / progress.total) * 100);
  // Отдельный текст для поля «география»: иначе запятая стирается при вводе и второе значение не набрать.
  const [geoText, setGeoText] = useState(profile.market_geographies.join(", "));
  useEffect(() => {
    if (parseGeographies(geoText).join("|") !== profile.market_geographies.join("|")) {
      setGeoText(profile.market_geographies.join(", "));
    }
  }, [profile.market_geographies]);

  return (
    <form className="profile-flow" onSubmit={onSubmit}>
      <section className="card progress-card">
        <div className="progress-line">
          <strong>Заполнено {progress.done} из {progress.total}</strong>
          <span>{percent}%</span>
        </div>
        <div className="progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{ width: `${percent}%` }} /></div>
        <p className="hint small">
          {progress.missing.length ? `Ещё не указано: ${progress.missing.join(", ")}. ` : "Всё основное указано. "}
          Чем точнее профиль, тем точнее подборка новостей. Пустые поля остаются «неизвестными» — ничего страшного. Не указывайте номера счетов и точные суммы.
        </p>
      </section>

      <section className="card form-section">
        <h2><span className="step">1</span> О компании</h2>
        <div className="form-grid">
          <Field label="Название компании или бренда" required>
            <input className="input" maxLength={200} value={profile.company_name} onChange={(event) => onChange("company_name", event.target.value)} />
          </Field>
          <Field label="Сайт компании" hint="Можно подтянуть данные с сайта — см. ниже">
            <input className="input" type="text" inputMode="url" placeholder="https://example.ru" maxLength={500} value={profile.website} onChange={(event) => onChange("website", event.target.value)} />
          </Field>
          <Field label="Страна"><input className="input" maxLength={100} value={profile.country} onChange={(event) => onChange("country", event.target.value)} /></Field>
          <Field label="Регион"><input className="input" maxLength={150} value={profile.region} onChange={(event) => onChange("region", event.target.value)} /></Field>
          <Field label="Город"><input className="input" maxLength={150} value={profile.city} onChange={(event) => onChange("city", event.target.value)} /></Field>
        </div>
        <Field label="Кратко о бизнесе" hint="2–3 предложения: чем занимаетесь и для кого">
          <textarea className="input" maxLength={2000} rows={3} value={profile.description} onChange={(event) => onChange("description", event.target.value)} />
        </Field>
        <label className="check"><input type="checkbox" checked={profile.description_skipped} onChange={(event) => onChange("description_skipped", event.target.checked)} /><span>Пока пропустить описание</span></label>

        <div className="site-box">
          <strong>Заполнить по сайту</strong>
          <p className="hint small">Мы прочитаем публичные страницы сайта и предложим значения полей. Сначала сохраните профиль с адресом сайта.</p>
          <label className="check">
            <input type="checkbox" checked={siteConsent} onChange={(event) => onConsent(event.target.checked)} />
            <span>Согласен отправить текст публичных страниц выбранной модели для анализа. При облачном маршруте он будет передан внешнему API; закрытые данные отправлять не следует.</span>
          </label>
          <button type="button" className="btn btn-ghost" onClick={onAnalyzeSite} disabled={!profile.website || !siteConsent || dirty || siteBusy}>
            {siteBusy ? "Анализируем…" : "Подтянуть данные с сайта"}
          </button>
          {dirty && profile.website ? <small>Сохраните изменения профиля перед запуском анализа.</small> : null}
        </div>
      </section>

      <section className="card form-section">
        <h2><span className="step">2</span> Чем занимается компания</h2>
        <div className="form-grid">
          <Field label="Основная деятельность"><input className="input" maxLength={200} value={profile.industry} onChange={(event) => onChange("industry", event.target.value)} /></Field>
          <Field label="ОКВЭД" hint="Если знаете"><input className="input" maxLength={30} value={profile.okved} onChange={(event) => onChange("okved", event.target.value)} /></Field>
          <Field label="Размер компании"><input className="input" placeholder="Например: 11–50 сотрудников" maxLength={100} value={profile.size_band} onChange={(event) => onChange("size_band", event.target.value)} /></Field>
          <Field label="Этап бизнеса"><input className="input" placeholder="Например: рост" maxLength={100} value={profile.business_stage} onChange={(event) => onChange("business_stage", event.target.value)} /></Field>
          <Field label="Тип клиентов"><input className="input" maxLength={500} value={profile.customer_types} onChange={(event) => onChange("customer_types", event.target.value)} /></Field>
          <Field label="Модель бизнеса"><input className="input" maxLength={500} value={profile.business_model} onChange={(event) => onChange("business_model", event.target.value)} /></Field>
        </div>
        <Field label="Товары и услуги">
          <textarea className="input" maxLength={1500} rows={2} value={profile.products_services} onChange={(event) => onChange("products_services", event.target.value)} />
        </Field>
        <TagPicker legend="Отрасль (можно выбрать несколько)" tags={options.industry_tags} selected={profile.industry_tags} onToggle={(id, checked) => onToggleTag("industry_tags", id, checked)} />
      </section>

      <section className="card form-section">
        <h2><span className="step">3</span> Что вам важно</h2>
        <p className="hint">Отметьте, что актуально сейчас. От этого зависит, какие новости попадут в подборку «Для вас».</p>
        <div className="interest-list">
          {INTERESTS.map((item) => (
            <InterestField key={item.key} label={item.label} hint={item.hint} value={profile[item.key]} onChange={(value) => onChange(item.key, value)} />
          ))}
        </div>
        <TagPicker legend="Темы, за которыми следить" tags={options.focus_topics} selected={profile.focus_topics} onToggle={(id, checked) => onToggleTag("focus_topics", id, checked)} />
        <Field label="География рынков и поставок" hint="Через запятую: например, Китай, Турция, Казахстан">
          <input className="input" maxLength={1000} value={geoText} onChange={(event) => { setGeoText(event.target.value); onChange("market_geographies", parseGeographies(event.target.value)); }} />
        </Field>
        <Field label="От чего зависит бизнес" hint="Сырьё, энергия, поставщики, сезонность">
          <textarea className="input" maxLength={1000} rows={2} value={profile.dependencies} onChange={(event) => onChange("dependencies", event.target.value)} />
        </Field>
        <Field label="Внешние факторы, влияющие на бизнес">
          <textarea className="input" maxLength={1500} rows={2} value={profile.external_factors} onChange={(event) => onChange("external_factors", event.target.value)} />
        </Field>
        <Field label="Дополнительный контекст">
          <textarea className="input" maxLength={1500} rows={2} value={profile.context_note} onChange={(event) => onChange("context_note", event.target.value)} />
        </Field>
      </section>

      <div className="savebar">
        <span className={dirty ? "dirty" : ""}>{dirty ? "Есть несохранённые изменения" : "Все изменения сохранены"}</span>
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Сохраняем…" : "Сохранить профиль"}</button>
      </div>
    </form>
  );
}
