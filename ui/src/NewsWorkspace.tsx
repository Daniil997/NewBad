import { useState, type ReactNode } from "react";
import { formatDay } from "./realTypes";
import "./news.css";

export type NewsRow = {
  id: string; title: string; date: string; category?: string | null;
  source?: string | null; excerpt?: string;
  score?: number | null;
  relevance?: { label: string; level: "high" | "mid" | "low" } | null;
};

export function NewsPageHeader({ company, context, actions, children }: {
  company: string; context: string; actions: ReactNode; children?: ReactNode;
}) {
  return <div className="news-page-heading">
    <p className="news-breadcrumb">Альфа-Бизнес <span>/</span> Новости и аналитика</p>
    <div className="real-head">
      <div><p className="eyebrow">Новости для бизнеса</p>
        <h1>Что важно для вашей компании</h1>
        <p className="news-page-description">События, их влияние на ваш бизнес и возможные следующие шаги.</p>
      </div>
      <div className="real-head-actions">{actions}</div>
    </div>
    <div className="news-company-context">
      <span className="news-company-avatar" aria-hidden="true">{company.trim().charAt(0).toLocaleUpperCase("ru") || "К"}</span>
      <div><strong>{company || "Ваша компания"}</strong><p>{context}</p></div>
      {children}
    </div>
  </div>;
}

export function NewsFeedPanel({ personal, onPersonal, personalCount, totalCount, query, onQuery,
  category, onCategory, categories, period, onPeriod, periodLabel, onRefresh, refreshing,
  loading, error, status, items, selectedId, onSelect, emptyText,
}: {
  personal: boolean; onPersonal: (personal: boolean) => void; personalCount: number; totalCount: number;
  query: string; onQuery: (value: string) => void; category: string; onCategory: (value: string) => void;
  categories: [string, string][]; period: string; onPeriod: (value: string) => void; periodLabel: string;
  onRefresh: () => void; refreshing: boolean; loading: boolean; error?: string; status?: ReactNode;
  items: NewsRow[]; selectedId: string; onSelect: (id: string) => void; emptyText: string;
}) {
  const [sort, setSort] = useState<"importance" | "newest" | "oldest">("importance");
  const filtered = Boolean(query.trim() || category !== "all" || period !== "window");
  const sortedItems = [...items].sort((left, right) => {
    if (sort === "importance") {
      const scoreDifference = (right.score ?? -1) - (left.score ?? -1);
      if (scoreDifference) return scoreDifference;
    }
    const leftDate = Date.parse(left.date);
    const rightDate = Date.parse(right.date);
    if (!Number.isFinite(leftDate)) return Number.isFinite(rightDate) ? 1 : 0;
    if (!Number.isFinite(rightDate)) return -1;
    return sort === "oldest" ? leftDate - rightDate : rightDate - leftDate;
  });
  function clear() { onQuery(""); onCategory("all"); onPeriod("window"); }
  return <section className="card feed-card" aria-label="Новости" aria-busy={loading} id="news-feed">
    <div className="news-feed-heading"><h2>Лента новостей</h2>
      <button type="button" className="news-refresh" onClick={onRefresh} disabled={refreshing || loading} aria-label="Обновить подборку" title="Обновить подборку">
        <span aria-hidden="true">↻</span><span>{refreshing ? "Обновляем…" : "Обновить"}</span>
      </button>
    </div>
    <div className="seg-tabs" role="group" aria-label="Какие новости показывать">
      <button type="button" aria-pressed={personal} onClick={() => onPersonal(true)}>Для вас <span className="count">{personalCount}</span></button>
      <button type="button" aria-pressed={!personal} onClick={() => onPersonal(false)}>Все новости <span className="count">{totalCount}</span></button>
    </div>
    <p className="news-feed-description">{personal ? "Отобраны с учётом профиля вашей компании" : "Все публикации из подключённых источников"}</p>
    <div className="news-search"><span aria-hidden="true">⌕</span>
      <input className="input" type="search" value={query} onChange={(event) => onQuery(event.target.value)} placeholder="Тема, новость или источник" aria-label="Поиск по новостям" />
    </div>
    <div className="filters-grid">
      <label className="field"><span className="field-label">Категория</span>
        <select className="input" value={category} onChange={(event) => onCategory(event.target.value)}>
          <option value="all">Все категории</option>{categories.map(([id, title]) => <option key={id} value={id}>{title}</option>)}
        </select>
      </label>
      <label className="field"><span className="field-label">Период</span>
        <select className="input" value={period} onChange={(event) => onPeriod(event.target.value)}>
          <option value="window">{periodLabel}</option><option value="day">Последний день</option><option value="three_days">Последние 3 дня</option>
        </select>
      </label>
    </div>
    <label className="field news-sort"><span className="field-label">Сортировка</span>
      <select className="input" value={sort} onChange={(event) => setSort(event.target.value as typeof sort)}>
        <option value="importance">По важности</option>
        <option value="newest">Сначала новые</option>
        <option value="oldest">Сначала старые</option>
      </select>
    </label>
    {status}
    {error ? <NewsState title="Лента не загрузилась" text={error} action="Повторить" onAction={onRefresh} /> : loading ? <NewsState loading title="Собираем вашу ленту" text="Загружаем новости и оценки связи с компанией." /> : <>
      <div className="news-results" role="status"><span>Показано: {items.length}</span>{filtered ? <button type="button" className="btn-link" onClick={clear}>Сбросить фильтры</button> : null}</div>
      {sortedItems.length ? <ul className="news-list">{sortedItems.map((item) => <li key={item.id}>
        <button type="button" className={`news-row${item.id === selectedId ? " active" : ""}`} aria-current={item.id === selectedId ? "true" : undefined} onClick={() => onSelect(item.id)}>
          <span className="news-row-meta"><span>{item.category || "Новость"}</span><time dateTime={item.date}>{formatDay(item.date)}</time></span>
          <span className="news-row-title">{item.title}</span>
          {item.excerpt ? <span className="news-row-excerpt">{item.excerpt.length > 180 ? `${item.excerpt.slice(0, 180).trimEnd()}…` : item.excerpt}</span> : null}
          <span className="news-row-foot"><span className="news-row-source">{item.source || "Источник не указан"}</span>{item.relevance ? <span className={`chip chip-${item.relevance.level}`}>{item.relevance.label}</span> : null}</span>
        </button>
      </li>)}</ul> : <NewsState title={filtered ? "Ничего не найдено" : "Здесь появятся новости"} text={filtered ? "Измените запрос или сбросьте фильтры, чтобы увидеть больше публикаций." : emptyText} action={filtered ? "Сбросить фильтры" : personal ? "Все новости" : undefined} onAction={filtered ? clear : () => onPersonal(false)} />}
    </>}
  </section>;
}

export function NewsState({ title, text, loading, action, onAction }: {
  title: string; text: string; loading?: boolean; action?: string; onAction?: () => void;
}) {
  return <div className={`news-state${loading ? " is-loading" : ""}`} role={loading ? "status" : undefined}>
    <span className="news-state-icon" aria-hidden="true">{loading ? "↻" : "≡"}</span>
    <h3>{title}</h3><p>{text}</p>
    {action ? <button type="button" className="btn btn-ghost" onClick={onAction}>{action}</button> : null}
  </div>;
}

export function NewsArticleHeading({ title, category, date, source, url }: {
  title: string; category?: string | null; date: string; source?: string | null; url?: string | null;
}) {
  return <header className="real-card-heading">
    <a className="news-back" href="#news-feed">← К ленте новостей</a>
    <p className="eyebrow">{category || "Новость"}{date ? ` · ${formatDay(date)}` : ""}</p>
    <h2 className="detail-title">{title}</h2>
    <p className="detail-source">{source}{url ? <> {source ? "· " : ""}<a href={url} target="_blank" rel="noreferrer">Читать первоисточник ↗</a></> : null}</p>
  </header>;
}

export function NewsRelevance({ label, level, children }: {
  label: string; level?: "high" | "mid" | "low"; children?: ReactNode;
}) {
  return <section className="news-relevance" aria-label="Оценка связи с компанией">
    <div className="real-block-heading"><h3>Почему это важно для вас</h3><span className={`chip chip-${level || "low"}`}>{label}</span></div>
    {children}
  </section>;
}

export function NewsAnalysis({ facts, meaning, risks, opportunities, nextStep, children }: {
  facts: ReactNode; meaning: ReactNode; risks?: ReactNode; opportunities?: ReactNode;
  nextStep?: ReactNode; children?: ReactNode;
}) {
  return <>
    <section className="detail-block real-card-fact">
      <div className="real-block-heading"><h3>Что произошло</h3><span className="real-layer-tag fact">Факт · первоисточник</span></div>
      <div className="news-facts">{facts}</div>
    </section>
    <section className="detail-block">
      <div className="real-block-heading"><h3>Что это значит для вас</h3><span className="real-layer-tag estimate">Аналитическая оценка</span></div>
      <div className="report real-report">{meaning}
        {risks || opportunities ? <div className="report-cols">
          <div className="report-col risk"><h4>Потенциальный риск</h4>{risks}</div>
          <div className="report-col chance"><h4>Возможность</h4>{opportunities}</div>
        </div> : null}
        {nextStep ? <section className="real-next-step"><h4>Что можно сделать</h4>{nextStep}</section> : null}
        {children}
      </div>
    </section>
  </>;
}

/** Compare calendar dates to a reference date; demo uses its latest fixture date. */
export function inNewsPeriod(date: string, period: string, reference?: string): boolean {
  if (period === "window") return true;
  const today = new Date();
  const localDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const published = Date.parse(date.slice(0, 10));
  const anchor = Date.parse((reference || localDate).slice(0, 10));
  const age = Math.round((anchor - published) / 86400000);
  return Number.isFinite(age) && age >= 0 && age < (period === "day" ? 1 : 3);
}
