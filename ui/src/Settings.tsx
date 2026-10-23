import { useEffect, useState } from "react";

type SettingsView = {
  mode: string; allow_cloud: boolean; active_generation: string; model: string;
  weights_from_browser: boolean; local_server: string; placement: string; placement_choice: string;
  news_lookback_days: number; news_retention_days: number; news_max_items: number;
  news_collection_interval_hours: number;
  market_retention_days: number; market_max_items_per_instrument: number;
  market_collection_interval_hours: number; market_categories: string[];
  personal_analysis_trigger: string; personal_analysis_interval_hours: number;
  personal_analysis_max_candidates: number;
  insight_refresh_mode: string; insight_refresh_interval_hours: number;
  insight_retention_days: number;
  task_routes: Record<string, Record<string, string>>;
};
type NewsStatus = {
  sources: { id: string; name: string; url: string }[];
  latest: null | {
    status: string; finished_at: string;
    sources: { source: string; status: string; imported: number; duplicates: number; rejected: number; error: string }[];
  };
  stored_count: number;
};
type MarketStatus = {
  provider: string; delayed: boolean; stored_count: number; counts: Record<string, number>;
  latest: null | { status: string; finished_at: string; categories: { category: string; status: string; fetched: number; missing: string[]; error: string }[] };
  items: { category: string; instrument: string; name: string; price: number; change_pct: number | null; currency: string; unit: string; source_timestamp: string; observed_at: string }[];
};
const MARKET_CATEGORY_LABELS: Record<string, string> = { currencies: "Валюты", equities: "Акции", metals: "Металлы" };

const MODES = ["mock", "local_only", "local_dual", "hybrid", "cloud_only", "replay"] as const;
const PLACEMENTS = ["auto", "gpu", "ram"] as const;
const MODE_LABELS: Record<string, string> = {
  mock: "Заглушка", local_only: "Локальная Gemma", local_dual: "Локальные Gemma и Qwen",
  hybrid: "Гибрид: облако с локальным резервом", cloud_only: "Облако", replay: "Повтор сохранённого ответа",
};
const MODE_DESCRIPTIONS: Record<string, string> = {
  mock: "Модель не вызывается: используются шаблонные ответы.",
  local_only: "Все стандартные вызовы модели идут на локальную Gemma.",
  local_dual: "Короткие задачи выполняет локальная Gemma, более тяжёлые — локальная Qwen.",
  hybrid: "Короткие задачи выполняет локальная Gemma; тяжёлые идут в облако с локальным резервом.",
  cloud_only: "Стандартные вызовы идут в облако. Нужны разрешение облака и ключ на сервере.",
  replay: "Возвращается сохранённый ответ из кэша; новый вызов модели не выполняется.",
};
const TASK_ROUTE_OPTIONS = [
  ["inherit", "Общий режим"], ["mock", "Заглушка (без вызова модели)"],
  ["local_gemma", "Локально: Gemma"], ["local_qwen", "Локально: Qwen"],
  ["cloud_gemma", "Облако: Gemma"], ["cloud_glm", "Облако: GLM Flash"],
  ["cloud_qwen", "Облако: Qwen"], ["cloud_deepseek", "Облако: DeepSeek Flash"],
  ["cloud_vl", "Облако: Qwen VL"], ["cloud_kimi", "Облако: Kimi"],
] as const;

export function Settings({ onBack }: { onBack: () => void }) {
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [mode, setMode] = useState("mock");
  const [allowCloud, setAllowCloud] = useState(false);
  const [placement, setPlacement] = useState("auto");
  const [newsLookbackDays, setNewsLookbackDays] = useState(3);
  const [newsRetentionDays, setNewsRetentionDays] = useState(90);
  const [newsMaxItems, setNewsMaxItems] = useState(500);
  const [newsIntervalHours, setNewsIntervalHours] = useState(0);
  const [newsStatus, setNewsStatus] = useState<NewsStatus | null>(null);
  const [marketRetentionDays, setMarketRetentionDays] = useState(90);
  const [marketMaxItems, setMarketMaxItems] = useState(1000);
  const [marketIntervalHours, setMarketIntervalHours] = useState(0);
  const [marketCategories, setMarketCategories] = useState(["currencies", "equities", "metals"]);
  const [marketStatus, setMarketStatus] = useState<MarketStatus | null>(null);
  const [analysisTrigger, setAnalysisTrigger] = useState("first_then_manual");
  const [analysisIntervalHours, setAnalysisIntervalHours] = useState(24);
  const [analysisMaxCandidates, setAnalysisMaxCandidates] = useState(30);
  const [insightRefreshMode, setInsightRefreshMode] = useState("manual");
  const [insightRefreshIntervalHours, setInsightRefreshIntervalHours] = useState(24);
  const [insightRetentionDays, setInsightRetentionDays] = useState(90);
  const [taskRoutes, setTaskRoutes] = useState<Record<string, Record<string, string>>>({
    real: { profile_site: "inherit", news_report: "inherit", chat: "inherit" },
    demo: { news_card: "inherit", chat: "inherit" },
  });
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [collectingMarket, setCollectingMarket] = useState(false);

  async function refreshNewsStatus() {
    const response = await fetch("/api/settings/news/status");
    if (!response.ok) throw new Error("Не удалось получить состояние сбора новостей");
    setNewsStatus((await response.json()) as NewsStatus);
  }
  async function refreshMarketStatus() {
    const response = await fetch("/api/settings/market/status");
    if (!response.ok) throw new Error("Не удалось получить состояние рыночных данных");
    setMarketStatus((await response.json()) as MarketStatus);
  }

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/settings", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Настройки: ${response.status}`);
        return response.json() as Promise<SettingsView>;
      })
      .then((payload) => {
        setSettings(payload);
        setMode(payload.mode);
        setAllowCloud(payload.allow_cloud);
        setPlacement(payload.placement_choice || "auto");
        setNewsLookbackDays(payload.news_lookback_days);
        setNewsRetentionDays(payload.news_retention_days);
        setNewsMaxItems(payload.news_max_items);
        setNewsIntervalHours(payload.news_collection_interval_hours);
        setMarketRetentionDays(payload.market_retention_days);
        setMarketMaxItems(payload.market_max_items_per_instrument);
        setMarketIntervalHours(payload.market_collection_interval_hours);
        setMarketCategories(payload.market_categories);
        setAnalysisTrigger(payload.personal_analysis_trigger);
        setAnalysisIntervalHours(payload.personal_analysis_interval_hours);
        setAnalysisMaxCandidates(payload.personal_analysis_max_candidates);
        setInsightRefreshMode(payload.insight_refresh_mode);
        setInsightRefreshIntervalHours(payload.insight_refresh_interval_hours);
        setInsightRetentionDays(payload.insight_retention_days);
        setTaskRoutes(payload.task_routes);
        return Promise.all([refreshNewsStatus(), refreshMarketStatus()]);
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Нет ответа от API");
      });
    return () => controller.abort();
  }, []);

  async function save() {
    setBusy(true);
    setError("");
    setSaved("");
    try {
      const response = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode, allow_cloud: allowCloud, placement,
          news_lookback_days: newsLookbackDays,
          news_retention_days: newsRetentionDays,
          news_max_items: newsMaxItems,
          news_collection_interval_hours: newsIntervalHours,
          market_retention_days: marketRetentionDays,
          market_max_items_per_instrument: marketMaxItems,
          market_collection_interval_hours: marketIntervalHours,
          market_categories: marketCategories,
          personal_analysis_trigger: analysisTrigger,
          personal_analysis_interval_hours: analysisIntervalHours,
          personal_analysis_max_candidates: analysisMaxCandidates,
          insight_refresh_mode: insightRefreshMode,
          insight_refresh_interval_hours: insightRefreshIntervalHours,
          insight_retention_days: insightRetentionDays,
          task_routes: taskRoutes,
        }),
      });
      const payload = (await response.json().catch(() => null)) as (SettingsView & { detail?: string }) | null;
      if (!response.ok || !payload || !payload.mode) {
        setError(payload?.detail || `Настройки: ${response.status}`);
        return;
      }
      setSettings(payload);
      await Promise.all([refreshNewsStatus(), refreshMarketStatus()]);
      setSaved("Сохранено. Общий режим, разрешение облака и размещение действуют в текущем процессе до перезапуска; маршруты задач и параметры сбора/хранения применяются без перезапуска.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Нет ответа от API");
    } finally {
      setBusy(false);
    }
  }

  async function collectMarket() {
    setCollectingMarket(true);
    setError("");
    setSaved("");
    try {
      const response = await fetch("/api/settings/market/collect", { method: "POST" });
      const payload = await response.json().catch(() => null) as { status?: string; detail?: string } | null;
      if (!response.ok || !payload) throw new Error(payload?.detail || "Не удалось собрать рыночные данные");
      await refreshMarketStatus();
      setSaved(payload.status === "error" ? "MOEX не вернула данные; подробности показаны ниже." : "Сбор рыночных данных завершён.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось собрать рыночные данные");
    } finally {
      setCollectingMarket(false);
    }
  }

  async function collectNews() {
    setCollecting(true);
    setError("");
    setSaved("");
    try {
      const response = await fetch("/api/settings/news/collect", { method: "POST" });
      const payload = await response.json().catch(() => null) as { status?: string; detail?: string } | null;
      if (!response.ok || !payload) throw new Error(payload?.detail || "Не удалось выполнить сбор новостей");
      await refreshNewsStatus();
      setSaved(payload.status === "error" ? "Все источники завершились ошибкой; подробности показаны ниже." : "Сбор завершён; импортированные новости доступны корпусу.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось выполнить сбор новостей");
    } finally {
      setCollecting(false);
    }
  }

  return (
    <main className="page">
      <button className="linkish" type="button" onClick={onBack}>На главную</button>
      <h1>Настройки разработки</h1>
      <p className="lead">
        Здесь настраивается текущий процесс сервера и фоновые задачи. Ключ API отсюда не задаётся.
      </p>
      <p className="hint">Изменения в разных разделах сохраняются одной кнопкой «Сохранить все настройки». Сбор новостей и рыночных данных запускается отдельно кнопками «Собрать сейчас».</p>
      {error ? <p className="error">{error}</p> : null}
      <form className="card settings-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <label>Общий режим модели по умолчанию
          <select value={mode} onChange={(event) => setMode(event.target.value)}>
            {MODES.map((item) => <option key={item} value={item}>{MODE_LABELS[item]}</option>)}
          </select>
        </label>
        <p className="hint">{MODE_DESCRIPTIONS[mode] || "Общий режим используется там, где не задан отдельный маршрут задачи."} Отдельные маршруты ниже могут переопределять его.</p>
        <label className="check">
          <input type="checkbox" checked={allowCloud} onChange={(event) => setAllowCloud(event.target.checked)} />
          Разрешить облачные вызовы (нужно для режимов hybrid/cloud_only и облачных маршрутов задач).
        </label>
        <label>Размещение локальной модели в Ollama
          <select value={placement} onChange={(event) => setPlacement(event.target.value)}>
            {PLACEMENTS.map((item) => <option key={item} value={item}>{item === "auto" ? "auto — по свободной памяти" : item === "gpu" ? "gpu — целиком на видеокарте" : "ram — часть модели в оперативной памяти"}</option>)}
          </select>
        </label>
        <p className="hint">Этот выбор применяется к локальному серверу Ollama (адрес обычно содержит порт 11434). Для llama-server на порту 8080 размещение задаётся при запуске; изменение здесь его не перезапускает.</p>
        {(mode === "hybrid" || mode === "cloud_only" || Object.values(taskRoutes).some((routes) => Object.values(routes).some((route) => route.startsWith("cloud_")))) && !allowCloud ? <p className="error">Выбран облачный режим или облачный маршрут задачи, но облачные вызовы запрещены. Включите разрешение облака или измените выбор модели.</p> : null}
        <h2>Модели по задачам и режимам</h2>
        <p className="hint">Можно выбрать только модель из разрешённого списка. Облачные варианты работают лишь при включённом облаке и настроенном ключе на сервере. Ключ здесь не вводится и в браузер не передаётся. «Общий режим» использует переключатель выше.</p>
        <fieldset>
          <legend>Реальный режим</legend>
          {([["profile_site", "Изучение сайта компании"], ["news_report", "Подробный разбор новости"], ["chat", "Чат по профилю"]] as const).map(([task, label]) => <label key={task}>{label}
            <select value={taskRoutes.real?.[task] || "inherit"} onChange={(event) => setTaskRoutes((routes) => ({ ...routes, real: { ...routes.real, [task]: event.target.value } }))}>
              {TASK_ROUTE_OPTIONS.map(([value, title]) => <option key={value} value={value}>{title}</option>)}
            </select>
          </label>)}
          <p className="hint">Персональная подборка новостей сейчас оценивается локальными правилами без модели; сбор RSS и котировок также не вызывает LLM.</p>
        </fieldset>
        <fieldset>
          <legend>Тестовый режим</legend>
          {([["news_card", "Формирование карточки новости"], ["chat", "Чат тестового режима"]] as const).map(([task, label]) => <label key={task}>{label}
            <select value={taskRoutes.demo?.[task] || "inherit"} onChange={(event) => setTaskRoutes((routes) => ({ ...routes, demo: { ...routes.demo, [task]: event.target.value } }))}>
              {TASK_ROUTE_OPTIONS.map(([value, title]) => <option key={value} value={value}>{title}</option>)}
            </select>
          </label>)}
        </fieldset>
        <h2>Сбор и хранение новостей</h2>
        <p className="hint">Используются официальные RSS-ленты. 0 часов означает только ручной запуск. Возраст новостей влияет и на сбор, и на актуальную ленту реального профиля.</p>
        <label>Учитывать новости не старше (дней)
          <input type="number" min={1} max={30} value={newsLookbackDays} onChange={(event) => setNewsLookbackDays(Number(event.target.value))} />
        </label>
        <label>Хранить новости (дней)
          <input type="number" min={1} max={3650} value={newsRetentionDays} onChange={(event) => setNewsRetentionDays(Number(event.target.value))} />
        </label>
        <label>Максимум хранимых новостей
          <input type="number" min={10} max={10000} value={newsMaxItems} onChange={(event) => setNewsMaxItems(Number(event.target.value))} />
        </label>
        <label>Автоматический сбор
          <select value={newsIntervalHours} onChange={(event) => setNewsIntervalHours(Number(event.target.value))}>
            <option value={0}>Только вручную</option>
            {[1, 3, 6, 12, 24].map((hours) => <option key={hours} value={hours}>Каждые {hours} ч.</option>)}
          </select>
        </label>
        <button type="submit" disabled={busy}>{busy ? "Сохраняем…" : "Сохранить все настройки"}</button>
        {saved ? <p>{saved}</p> : null}
      </form>
      <section className="card">
        <h2>Источники новостей</h2>
        {newsStatus ? <>
          <p>Сохранено новостей: {newsStatus.stored_count}</p>
          <ul>{newsStatus.sources.map((source) => <li key={source.id}><a href={source.url} target="_blank" rel="noreferrer">{source.name}</a></li>)}</ul>
          <button type="button" onClick={() => void collectNews()} disabled={collecting}>{collecting ? "Собираем…" : "Собрать сейчас"}</button>
          {newsStatus.latest ? <>
            <p>Последний запуск: {new Date(newsStatus.latest.finished_at).toLocaleString("ru-RU")} · {newsStatus.latest.status}</p>
            <ul>{newsStatus.latest.sources.map((item) => <li key={item.source}>{item.source}: {item.status}; новых {item.imported}, дублей {item.duplicates}, отклонено {item.rejected}{item.error ? ` (ошибка: ${item.error})` : ""}</li>)}</ul>
          </> : <p className="hint">Сбор ещё не запускался.</p>}
        </> : <button type="button" onClick={() => void refreshNewsStatus()}>Загрузить состояние</button>}
      </section>
      <section className="card settings-form">
        <h2>Курсы, акции и металлы</h2>
        <p className="hint">Единый источник: Московская биржа, API ISS. Бесплатные данные могут поступать с задержкой и не подходят для торговых решений.</p>
        <fieldset>
          <legend>Собирать категории</legend>
          {Object.entries(MARKET_CATEGORY_LABELS).map(([id, label]) => <label className="check" key={id}>
            <input type="checkbox" checked={marketCategories.includes(id)} disabled={marketCategories.length === 1 && marketCategories.includes(id)} onChange={(event) => setMarketCategories((current) => event.target.checked ? [...current, id] : current.filter((item) => item !== id))} />
            {label}
          </label>)}
        </fieldset>
        <label>Хранить рыночные наблюдения (дней)
          <input type="number" min={1} max={3650} value={marketRetentionDays} onChange={(event) => setMarketRetentionDays(Number(event.target.value))} />
        </label>
        <label>Максимум наблюдений на один инструмент
          <input type="number" min={10} max={10000} value={marketMaxItems} onChange={(event) => setMarketMaxItems(Number(event.target.value))} />
        </label>
        <label>Автоматический сбор
          <select value={marketIntervalHours} onChange={(event) => setMarketIntervalHours(Number(event.target.value))}>
            <option value={0}>Только вручную</option>
            {[1, 3, 6, 12, 24].map((hours) => <option key={hours} value={hours}>Каждые {hours} ч.</option>)}
          </select>
        </label>
      </section>
      <section className="card settings-form">
        <h2>Обзор компании</h2>
        <p className="hint">Сохранённый обзор собирается из уже оценённых новостей и рыночных данных, без отдельного вызова модели. Повторный вход использует сохранённый результат.</p>
        <label>Обновление обзора
          <select value={insightRefreshMode} onChange={(event) => setInsightRefreshMode(event.target.value)}>
            <option value="manual">Только вручную</option>
            <option value="interval">По интервалу</option>
          </select>
        </label>
        <label>Интервал обновления обзора
          <select disabled={insightRefreshMode !== "interval"} value={insightRefreshIntervalHours} onChange={(event) => setInsightRefreshIntervalHours(Number(event.target.value))}>
            {[1, 3, 6, 12, 24, 48, 72].map((hours) => <option key={hours} value={hours}>Каждые {hours} ч.</option>)}
          </select>
        </label>
        <label>Хранить сохранённые сводки (дней)
          <input type="number" min={1} max={3650} value={insightRetentionDays} onChange={(event) => setInsightRetentionDays(Number(event.target.value))} />
        </label>
        <p className="hint">Снимки старше срока удаляются из SQLite при сохранении настройки, открытии профиля и фоновой очистке раз в час. Подробные разборы новостей хранятся отдельно.</p>
      </section>
      <section className="card settings-form">
        <h2>Анализ новостей для профиля</h2>
        <p className="hint">Первая версия использует объяснимые правила и не вызывает модель. Оценки привязаны к версии профиля и материалу.</p>
        <label>Когда запускать анализ
          <select value={analysisTrigger} onChange={(event) => setAnalysisTrigger(event.target.value)}>
            <option value="first_then_manual">При первом входе, затем вручную</option>
            <option value="on_new_interval">При новых новостях с интервалом</option>
            <option value="manual">Только вручную</option>
          </select>
        </label>
        <label>Интервал автоматического анализа
          <select disabled={analysisTrigger !== "on_new_interval"} value={analysisIntervalHours} onChange={(event) => setAnalysisIntervalHours(Number(event.target.value))}>
            {[1, 3, 6, 12, 24, 48, 72].map((hours) => <option key={hours} value={hours}>Каждые {hours} ч.</option>)}
          </select>
        </label>
        <label>Максимум новостей за один запуск
          <input type="number" min={1} max={100} value={analysisMaxCandidates} onChange={(event) => setAnalysisMaxCandidates(Number(event.target.value))} />
        </label>
      </section>
      <section className="card">
        <h2>Рыночные данные MOEX</h2>
        <p><a href="https://www.moex.com/a2920" target="_blank" rel="noreferrer">Московская биржа — документация ISS</a></p>
        {marketStatus ? <>
          <p>Хранится инструментов с данными: {marketStatus.stored_count} · {Object.entries(marketStatus.counts).map(([key, value]) => `${MARKET_CATEGORY_LABELS[key] || key}: ${value}`).join("; ") || "пока нет"}</p>
          <button type="button" onClick={() => void collectMarket()} disabled={collectingMarket}>{collectingMarket ? "Запрашиваем MOEX…" : "Собрать сейчас"}</button>
          {marketStatus.latest ? <>
            <p>Последний запуск: {new Date(marketStatus.latest.finished_at).toLocaleString("ru-RU")} · {marketStatus.latest.status}</p>
            <ul>{marketStatus.latest.categories.map((item) => <li key={item.category}>{MARKET_CATEGORY_LABELS[item.category] || item.category}: {item.status}; получено {item.fetched}; отсутствуют {item.missing.join(", ") || "нет"}{item.error ? ` (${item.error})` : ""}</li>)}</ul>
          </> : <p className="hint">Сбор ещё не запускался.</p>}
          {marketStatus.items.length ? <ul>{marketStatus.items.map((item) => <li key={`${item.category}-${item.instrument}`}>{item.name} ({item.instrument}): {item.price} {item.currency}{item.change_pct !== null ? ` · ${item.change_pct > 0 ? "+" : ""}${item.change_pct}%` : ""} · по MOEX: {item.source_timestamp}; получено: {new Date(item.observed_at).toLocaleString("ru-RU")}</li>)}</ul> : null}
        </> : <button type="button" onClick={() => void refreshMarketStatus()}>Загрузить состояние</button>}
      </section>
      {settings ? <section className="card">
        <h2>Сейчас</h2>
        <dl>
          <div><dt>Режим</dt><dd>{MODE_LABELS[settings.mode] || settings.mode}</dd></div>
          <div><dt>Чем отвечаем</dt><dd>{settings.active_generation}</dd></div>
          <div><dt>Модель</dt><dd>{settings.model}</dd></div>
          <div><dt>Облако</dt><dd>{settings.allow_cloud ? "разрешено" : "выключено"}</dd></div>
          <div><dt>Локальный сервер модели</dt><dd>{settings.local_server === "up" ? "отвечает" : settings.local_server === "down" ? "не запущен" : "не используется"}</dd></div>
        </dl>
      </section> : null}
    </main>
  );
}
