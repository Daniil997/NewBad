import { useEffect, useState } from "react";
import "./settings.css";
import { NewsRecommendations } from "./NewsRecommendations";

type SettingsData = {
  news_lookback_days: number; news_retention_days: number; news_max_items: number; news_collection_interval_hours: number;
  market_retention_days: number; market_max_items_per_instrument: number; market_collection_interval_hours: number; market_categories: string[];
  personal_analysis_trigger: string; personal_analysis_interval_hours: number; personal_analysis_max_candidates: number;
  insight_refresh_mode: string; insight_refresh_interval_hours: number; insight_retention_days: number;
  task_routes: Record<string, Record<string, string>>; validate_numeric_grounding: boolean;
};
type SettingsView = SettingsData & { mode: string; allow_cloud: boolean; local_server: string };
type SourceResult = {
  source_id?: string; source: string; status: string; imported: number; duplicates: number; rejected: number;
  seen?: number; article_fetches?: number; skipped_old?: number; skipped_future?: number; deferred_articles?: number; article_errors?: number; deferred_by_time?: number;
  error: string; error_message?: string; rejection_reasons?: Record<string, number>;
  rejected_examples?: { title: string; url: string; reason: string }[];
};
type NewsStatus = {
  sources: { id: string; name: string; url: string; description?: string; article_mode?: string }[];
  latest: null | { status: string; finished_at: string; sources: SourceResult[] };
  stored_count: number; recent_count?: number; collecting?: boolean; settings?: SettingsData;
};
type MarketStatus = {
  stored_count: number; counts: Record<string, number>;
  latest: null | { status: string; finished_at: string; categories: { category: string; status: string; fetched: number; missing: string[]; error: string }[] };
  items: { category: string; instrument: string; name: string; price: number; change_pct: number | null; currency: string; source_timestamp: string; observed_at: string }[];
};
const SECTIONS = [
  ["news", "Новости", "Источники и сбор"], ["models", "Модели", "Маршруты задач"],
  ["recommendations", "Рекомендации банка", "Оценки для компаний"],
  ["markets", "Рыночные данные", "Курсы, акции, металлы"], ["analysis", "Персонализация", "Лента и обзор компании"],
  ["maintenance", "Обслуживание", "Очистка и сброс"],
] as const;
type Section = typeof SECTIONS[number][0];
const CATEGORY_LABELS: Record<string, string> = { currencies: "Валюты", equities: "Акции", metals: "Металлы" };
const STATUS_LABELS: Record<string, string> = {
  imported: "Есть новые", duplicate: "Без новых", outside_window: "Вне периода", rejected: "Нет текста для импорта",
  empty: "Лента пуста", error: "Ошибка", complete: "Завершён", partial: "Частично", ok: "Получены данные",
};
const ROUTE_OPTIONS = [
  ["mock", "Без модели · заглушка"], ["local_gemma", "Локально · Gemma"], ["local_qwen", "Локально · Qwen"],
  ["cloud_gemma", "Облако · google/gemma-4-26b-a4b-it"], ["cloud_glm", "Облако · z-ai/glm-5.3-flash"],
  ["cloud_qwen", "Облако · qwen/qwen3.6-35b-a3b"], ["cloud_deepseek", "Облако · deepseek/deepseek-v4.1-flash"],
  ["cloud_vl", "Облако · qwen/qwen3-vl-30b-a3b-instruct"], ["cloud_kimi", "Облако · moonshotai/kimi-k2.6"],
];
function pickSettings(payload: SettingsView): SettingsData {
  const { mode: _mode, allow_cloud: _cloud, local_server: _local, ...rest } = payload;
  const keys = ["news_lookback_days", "news_retention_days", "news_max_items", "news_collection_interval_hours", "market_retention_days", "market_max_items_per_instrument", "market_collection_interval_hours", "market_categories", "personal_analysis_trigger", "personal_analysis_interval_hours", "personal_analysis_max_candidates", "insight_refresh_mode", "insight_refresh_interval_hours", "insight_retention_days", "task_routes", "validate_numeric_grounding"] as const;
  const result = Object.fromEntries(keys.map((key) => [key, rest[key]])) as SettingsData;
  result.validate_numeric_grounding ??= true;
  result.task_routes = Object.fromEntries(Object.entries(result.task_routes).map(([space, routes]) => [space,
    Object.fromEntries(Object.entries(routes).map(([task, route]) => [task, route === "inherit" ? "mock" : route])),
  ]));
  return result;
}
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload) {
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item: { msg?: string }) => item.msg || "Проверьте значения полей").join(". ") : `Нет ответа от сервера (${response.status})`;
    throw new Error(message);
  }
  return payload as T;
}
function dateTime(value?: string) {
  return value ? new Date(value).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "Ещё не запускался";
}
function Icon({ section }: { section: Section }) {
  const paths: Record<Section, string> = {
    news: "M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h4",
    recommendations: "M4 5h16v12H9l-5 4z M8 9h8 M8 13h5",
    models: "M8 8h8v8H8z M9 3v5 M15 3v5 M9 16v5 M15 16v5 M3 9h5 M3 15h5 M16 9h5 M16 15h5",
    markets: "M4 19h16 M6 15l4-5 4 3 5-8",
    analysis: "M4 6h16 M4 12h16 M4 18h16 M8 3v6 M16 9v6 M10 15v6",
    maintenance: "M14 5a5 5 0 0 0-6 6l-5 5 5 5 5-5a5 5 0 0 0 6-6l-4 3-3-3z",
  };
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[section]} /></svg>;
}
function Schedule({ value, onChange, disabled = false, extended = false }: { value: number; onChange: (value: number) => void; disabled?: boolean; extended?: boolean }) {
  return <select value={value} disabled={disabled} onChange={(event) => onChange(Number(event.target.value))}>
    {!extended && <option value={0}>Только вручную</option>}
    {[1, 3, 6, 12, 24, ...(extended ? [48, 72, 168] : [])].filter((v, i, a) => a.indexOf(v) === i).map((hours) => <option key={hours} value={hours}>Каждые {hours} ч.</option>)}
    {![0, 1, 3, 6, 12, 24, ...(extended ? [48, 72, 168] : [])].includes(value) && <option value={value}>Каждые {value} ч.</option>}
  </select>;
}

export function Settings({ onBack }: { onBack: () => void }) {
  const [section, setSection] = useState<Section>("news");
  const [values, setValues] = useState<SettingsData | null>(null);
  const [baseline, setBaseline] = useState<SettingsData | null>(null);
  const [server, setServer] = useState<SettingsView | null>(null);
  const [news, setNews] = useState<NewsStatus | null>(null);
  const [market, setMarket] = useState<MarketStatus | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");
  const [loading, setLoading] = useState(true);
  const [recommendationDirty, setRecommendationDirty] = useState(false);
  const [recommendationBusy, setRecommendationBusy] = useState(false);
  const dirty = JSON.stringify(values) !== JSON.stringify(baseline) || recommendationDirty;
  const locked = !!busy || loading || recommendationBusy;

  async function refreshNews() { const result = await request<NewsStatus>("/api/settings/news/status"); setNews(result); return result; }
  async function refreshMarket() { const result = await request<MarketStatus>("/api/settings/market/status"); setMarket(result); return result; }
  async function load(signal?: AbortSignal) {
    setLoading(true); setError("");
    const results = await Promise.allSettled([
      request<SettingsView>("/api/settings", { signal }),
      request<NewsStatus>("/api/settings/news/status", { signal }),
      request<MarketStatus>("/api/settings/market/status", { signal }),
    ]);
    if (signal?.aborted) return;
    const [settingsResult, newsResult, marketResult] = results;
    if (settingsResult.status === "fulfilled") {
      const data = pickSettings(settingsResult.value);
      setValues(data); setBaseline(data); setServer(settingsResult.value);
    }
    if (newsResult.status === "fulfilled") setNews(newsResult.value);
    if (marketResult.status === "fulfilled") setMarket(marketResult.value);
    const failures = results.filter((result) => result.status === "rejected");
    if (failures.length) setError(failures.map((result) => String(result.reason?.message || "Не удалось загрузить данные")).join(". "));
    setLoading(false);
  }
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, []);
  useEffect(() => {
    if (!news?.collecting || busy === "news") return;
    const timer = window.setInterval(() => { void refreshNews().catch((reason: Error) => setError(reason.message)); }, 4000);
    return () => window.clearInterval(timer);
  }, [news?.collecting, busy]);
  useEffect(() => {
    if (!dirty) return;
    const preventLeave = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", preventLeave);
    return () => window.removeEventListener("beforeunload", preventLeave);
  }, [dirty]);

  function update<K extends keyof SettingsData>(key: K, value: SettingsData[K]) {
    setValues((current) => current ? { ...current, [key]: value } : current);
    setNotice("");
  }
  async function persist(): Promise<boolean> {
    if (!values) return false;
    if (values.news_retention_days < values.news_lookback_days) {
      setSection("news");
      setError("Срок хранения новостей должен быть не меньше периода сбора.");
      return false;
    }
    setBusy("save"); setError(""); setNotice("");
    try {
      const data = pickSettings(await request<SettingsView>("/api/settings", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values),
      }));
      setValues(data); setBaseline(data);
      await Promise.all([refreshNews(), refreshMarket()]);
      setNotice("Настройки сохранены и применены.");
      return true;
    } catch (reason) { setError((reason as Error).message); return false; }
    finally { setBusy(""); }
  }
  async function collect(kind: "news" | "market") {
    if (dirty && !await persist()) return;
    setBusy(kind); setError(""); setNotice("");
    try {
      const result = await request<{ status: string; sources?: SourceResult[]; retention?: { removed_by_age: number; removed_by_count: number } }>(`/api/settings/${kind}/collect`, { method: "POST" });
      if (kind === "news") {
        await refreshNews();
        const added = result.sources?.reduce((sum, item) => sum + item.imported, 0) ?? 0;
        const removed = (result.retention?.removed_by_age ?? 0) + (result.retention?.removed_by_count ?? 0);
        if (result.status === "error") setError("Источники недоступны. Подробности — в отчёте по источникам.");
        else setNotice(`Сбор завершён. Новых публикаций: ${added}.${removed ? ` По лимитам хранения удалено: ${removed}.` : ""}${result.status === "partial" ? " Часть источников недоступна — см. отчёт." : ""}`);
      } else {
        await refreshMarket();
        if (result.status === "error") setError("MOEX не вернула данные. Подробности — в отчёте.");
        else setNotice("Рыночные данные обновлены.");
      }
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }
  async function maintenance(kind: "news" | "demo") {
    const question = kind === "news" ? "Удалить все собранные новости? Профили компаний и рыночные данные сохранятся." : "Сбросить результаты тестового режима: чаты, карточки, кэш и историю запусков?";
    if (!window.confirm(question)) return;
    setBusy(kind === "news" ? "clear" : "reset"); setError(""); setNotice("");
    try {
      const result = await request<{ removed?: number }>(kind === "news" ? "/api/settings/news/clear" : "/api/settings/demo/reset", { method: "POST" });
      if (kind === "news") await refreshNews();
      setNotice(`Готово. Удалено записей: ${result.removed ?? 0}.`);
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }
  function back() { if (!recommendationBusy && (!dirty || window.confirm("Есть несохранённые изменения. Выйти без сохранения?"))) onBack(); }
  function changeSection(next: Section) {
    if (next === section || recommendationBusy) return;
    if (recommendationDirty && !window.confirm("Есть несохранённая рекомендация. Перейти в другой раздел без сохранения?")) return;
    setRecommendationDirty(false); setSection(next); setNotice("");
  }
  const totals = (news?.latest?.sources ?? []).reduce((sum, item) => ({
    seen: sum.seen + (item.seen ?? 0), imported: sum.imported + item.imported,
    duplicates: sum.duplicates + item.duplicates, old: sum.old + (item.skipped_old ?? 0), rejected: sum.rejected + item.rejected,
    deferred: sum.deferred + (item.deferred_articles ?? 0),
  }), { seen: 0, imported: 0, duplicates: 0, old: 0, rejected: 0, deferred: 0 });
  const title = SECTIONS.find(([id]) => id === section)!;

  return <main className="dev-page">
    <header className="dev-header">
      <button className="dev-back" type="button" onClick={back}>← На главную</button>
      <div className="dev-heading"><div><p className="dev-eyebrow">Рабочее пространство / Разработка</p><h1>Настройки разработчика</h1><p>Управляйте источниками данных, моделями и персонализацией.</p></div><span className="dev-environment">DEV</span></div>
    </header>
    <div className="dev-layout">
      <nav className="dev-nav" aria-label="Разделы настроек">
        <p className="dev-nav-caption">Настройки</p>
        {SECTIONS.map(([id, label, hint]) => <button type="button" key={id} disabled={recommendationBusy} className={section === id ? "is-active" : ""} aria-current={section === id ? "page" : undefined} onClick={() => changeSection(id)}><Icon section={id} /><span><strong>{label}</strong><small>{hint}</small></span>{id === "news" && news && <b>{news.stored_count}</b>}</button>)}
        <div className="dev-nav-note"><span className={`dev-dot ${server?.allow_cloud ? "is-on" : ""}`} />{server ? server.allow_cloud ? "Облако разрешено" : "Облако выключено" : "Подключаемся к API…"}<small>Сбор RSS работает без модели</small></div>
      </nav>
      <div className="dev-content">
        <div className="dev-section-heading"><div><p className="dev-eyebrow">{title[2]}</p><h2>{title[1]}</h2></div><span className="dev-badge">{dirty ? "Есть изменения" : loading ? "Загрузка…" : "Сохранено"}</span></div>
        {error && <div className="dev-alert is-error" role="alert">{error}{!values && !loading && <button type="button" className="dev-button is-secondary" onClick={() => void load()}>Повторить загрузку</button>}</div>}
        {notice && <div className="dev-alert is-success" role="status">{notice}</div>}
        {loading && <div className="dev-card dev-loading" role="status">Загружаем настройки и состояние источников…</div>}
        {section === "recommendations" && <NewsRecommendations onDirtyChange={setRecommendationDirty} onBusyChange={setRecommendationBusy} />}
        {values && section !== "recommendations" && <form className="dev-form" onSubmit={(event) => { event.preventDefault(); void persist(); }}>
          <fieldset className="dev-controls" disabled={locked}>
            {section === "news" && <>
              <section className="dev-card">
                <div className="dev-card-heading"><div><h3>Сбор новостей</h3><p>Публикации регуляторов и деловых СМИ в общей базе.</p></div><span className="dev-badge">{news?.sources.length ?? "—"} источников</span></div>
                <div className="dev-stats"><div><strong>{news?.stored_count ?? "—"}</strong><span>всего в базе</span></div><div><strong>{news?.recent_count ?? "—"}</strong><span>в сохранённом периоде</span></div><div><strong>{news?.latest ? totals.imported : "—"}</strong><span>новых за последний сбор</span></div></div>
                <div className="dev-collection-bar"><div><span className="dev-label">Последний запуск</span><strong>{dateTime(news?.latest?.finished_at)}</strong>{news?.latest && <small>{STATUS_LABELS[news.latest.status] || news.latest.status}</small>}</div><button className="dev-button" type="button" disabled={locked || news?.collecting} onClick={() => void collect("news")}>{busy === "news" || news?.collecting ? "Собираем публикации…" : dirty ? "Сохранить и собрать" : "Собрать сейчас"}<span aria-hidden="true">↗</span></button></div>
                {(busy === "news" || news?.collecting) && <p className="dev-callout" role="status">Читаем ленты и страницы публикаций. Сбор может занять несколько минут; результаты появятся после завершения.</p>}
              </section>
              <section className="dev-card"><div className="dev-card-heading"><div><h3>Период и хранение</h3><p>Период определяет, какие публикации загружать и показывать в актуальной ленте.</p></div></div>
                <div className="dev-field"><label htmlFor="news-window">Период сбора</label><div className="dev-presets">{[3, 7, 14, 30].map((days) => <button type="button" key={days} aria-pressed={values.news_lookback_days === days} onClick={() => update("news_lookback_days", days)}>{days} {days === 3 ? "дня" : "дней"}</button>)}</div><div className="dev-inline-input"><input id="news-window" required type="number" min={1} max={30} value={values.news_lookback_days} onChange={(e) => update("news_lookback_days", Number(e.target.value))} /><span>дней · от 1 до 30</span></div></div>
                {values.news_lookback_days <= 3 && <p className="dev-callout">Короткое окно: за выходные или несколько дней регуляторы публикуют мало материалов. Для более широкой подборки выберите 14 или 30 дней.</p>}
                <div className="dev-fields"><label>Автоматический сбор<Schedule value={values.news_collection_interval_hours} onChange={(v) => update("news_collection_interval_hours", v)} /><small>По расписанию сервера</small></label><label>Срок хранения, дней<input required type="number" min={values.news_lookback_days} max={3650} value={values.news_retention_days} onChange={(e) => update("news_retention_days", Number(e.target.value))} /><small>Не меньше периода сбора</small></label><label>Максимум новостей в базе<input required type="number" min={10} max={10000} value={values.news_max_items} onChange={(e) => update("news_max_items", Number(e.target.value))} /><small>Общий лимит для всех источников</small></label></div>
                {values.news_retention_days < values.news_lookback_days && <p className="dev-inline-error" role="alert">Увеличьте срок хранения до {values.news_lookback_days} дней или больше.</p>}
                <p className="dev-footnote">RSS содержит ограниченный архив: увеличение периода не добавит публикации, которых источник уже не отдаёт. Повторный сбор добавляет новые материалы и обновляет тексты без очистки базы.</p>
              </section>
              <section className="dev-card"><div className="dev-card-heading"><div><h3>Источники и результаты</h3><p>Отчёт последнего запуска: от записей в ленте до сохранённых публикаций.</p></div><button type="button" className="dev-text-button" disabled={locked} onClick={() => void refreshNews().catch((reason: Error) => setError(reason.message))}>Обновить статус</button></div>
                {news?.latest && <div className="dev-funnel" aria-label="Итоги сбора"><span><b>{totals.seen}</b> в RSS</span><span><b>{totals.imported}</b> новых</span><span><b>{totals.duplicates}</b> дублей</span><span><b>{totals.old}</b> вне периода</span><span><b>{totals.rejected}</b> отклонено</span></div>}
                {!!totals.deferred && <p className="dev-callout">Для {totals.deferred} записей достигнут лимит количества или времени загрузки страниц. Новые материалы получают приоритет перед повторной загрузкой сохранённых.</p>}
                <div className="dev-sources">{news?.sources.map((source) => {
                  const result = news.latest?.sources.find((item) => item.source_id === source.id || item.source === source.name);
                  return <details className="dev-source" key={source.id}><summary><span className="dev-source-icon" aria-hidden="true">{source.id.startsWith("cbr") ? "ЦБ" : source.id.startsWith("fns") ? "ФН" : "В"}</span><span className="dev-source-name"><strong>{source.name}</strong><small>{source.article_mode === "html" ? "RSS + текст публикации" : "Текст из RSS"}</small></span><span className={`dev-source-result ${result?.status === "error" ? "is-error" : ""}`}>{result ? `${result.imported} новых` : "Нет запусков"}<small>{result ? STATUS_LABELS[result.status] || result.status : "Ожидает сбора"}</small></span><span className="dev-chevron" aria-hidden="true">⌄</span></summary><div className="dev-source-details"><p>{source.description}</p><a href={source.url} target="_blank" rel="noreferrer">Открыть RSS источника ↗</a>{result && <><dl className="dev-source-metrics">{[["Записей в RSS", result.seen ?? 0], ["Загрузок страниц", result.article_fetches ?? 0], ["Дубли", result.duplicates], ["Вне периода", result.skipped_old ?? 0], ["Отклонено", result.rejected], ["Ошибки страниц", result.article_errors ?? 0], ["Будущая дата", result.skipped_future ?? 0], ["Отложено", result.deferred_articles ?? 0], ["Из них по времени", result.deferred_by_time ?? 0]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{result.error && <p className="dev-inline-error">{result.error_message || ({ timeout: "Источник не ответил вовремя", rate: "Повторный запрос слишком скоро. Подождите минуту.", source: "Не удалось прочитать ленту" }[result.error] || result.error)}</p>}{result.rejection_reasons && Object.keys(result.rejection_reasons).length > 0 && <div className="dev-reasons"><strong>Почему записи не загружены</strong><ul>{Object.entries(result.rejection_reasons).sort((a, b) => b[1] - a[1]).map(([reason, count]) => <li key={reason}>{reason}<b>{count}</b></li>)}</ul></div>}{!!result.rejected_examples?.length && <ul className="dev-rejected">{result.rejected_examples.map((example, i) => <li key={i}>{example.url ? <a href={example.url} target="_blank" rel="noreferrer">{example.title || "Без заголовка"}</a> : example.title || "Без заголовка"}<small>{example.reason}</small></li>)}</ul>}</>}</div></details>;
                })}</div>
                {!news && <p className="dev-footnote">Состояние источников не загружено. Нажмите «Обновить статус».</p>}
                {news && !news.latest && <p className="dev-footnote">Запустите первый сбор, чтобы увидеть статистику по каждому источнику.</p>}
                {!!totals.rejected && <p className="dev-footnote">Некоторые ленты содержат ссылки на документы и разделы без текста. Такие записи пропускаются; заголовок не заменяет содержание.</p>}
              </section>
            </>}
            {section === "models" && <>
              <section className="dev-card"><div className="dev-card-heading"><div><h3>Модели по задачам</h3><p>Отдельный маршрут для каждой задачи и режима приложения.</p></div></div><p className="dev-callout">{server?.allow_cloud ? "Облачные маршруты разрешены сервером. Для вызова нужен настроенный на сервере ключ." : "Облако выключено на сервере. Выбранный облачный маршрут не сможет выполнить запрос."} Локальный сервер: {server?.local_server === "up" ? "доступен" : "недоступен"}.</p>
                {([ ["real", "Реальный режим", [["profile_site", "Изучение сайта компании"], ["news_report", "Подробный разбор новости"], ["news_selection", "Персональная подборка"], ["chat", "Чат по профилю"]]], ["demo", "Тестовый режим", [["news_card_fact", "Извлечение факта"], ["news_card_copy", "Анализ и выводы"], ["chat", "Чат тестового режима"]]] ] as const).map(([space, label, tasks]) => <div className="dev-route-group" key={space}><h4>{label}</h4>{tasks.map(([task, taskLabel]) => <label className="dev-route-row" key={task}><span>{taskLabel}</span><select value={values.task_routes[space]?.[task] || "mock"} onChange={(e) => update("task_routes", { ...values.task_routes, [space]: { ...values.task_routes[space], [task]: e.target.value } })}>{ROUTE_OPTIONS.map(([value, name]) => <option value={value} key={value}>{name}</option>)}</select></label>)}</div>)}
                <p className="dev-footnote">Ключ API и разрешение облака задаются на сервере. Сбор RSS и котировок не вызывает модель.</p>
              </section>
              <section className="dev-card"><h3>Проверка ответов</h3><label className="dev-toggle"><span><strong>Подтверждение чисел в чате</strong><small>Отклонять ответы, если числа не подтверждены указанными материалами.</small></span><input type="checkbox" checked={values.validate_numeric_grounding} onChange={(e) => update("validate_numeric_grounding", e.target.checked)} /></label></section>
            </>}
            {section === "markets" && <>
              <section className="dev-card"><div className="dev-card-heading"><div><h3>Рыночные данные</h3><p>Московская биржа · ISS. Данные могут поступать с задержкой.</p></div><span className="dev-badge">{market?.stored_count ?? "—"} инструментов</span></div><div className="dev-collection-bar"><div><span className="dev-label">Последний запуск</span><strong>{dateTime(market?.latest?.finished_at)}</strong></div><button className="dev-button" type="button" disabled={locked} onClick={() => void collect("market")}>{busy === "market" ? "Запрашиваем MOEX…" : dirty ? "Сохранить и собрать" : "Собрать сейчас"}</button></div><div className="dev-category-options">{Object.entries(CATEGORY_LABELS).map(([id, label]) => <label key={id}><input type="checkbox" checked={values.market_categories.includes(id)} disabled={values.market_categories.length === 1 && values.market_categories.includes(id)} onChange={(e) => update("market_categories", e.target.checked ? [...values.market_categories, id] : values.market_categories.filter((item) => item !== id))} />{label}</label>)}</div><div className="dev-fields"><label>Автоматический сбор<Schedule value={values.market_collection_interval_hours} onChange={(v) => update("market_collection_interval_hours", v)} /></label><label>Срок хранения, дней<input required type="number" min={1} max={3650} value={values.market_retention_days} onChange={(e) => update("market_retention_days", Number(e.target.value))} /></label><label>Точек на инструмент<input required type="number" min={10} max={10000} value={values.market_max_items_per_instrument} onChange={(e) => update("market_max_items_per_instrument", Number(e.target.value))} /></label></div>{market?.latest && <div className="dev-market-results">{market.latest.categories.map((item) => <div key={item.category}><strong>{CATEGORY_LABELS[item.category] || item.category}</strong><span>{STATUS_LABELS[item.status] || item.status} · {item.fetched} значений</span>{item.error && <small className="dev-inline-error">{item.error}</small>}{item.missing.length > 0 && <small>Нет данных: {item.missing.join(", ")}</small>}</div>)}</div>}</section>
              {!!market?.items.length && <section className="dev-card"><h3>Последние значения</h3><div className="dev-table-wrap"><table className="dev-table"><thead><tr><th>Инструмент</th><th>Значение</th><th>Изменение</th><th>По данным MOEX</th></tr></thead><tbody>{market.items.map((item) => <tr key={`${item.category}-${item.instrument}`}><td><strong>{item.name}</strong><small>{item.instrument}</small></td><td>{item.price.toLocaleString("ru-RU")} {item.currency}</td><td>{item.change_pct === null ? "—" : `${item.change_pct > 0 ? "+" : ""}${item.change_pct}%`}</td><td>{item.source_timestamp}<small>Получено {dateTime(item.observed_at)}</small></td></tr>)}</tbody></table></div></section>}
            </>}
            {section === "analysis" && <>
              <section className="dev-card"><h3>Персональная новостная лента</h3><p className="dev-description">Оценки используют профиль компании и выбранный маршрут модели. При недоступности модели применяются правила. Анализ запускается при открытии ленты или вручную.</p><div className="dev-fields is-two"><label className="dev-full-width">Когда анализировать новости<select value={values.personal_analysis_trigger} onChange={(e) => update("personal_analysis_trigger", e.target.value)}><option value="first_then_manual">При первом открытии, затем вручную</option><option value="on_new_interval">При открытии, если есть новые новости и прошёл интервал</option><option value="manual">Только вручную</option></select></label><label>Интервал анализа<Schedule extended disabled={values.personal_analysis_trigger !== "on_new_interval"} value={values.personal_analysis_interval_hours} onChange={(v) => update("personal_analysis_interval_hours", v)} /></label><label>Новостей за один запуск<input required type="number" min={1} max={100} value={values.personal_analysis_max_candidates} onChange={(e) => update("personal_analysis_max_candidates", Number(e.target.value))} /><small>Лимит анализа, а не сбора RSS</small></label></div></section>
              <section className="dev-card"><h3>Обзор компании</h3><p className="dev-description">Обзор собирается из сохранённых оценок новостей и рыночных данных. Интервал проверяется при открытии профиля.</p><div className="dev-fields is-two"><label className="dev-full-width">Когда обновлять обзор<select value={values.insight_refresh_mode} onChange={(e) => update("insight_refresh_mode", e.target.value)}><option value="manual">Только вручную</option><option value="interval">При открытии, если прошёл интервал</option></select></label><label>Интервал обновления<Schedule extended disabled={values.insight_refresh_mode !== "interval"} value={values.insight_refresh_interval_hours} onChange={(v) => update("insight_refresh_interval_hours", v)} /></label><label>Хранить сводки, дней<input required type="number" min={1} max={3650} value={values.insight_retention_days} onChange={(e) => update("insight_retention_days", Number(e.target.value))} /></label></div></section>
            </>}
            {section === "maintenance" && <section className="dev-card dev-maintenance"><h3>Управление данными</h3><p className="dev-description">Для повторной проверки сценариев. Удаление потребует подтверждения.</p><div><h4>Очистить собранные новости</h4><p>Удалить публикации из общей базы. Следующий сбор загрузит доступные материалы заново.</p><button type="button" className="dev-button is-danger" disabled={locked || news?.collecting} onClick={() => void maintenance("news")}>{busy === "clear" ? "Очищаем…" : "Очистить базу новостей"}</button></div><div><h4>Сбросить тестовый режим</h4><p>Удалить чаты, сгенерированные карточки, кэш и историю запусков. Встроенные компании и исходные материалы сохраняются.</p><button type="button" className="dev-button is-danger" disabled={locked} onClick={() => void maintenance("demo")}>{busy === "reset" ? "Сбрасываем…" : "Сбросить тестовые данные"}</button></div></section>}
          </fieldset>
          <footer className="dev-save-bar"><div><span className={`dev-dot ${dirty ? "is-dirty" : "is-on"}`} /><span>{dirty ? "Есть несохранённые изменения" : "Все изменения сохранены"}<small>Настройки всех разделов</small></span></div><div><button className="dev-button is-secondary" type="button" disabled={!dirty || locked} onClick={() => { setValues(baseline); setNotice(""); setError(""); }}>Отменить</button><button className="dev-button" type="submit" disabled={!dirty || locked}>{busy === "save" ? "Сохраняем…" : "Сохранить настройки"}</button></div></footer>
        </form>}
      </div>
    </div>
  </main>;
}
