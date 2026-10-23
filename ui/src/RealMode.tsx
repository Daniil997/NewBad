import { useEffect, useState, type FormEvent } from "react";
import { ModeSwitch, type AppMode } from "./ModeSwitch";

type User = { id: string; login: string; created_at: string };
type Interest = "interested" | "not_relevant" | "unknown";
type BusinessProfile = {
  company_name: string; website: string; country: string; region: string; city: string;
  description: string; description_skipped: boolean; industry: string; okved: string; industry_tags: string[];
  products_services: string; customer_types: string; business_model: string;
  size_band: string; business_stage: string; credit_interest: Interest;
  deposit_interest: Interest; currency_interest: Interest; export_import_interest: Interest;
  leasing_interest: Interest; guarantees_interest: Interest; factoring_interest: Interest;
  settlements_interest: Interest; market_geographies: string[]; dependencies: string;
  focus_topics: string[]; external_factors: string; context_note: string; language: "ru" | "en";
};
type SavedProfile = BusinessProfile & { profile_version: number; status: "incomplete" | "complete"; updated_at: string };
type TagOption = { id: string; label: string; description: string };
type ProfileOptions = { industry_tags: TagOption[]; focus_topics: TagOption[] };
type SiteSuggestion = {
  field_name: string; value: string | string[]; evidence: string; source_url: string;
  source_title: string; extracted_at: string; confidence: number;
};
type RealNewsItem = {
  id: string; title: string; published_at: string; excerpt: string; event_type: string;
  event_title: string; source: string; url: string;
  analysis: null | { relevant: boolean; score: number; reasons: string[]; method: string; analyzed_at: string };
  report: null | { summary: string; relevance: string; risks: string; opportunities: string; questions: string[]; evidence_ids: string[]; citations: ChatCitation[]; created_at: string };
};
type RealFeed = {
  items: RealNewsItem[]; insight: RealNewsItem[]; profile_version: number;
  insight_markets: { category: string; instrument: string; title: string; price: number; change_pct: number | null; currency: string; source_url: string; source_timestamp: string; reason: string }[];
  insight_updated_at: string | null; insight_status: string; insight_stale: boolean; insight_refresh_mode: string;
  lookback_days: number; analysis_method: string; analyzed_count: number;
  pending_count: number; job: null | { status: string; updated_at: string; error: string };
  trigger: string; personalization_available: boolean;
};
type RealMarkets = {
  provider: string; delayed: boolean; latest: null | { status: string; finished_at: string };
  snapshot: { stored_count: number; items: { category: string; instrument: string; name: string; price: number; change_pct: number | null; currency: string; source_url: string; source_timestamp: string; observed_at: string; profile_reason: string }[] };
};
type MarketHistory = {
  items: { price: number; currency: string; source_timestamp: string; observed_at: string; source_url: string }[];
  change_over_period_pct: number | null;
};
type ChatCitation = { title: string; url: string; published_at: string; kind: string };
type ChatMessage = { role: "user" | "assistant"; text: string; citations: ChatCitation[]; created_at: string };

function ProfileChatScreen({ sessionId, csrf, companyName, onBack }: {
  sessionId: string; csrf: string; companyName: string; onBack: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [title, setTitle] = useState("Общий чат по бизнесу");
  const [focused, setFocused] = useState(false);
  useEffect(() => {
    let cancelled = false;
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
  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = question.trim();
    if (!text || busy) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/real/chat/sessions/${encodeURIComponent(sessionId)}/messages`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
        body: JSON.stringify({ question: text }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Не удалось получить ответ");
      setMessages((current) => [...current, { role: "user", text, citations: [], created_at: data.created_at }, data as ChatMessage]);
      setQuestion("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось связаться с чатом");
    } finally { setBusy(false); }
  }
  return <section className="card profile-chat-page">
    <button className="linkish" type="button" onClick={onBack}>← К новостям</button>
    <p className="legend">Чат по профилю компании</p>
    <h2>{title}</h2>
    <p className="hint">Контекст компании: {companyName || "профиль пока не заполнен"}. Можно задавать общие вопросы по бизнесу и новостям; выбранная статья {focused ? "добавлена как основной материал диалога" : "не закреплена, доступны свежие официальные материалы"}.</p>
    <div className="profile-chat-messages" aria-live="polite">
      {messages.length ? messages.map((message, index) => <article className={`profile-chat-message ${message.role}`} key={`${message.created_at}-${index}`}>
        <strong>{message.role === "user" ? "Вы" : "Помощник"}</strong>
        <p>{message.text}</p>
        {message.citations.length ? <div className="profile-chat-citations"><small>Материалы:</small><ul>{message.citations.map((citation, citationIndex) => <li key={`${citation.url}-${citationIndex}`}>
          {citation.url ? <a href={citation.url} target="_blank" rel="noreferrer">{citation.title}</a> : <span>{citation.title} (данные профиля)</span>}{citation.published_at ? <small> · {citation.published_at}</small> : null}
        </li>)}</ul></div> : null}
      </article>) : <p className="hint">Начните с вопроса о том, как новости и рыночные события могут быть связаны с вашим профилем.</p>}
    </div>
    {error ? <p className="error" role="alert">{error}</p> : null}
    <form className="profile-chat-form" onSubmit={(event) => void send(event)}>
      <label htmlFor="profile-chat-question">Ваш вопрос</label>
      <textarea id="profile-chat-question" rows={3} maxLength={2000} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Например: какие из свежих новостей могут затронуть наши поставки?" />
      <button type="submit" disabled={busy || !question.trim()}>{busy ? "Готовим ответ…" : "Отправить"}</button>
    </form>
    <p className="hint">Ответ отделяет факт публикации от оценки для вашей компании. Внизу показаны использованные материалы. При недоступности модели сообщение не сохраняется.</p>
  </section>;
}

const PROFILE_FIELD_LABELS: Record<string, string> = {
  company_name: "Название компании", country: "Страна", region: "Регион", city: "Город",
  description: "Описание бизнеса", industry: "Основная деятельность", industry_tags: "Отраслевые теги",
  products_services: "Товары и услуги", customer_types: "Тип клиентов", business_model: "Модель бизнеса",
};

const EMPTY_PROFILE: BusinessProfile = {
  company_name: "", website: "", country: "", region: "", city: "", description: "",
  description_skipped: false, industry: "", okved: "", industry_tags: [], products_services: "", customer_types: "",
  business_model: "", size_band: "", business_stage: "", credit_interest: "unknown",
  deposit_interest: "unknown", currency_interest: "unknown", export_import_interest: "unknown",
  leasing_interest: "unknown", guarantees_interest: "unknown", factoring_interest: "unknown",
  settlements_interest: "unknown", market_geographies: [], dependencies: "", focus_topics: [],
  external_factors: "", context_note: "", language: "ru",
};

function profilePayload(source: BusinessProfile): BusinessProfile {
  const payload = { ...EMPTY_PROFILE };
  (Object.keys(EMPTY_PROFILE) as (keyof BusinessProfile)[]).forEach((key) => {
    payload[key] = source[key];
  });
  return payload;
}

export function RealMode({
  mode,
  onModeChange,
  onHome,
}: {
  mode: AppMode;
  onModeChange: (mode: AppMode) => void;
  onHome: () => void;
}) {
  const [csrf, setCsrf] = useState("");
  const [user, setUser] = useState<User | null>(null);
  const [registering, setRegistering] = useState(false);
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [profile, setProfile] = useState<BusinessProfile>(EMPTY_PROFILE);
  const [profileMeta, setProfileMeta] = useState<SavedProfile | null>(null);
  const [profileBusy, setProfileBusy] = useState(false);
  const [editingProfile, setEditingProfile] = useState(true);
  const [profileDirty, setProfileDirty] = useState(false);
  const [profileOptions, setProfileOptions] = useState<ProfileOptions>({ industry_tags: [], focus_topics: [] });
  const [siteConsent, setSiteConsent] = useState(false);
  const [siteBusy, setSiteBusy] = useState(false);
  const [siteWarnings, setSiteWarnings] = useState<string[]>([]);
  const [siteSuggestions, setSiteSuggestions] = useState<SiteSuggestion[]>([]);
  const [realFeed, setRealFeed] = useState<RealFeed | null>(null);
  const [realMarkets, setRealMarkets] = useState<RealMarkets | null>(null);
  const [marketHistories, setMarketHistories] = useState<Record<string, MarketHistory>>({});
  const [marketHistoryBusy, setMarketHistoryBusy] = useState("");
  const [feedTab, setFeedTab] = useState<"personal" | "all">("personal");
  const [feedQuery, setFeedQuery] = useState("");
  const [feedCategory, setFeedCategory] = useState("all");
  const [feedPeriod, setFeedPeriod] = useState("window");
  const [feedBusy, setFeedBusy] = useState(false);
  const [feedRefreshKey, setFeedRefreshKey] = useState(0);
  const [activeChatId, setActiveChatId] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [insightBusy, setInsightBusy] = useState(false);
  const [newsReportBusy, setNewsReportBusy] = useState("");
  const [openNewsReports, setOpenNewsReports] = useState<string[]>([]);

  async function refreshCsrf(): Promise<string> {
    const response = await fetch("/api/auth/csrf");
    if (!response.ok) throw new Error("Не удалось подготовить защищённую сессию");
    const payload = (await response.json()) as { csrf_token: string };
    setCsrf(payload.csrf_token);
    return payload.csrf_token;
  }

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        await refreshCsrf();
        const meResponse = await fetch("/api/auth/me", { signal: controller.signal });
        if (!meResponse.ok) throw new Error("Не удалось проверить вход");
        const me = (await meResponse.json()) as { authenticated: boolean; user: User | null };
        setUser(me.authenticated ? me.user : null);
        if (me.authenticated) await loadProfile();
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Сервис авторизации недоступен");
        }
      } finally {
        if (!controller.signal.aborted) setReady(true);
      }
    })();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!user || !profileMeta) return;
    let disposed = false;
    let timer: number | undefined;
    async function refresh() {
      try {
        const [feedResponse, marketResponse] = await Promise.all([
          fetch("/api/real/feed"), fetch("/api/real/markets"),
        ]);
        if (!feedResponse.ok) throw new Error("Не удалось загрузить ленту компании");
        const feed = (await feedResponse.json()) as RealFeed;
        if (!disposed) setRealFeed(feed);
        if (marketResponse.ok && !disposed) setRealMarkets((await marketResponse.json()) as RealMarkets);
        if (!disposed && feed.job && ["queued", "running"].includes(feed.job.status)) {
          timer = window.setTimeout(() => void refresh(), 1800);
        }
      } catch (reason) {
        if (!disposed) setError(reason instanceof Error ? reason.message : "Не удалось загрузить ленту");
      }
    }
    void refresh();
    return () => { disposed = true; if (timer !== undefined) window.clearTimeout(timer); };
  }, [user?.id, profileMeta?.profile_version, editingProfile, feedRefreshKey]);

  async function loadProfile(): Promise<void> {
    const [response, optionsResponse, suggestionsResponse] = await Promise.all([
      fetch("/api/real/profile"),
      fetch("/api/real/profile/options"),
      fetch("/api/real/profile/suggestions"),
    ]);
    if (!response.ok) throw new Error("Не удалось загрузить профиль компании");
    if (!optionsResponse.ok) throw new Error("Не удалось загрузить варианты профиля");
    if (!suggestionsResponse.ok) throw new Error("Не удалось загрузить предложения сайта");
    const payload = (await response.json()) as { profile: SavedProfile };
    setProfileOptions((await optionsResponse.json()) as ProfileOptions);
    let suggestions = ((await suggestionsResponse.json()) as { suggestions: SiteSuggestion[] }).suggestions;
    let savedProfile = payload.profile;
    let filledLabels: string[] = [];
    if (suggestions.length) {
      const csrfToken = await refreshCsrf();
      const appliedResponse = await fetch("/api/real/profile/suggestions/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      });
      const applied = (await appliedResponse.json().catch(() => null)) as {
        profile?: SavedProfile; filled_fields?: string[]; suggestions?: SiteSuggestion[];
      } | null;
      if (appliedResponse.ok && applied?.profile) {
        savedProfile = applied.profile;
        suggestions = applied.suggestions ?? [];
        filledLabels = (applied.filled_fields ?? []).map((field) => PROFILE_FIELD_LABELS[field] ?? field);
      }
    }
    setSiteSuggestions(suggestions);
    setProfile({ ...EMPTY_PROFILE, ...savedProfile });
    setProfileMeta(savedProfile);
    setEditingProfile(filledLabels.length > 0 || savedProfile.status !== "complete");
    if (filledLabels.length) {
      setNotice(`В поля профиля записано: ${filledLabels.join(", ")}. Проверьте значения и поправьте, если нужно.`);
    }
    setProfileDirty(false);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (registering && password !== confirmPassword) {
      setError("Пароли не совпадают");
      return;
    }
    setBusy(true);
    try {
      const csrfToken = await refreshCsrf();
      const response = await fetch(`/api/auth/${registering ? "register" : "login"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ login, password }),
      });
      const payload = (await response.json().catch(() => null)) as (User & { detail?: string }) | null;
      if (!response.ok || !payload?.id) {
        setError(payload?.detail || `Вход: ${response.status}`);
        return;
      }
      setUser({ id: payload.id, login: payload.login, created_at: payload.created_at });
      await loadProfile();
      setPassword("");
      setConfirmPassword("");
      setNotice(registering ? "Аккаунт создан. Заполните профиль компании ниже." : "Вы вошли.");
    } catch {
      setError("Сервер авторизации не ответил");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    setError("");
    try {
      const csrfToken = await refreshCsrf();
      const response = await fetch("/api/auth/logout", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
      });
      if (!response.ok) throw new Error("Не удалось завершить сессию");
      setUser(null);
      setActiveChatId("");
      setProfile(EMPTY_PROFILE);
      setProfileMeta(null);
      setRealFeed(null);
      setRealMarkets(null);
      setNotice("Вы вышли из аккаунта.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось выйти");
    } finally {
      setBusy(false);
    }
  }

  async function openProfileChat(newsId?: string) {
    setChatBusy(true); setError("");
    try {
      const csrfToken = await refreshCsrf();
      const response = await fetch("/api/real/chat/sessions", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify(newsId ? { news_id: newsId } : {}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Не удалось открыть чат");
      setActiveChatId(data.id as string);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось открыть чат");
    } finally { setChatBusy(false); }
  }

  async function analyzeNewsNow() {
    setFeedBusy(true);
    setError("");
    setNotice("");
    try {
      const csrfToken = await refreshCsrf();
      const response = await fetch("/api/real/feed/analyze", {
        method: "POST", headers: { "X-CSRF-Token": csrfToken },
      });
      const payload = (await response.json().catch(() => null)) as (RealFeed & { detail?: string }) | null;
      if (!response.ok || !payload?.items) throw new Error(payload?.detail || "Не удалось поставить новости в очередь");
      setRealFeed(payload);
      setFeedRefreshKey((value) => value + 1);
      setNotice(payload.pending_count ? "Анализ поставлен в очередь. Лента обновится по мере готовности." : "Все актуальные новости уже оценены.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось запустить анализ");
    } finally {
      setFeedBusy(false);
    }
  }

  async function refreshInsightNow() {
    setInsightBusy(true); setError("");
    try {
      const csrfToken = await refreshCsrf();
      const response = await fetch("/api/real/insight/refresh", { method: "POST", headers: { "X-CSRF-Token": csrfToken } });
      const payload = (await response.json().catch(() => null)) as (RealFeed & { detail?: string }) | null;
      if (!response.ok || !payload?.insight_status) throw new Error(payload?.detail || "Не удалось обновить обзор");
      setRealFeed(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось обновить обзор");
    } finally { setInsightBusy(false); }
  }

  async function requestNewsReport(newsId: string, refresh = false) {
    setNewsReportBusy(newsId); setError("");
    try {
      const csrfToken = await refreshCsrf();
      const response = await fetch(`/api/real/news/${encodeURIComponent(newsId)}/report`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ refresh }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Не удалось получить разбор новости");
      setRealFeed((current) => current ? {
        ...current, items: current.items.map((item) => item.id === newsId ? { ...item, report: data } : item),
      } : current);
      setOpenNewsReports((current) => current.includes(newsId) ? current : [...current, newsId]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось получить разбор новости");
    } finally { setNewsReportBusy(""); }
  }

  async function toggleMarketHistory(category: string, instrument: string) {
    const key = `${category}:${instrument}`;
    if (marketHistories[key]) {
      setMarketHistories((current) => { const next = { ...current }; delete next[key]; return next; });
      return;
    }
    setMarketHistoryBusy(key);
    try {
      const response = await fetch(`/api/real/markets/${encodeURIComponent(category)}/${encodeURIComponent(instrument)}`);
      if (!response.ok) throw new Error("Не удалось загрузить историю инструмента");
      const history = (await response.json()) as MarketHistory;
      setMarketHistories((current) => ({ ...current, [key]: history }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось загрузить историю рынка");
    } finally {
      setMarketHistoryBusy("");
    }
  }

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setProfileBusy(true);
    setError("");
    setNotice("");
    try {
      const csrfToken = await refreshCsrf();
      const response = await fetch("/api/real/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify(profilePayload(profile)),
      });
      const payload = (await response.json().catch(() => null)) as { profile?: SavedProfile; detail?: string; fields?: string[] } | null;
      if (!response.ok || !payload?.profile) {
        const fields = payload?.fields?.length ? ` (${payload.fields.join(", ")})` : "";
        throw new Error(`${payload?.detail || "Не удалось сохранить профиль"}${fields}`);
      }
      setProfile({ ...EMPTY_PROFILE, ...payload.profile });
      setProfileMeta(payload.profile);
      setProfileDirty(false);
      setEditingProfile(payload.profile.status !== "complete");
      setNotice(payload.profile.status === "complete" ? "Профиль сохранён." : "Черновик профиля сохранён.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить профиль");
    } finally {
      setProfileBusy(false);
    }
  }

  function updateProfile<K extends keyof BusinessProfile>(key: K, value: BusinessProfile[K]) {
    setProfile((current) => ({ ...current, [key]: value }));
    setProfileDirty(true);
  }

  function returnToNews() {
    if (profileDirty && !window.confirm("Есть несохранённые изменения профиля. Отменить их?")) return;
    if (profileDirty && profileMeta) setProfile({ ...EMPTY_PROFILE, ...profileMeta });
    setProfileDirty(false);
    setEditingProfile(false);
  }

  async function analyzeWebsite() {
    if (!siteConsent || profileDirty || !profile.website) return;
    setSiteBusy(true);
    setError("");
    setNotice("");
    setSiteWarnings([]);
    try {
      const csrfToken = await refreshCsrf();
      const response = await fetch("/api/real/profile/site-analysis", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ consent: true }),
      });
      const payload = (await response.json().catch(() => null)) as {
        status?: string; profile?: SavedProfile | null; filled_fields?: string[]; suggestions?: SiteSuggestion[];
        warnings?: string[]; pages_scanned?: number; opened_url?: string; detail?: string;
      } | null;
      if (!response.ok || !payload) throw new Error(payload?.detail || "Не удалось проанализировать сайт");
      setSiteSuggestions(payload.suggestions ?? []);
      setSiteWarnings(payload.warnings ?? []);
      if (payload.profile) {
        setProfile({ ...EMPTY_PROFILE, ...payload.profile });
        setProfileMeta(payload.profile);
        setProfileDirty(false);
        setEditingProfile(true);
      }
      const labels = (payload.filled_fields ?? []).map((field) => PROFILE_FIELD_LABELS[field] ?? field);
      const opened = payload.opened_url ? ` Сайт открылся как ${payload.opened_url}.` : "";
      setNotice(labels.length
        ? `В поля профиля записано: ${labels.join(", ")}. Проверьте значения и поправьте, если нужно.${opened}`
        : payload.pages_scanned
          ? `Просмотрено страниц: ${payload.pages_scanned}. Новых пустых полей для заполнения нет.${opened}`
          : "На сайте не найдено доступных для анализа страниц.");
      setSiteConsent(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось проанализировать сайт");
    } finally {
      setSiteBusy(false);
    }
  }

  async function decideSuggestion(suggestion: SiteSuggestion, accept: boolean) {
    if (profileDirty) {
      setError("Сначала сохраните или отмените изменения профиля.");
      return;
    }
    setSiteBusy(true);
    setError("");
    try {
      const csrfToken = await refreshCsrf();
      const response = await fetch("/api/real/profile/suggestions/decision", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({
          field_name: suggestion.field_name,
          source_url: suggestion.source_url,
          accept,
        }),
      });
      const payload = (await response.json().catch(() => null)) as {
        status?: string; profile?: SavedProfile | null; suggestions?: SiteSuggestion[]; detail?: string;
      } | null;
      if (!response.ok || !payload) throw new Error(payload?.detail || "Не удалось сохранить решение");
      setSiteSuggestions(payload.suggestions ?? []);
      if (payload.profile) {
        setProfile({ ...EMPTY_PROFILE, ...payload.profile });
        setProfileMeta(payload.profile);
      }
      setNotice(accept ? "Значение подтверждено и добавлено в профиль." : "Предложение отклонено.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить решение");
    } finally {
      setSiteBusy(false);
    }
  }

  function suggestionLabel(field: string, value: string | string[]): string {
    if (!Array.isArray(value)) return value;
    const options = field === "industry_tags" ? profileOptions.industry_tags : [];
    return value.map((id) => options.find((item) => item.id === id)?.label ?? id).join(", ");
  }

  function toggleTag(key: "industry_tags" | "focus_topics", id: string, checked: boolean) {
    const current = profile[key];
    updateProfile(key, checked ? [...current, id] : current.filter((item) => item !== id));
  }

  function updateGeographies(value: string) {
    updateProfile("market_geographies", value.split(",").map((item) => item.trim()).filter(Boolean));
  }

  const feedItems = (realFeed?.items ?? []).filter((item) => {
    if (feedTab === "personal" && !item.analysis?.relevant) return false;
    if (feedCategory !== "all" && item.event_type !== feedCategory) return false;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const publishedDate = new Date(`${item.published_at}T00:00:00`);
    const ageCalendarDays = Math.floor((today.getTime() - publishedDate.getTime()) / 86400000);
    if (feedPeriod === "day" && ageCalendarDays > 0) return false;
    if (feedPeriod === "three_days" && ageCalendarDays > 2) return false;
    const query = feedQuery.trim().toLocaleLowerCase("ru-RU");
    return !query || `${item.title} ${item.excerpt} ${item.source}`.toLocaleLowerCase("ru-RU").includes(query);
  });
  const feedCategories = [...new Map((realFeed?.items ?? []).filter((item) => item.event_type !== "unknown").map((item) => [item.event_type, item.event_title])).entries()];

  return (
    <div className="bank">
      <header className="bank-bar">
        <div className="bank-brand">
          <strong>Альфа-Бизнес</strong>
          <span>интернет-банк</span>
        </div>
        <ModeSwitch mode={mode} onChange={onModeChange} />
      </header>
      <main className="bank-main narrow">
        <button className="linkish" type="button" onClick={onHome}>На главную</button>
        <h1>{user ? activeChatId ? "Чат по бизнесу" : "Реальный режим" : registering ? "Регистрация" : "Вход"}</h1>
        {!ready ? <p>Проверяем сессию…</p> : null}
        {user ? (
          <>
            <section className="card">
              <h2>Здравствуйте, {user.login}</h2>
              {profileMeta ? (
                <p className="hint">
                  {profileMeta.status === "complete" ? "Профиль заполнен" : "Профиль заполнен частично"}
                  {` · версия ${profileMeta.profile_version}`}
                </p>
              ) : null}
              {notice ? <p role="status">{notice}</p> : null}
              {error ? <p className="error" role="alert">{error}</p> : null}
              <button className="auth-action" type="button" onClick={() => void logout()} disabled={busy || profileBusy}>
                {busy ? "Завершаем…" : "Выйти"}
              </button>
            </section>
            {activeChatId ? <ProfileChatScreen sessionId={activeChatId} csrf={csrf} companyName={profile.company_name} onBack={() => setActiveChatId("")} /> : <>
            {siteSuggestions.length || siteWarnings.length ? (
              <section className="card site-suggestions">
                <h2>Проверьте найденные данные</h2>
                <p className="hint">Это предложения модели, они не попадают в профиль без вашего подтверждения.</p>
                {siteWarnings.map((warning, index) => <p className="site-warning" key={`${index}-${warning}`}>{warning}</p>)}
                {siteSuggestions.map((suggestion) => (
                  <article className="site-suggestion" key={`${suggestion.field_name}:${suggestion.source_url}`}>
                    <h3>{PROFILE_FIELD_LABELS[suggestion.field_name] ?? suggestion.field_name}</h3>
                    <p><strong>Предложение:</strong> {suggestionLabel(suggestion.field_name, suggestion.value)}</p>
                    <blockquote>{suggestion.evidence}</blockquote>
                    <p>
                      <a href={suggestion.source_url} target="_blank" rel="noreferrer">
                        {suggestion.source_title || "Страница-источник"}
                      </a>
                      {` · оценка модели: ${suggestion.confidence >= 0.8 ? "высокая" : "средняя"} (не гарантия точности)`}
                    </p>
                    <div className="site-suggestion-actions">
                      <button type="button" onClick={() => void decideSuggestion(suggestion, true)} disabled={siteBusy || profileDirty}>
                        Подтвердить
                      </button>
                      <button type="button" className="secondary" onClick={() => void decideSuggestion(suggestion, false)} disabled={siteBusy || profileDirty}>
                        Отклонить
                      </button>
                    </div>
                  </article>
                ))}
              </section>
            ) : null}
            {!editingProfile ? (
              <>
                <div className="profile-toolbar">
                  <button type="button" className="auth-action" onClick={() => setEditingProfile(true)}>Настроить профиль компании</button>
                  <button type="button" className="auth-action" onClick={() => void openProfileChat()} disabled={chatBusy}>{chatBusy ? "Открываем чат…" : "Обсудить новости и бизнес"}</button>
                </div>
                <section className="card">
                  <h2>Важные новости</h2>
                  <div className="feed-tabs" role="tablist" aria-label="Режим новостной ленты">
                    <button type="button" role="tab" aria-selected={feedTab === "personal"} onClick={() => setFeedTab("personal")}>Для вас</button>
                    <button type="button" role="tab" aria-selected={feedTab === "all"} onClick={() => setFeedTab("all")}>Все новости</button>
                  </div>
                  {realFeed ? <p className="hint">Окно: последние {realFeed.lookback_days} дн. · обработано {realFeed.analyzed_count} · метод: {realFeed.analysis_method}</p> : <p>Загружаем ленту…</p>}
                  {realFeed?.job?.status === "queued" || realFeed?.job?.status === "running" ? <p role="status">Анализ новостей выполняется · в очереди {realFeed.pending_count}</p> : null}
                  {realFeed && realFeed.pending_count > 0 && !["queued", "running"].includes(realFeed.job?.status ?? "") ? <p role="status">Есть новые материалы ({realFeed.pending_count}). Нажмите «Обновить подборку», чтобы увидеть связь с вашей компанией.</p> : null}
                  {realFeed?.job?.status === "failed" ? <p className="error">Последняя задача анализа завершилась с ошибкой. Можно запустить её вручную.</p> : null}
                  <div className="feed-filters">
                    <label>Поиск<input value={feedQuery} onChange={(event) => setFeedQuery(event.target.value)} placeholder="Тема, слово или источник" /></label>
                    <label>Категория<select value={feedCategory} onChange={(event) => setFeedCategory(event.target.value)}>
                      <option value="all">Все категории</option>
                      {feedCategories.map(([category, title]) => <option key={category} value={category}>{title}</option>)}
                    </select></label>
                    <label>Период<select value={feedPeriod} onChange={(event) => setFeedPeriod(event.target.value)}>
                      <option value="window">Окно настроек ({realFeed?.lookback_days ?? 3} дн.)</option>
                      <option value="day">Сегодня (по дате публикации)</option>
                      <option value="three_days">3 календарных дня</option>
                    </select></label>
                  </div>
                  <button type="button" onClick={() => void analyzeNewsNow()} disabled={feedBusy || Boolean(realFeed?.job && ["queued", "running"].includes(realFeed.job.status))}>
                    {feedBusy ? "Ставим в очередь…" : "Обновить подборку"}
                  </button>
                  {feedTab === "personal" ? <p className="hint">В подборке показаны только свежие новости, которым правила нашли объяснимую связь с профилем. «Все новости» не запускает анализ.</p> : null}
                  {feedItems.length ? <div className="real-news-list">
                    {feedItems.map((item) => <article className="real-news-item" id={`real-news-${item.id}`} key={item.id}>
                      <p className="eyebrow">{item.event_title} · {item.published_at}</p>
                      <h3><a href={item.url} target="_blank" rel="noreferrer">{item.title}</a></h3>
                      <p className="hint">{item.source}</p>
                      <p>{item.excerpt}</p>
                      {item.analysis ? <div className="analysis-reasons">
                        <p><strong>{item.analysis.method}</strong> · связь с профилем {Math.round(item.analysis.score * 100)}%</p>
                        <ul>{item.analysis.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
                        <small>Оценка создана {new Date(item.analysis.analyzed_at).toLocaleString("ru-RU")}</small>
                      </div> : feedTab === "all" ? <p className="hint">Ещё не оценивалась для профиля. Просмотр «Все новости» не запускает анализ.</p> : null}
                      <div className="news-report-controls">
                        <button type="button" className="secondary" onClick={() => item.report ? setOpenNewsReports((current) => current.includes(item.id) ? current.filter((id) => id !== item.id) : [...current, item.id]) : void requestNewsReport(item.id)} disabled={newsReportBusy === item.id}>
                          {newsReportBusy === item.id ? "Готовим разбор…" : item.report ? openNewsReports.includes(item.id) ? "Скрыть подробный разбор" : "Показать подробный разбор" : "Подробно разобрать для бизнеса"}
                        </button>
                        {item.report && openNewsReports.includes(item.id) ? <div className="news-report">
                          <p className="hint">Сохранено {new Date(item.report.created_at).toLocaleString("ru-RU")}. При повторном открытии используется этот результат.</p>
                          <p><strong>Кратко:</strong> {item.report.summary}</p><p><strong>Связь с профилем:</strong> {item.report.relevance}</p>
                          <p><strong>Риски:</strong> {item.report.risks}</p><p><strong>Возможности:</strong> {item.report.opportunities}</p>
                          {item.report.questions.length ? <ul>{item.report.questions.map((question) => <li key={question}>{question}</li>)}</ul> : null}
                          {item.report.citations.length ? <p><small>Основание: {item.report.citations.map((citation, index) => <span key={`${citation.url}-${index}`}>{index ? " · " : ""}{citation.url ? <a href={citation.url} target="_blank" rel="noreferrer">{citation.title}</a> : citation.title}{citation.published_at ? ` (${citation.published_at})` : ""}</span>)}</small></p> : null}
                          <button type="button" className="linkish" onClick={() => void requestNewsReport(item.id, true)} disabled={newsReportBusy === item.id}>{newsReportBusy === item.id ? "Обновляем…" : "Обновить разбор"}</button>
                        </div> : null}
                      </div>
                      <button type="button" className="secondary" onClick={() => void openProfileChat(item.id)} disabled={chatBusy}>Обсудить эту новость</button>
                    </article>)}
                  </div> : <p>{feedTab === "personal" ? "Пока нет новостей с подтверждённой связью с профилем." : "За выбранное окно нет сохранённых новостей из подключённых источников."}</p>}
                </section>
                <section className="card insight">
                  <p className="legend">Обзор компании</p>
                  <h2>Что сейчас важно для вашего бизнеса</h2>
                  <p className="hint">{realFeed?.insight_status ?? "Загружаем сохранённый обзор…"}{realFeed?.insight_updated_at ? ` · сохранён ${new Date(realFeed.insight_updated_at).toLocaleString("ru-RU")}` : ""}</p>
                  {realFeed?.insight.length ? <>
                    <p className="hint">Краткий обзор собран из уже сохранённых оценок и рыночных наблюдений. Новых вызовов модели нет.</p>
                    <ul>{realFeed.insight.map((item) => <li key={item.id}><a href={`#real-news-${item.id}`}>{item.title}</a><br /><small>{item.analysis?.reasons[0]}</small></li>)}</ul>
                  </> : <p>{realFeed?.job && ["queued", "running"].includes(realFeed.job.status) ? "Обзор появится после завершения анализа." : "В сохранённом обзоре пока нет подтверждённых персональных новостных сигналов."}</p>}
                  {realFeed?.insight_markets.length ? <>
                    <h3>Рыночные наблюдения по профилю</h3>
                    <ul>{realFeed.insight_markets.map((item) => <li key={`insight-${item.instrument}`}><a href="#real-markets">{item.title}: {item.price} {item.currency}{item.change_pct !== null ? ` · ${item.change_pct}%` : ""}</a><br /><small>{item.reason}; наблюдение по MOEX на {item.source_timestamp}, не прогноз влияния.</small></li>)}</ul>
                  </> : null}
                  <button type="button" className="secondary" onClick={() => void refreshInsightNow()} disabled={insightBusy}>{insightBusy ? "Обновляем обзор…" : "Обновить обзор сейчас"}</button>
                  {realFeed?.trigger === "manual" ? <button type="button" onClick={() => void analyzeNewsNow()} disabled={feedBusy}>Обновить анализ</button> : null}
                </section>
                <section className="card market-panel" id="real-markets">
                  <h2>Рынки</h2>
                  <p className="hint">Источник: Московская биржа (MOEX ISS). Бесплатные данные могут быть задержаны; время и источник указаны для каждого значения.</p>
                  {realMarkets?.snapshot.items.length ? <ul>{realMarkets.snapshot.items.map((item) => <li key={`${item.category}-${item.instrument}`}>
                    <strong>{item.name}</strong> ({item.instrument}): {item.price} {item.currency}{item.change_pct !== null ? ` · ${item.change_pct > 0 ? "+" : ""}${item.change_pct}%` : ""}{item.profile_reason ? <span className="market-profile-reason"> · {item.profile_reason}</span> : null}<br />
                    <small>MOEX: {item.source_timestamp} · получено {new Date(item.observed_at).toLocaleString("ru-RU")} · <a href={item.source_url} target="_blank" rel="noreferrer">источник</a></small>
                    <br /><button type="button" className="secondary" onClick={() => void toggleMarketHistory(item.category, item.instrument)} disabled={marketHistoryBusy === `${item.category}:${item.instrument}`}>
                      {marketHistoryBusy === `${item.category}:${item.instrument}` ? "Загружаем…" : marketHistories[`${item.category}:${item.instrument}`] ? "Скрыть историю" : "История"}
                    </button>
                    {marketHistories[`${item.category}:${item.instrument}`] ? <div className="market-history">
                      {item.profile_reason ? <p>{item.profile_reason}. Это связь по профилю, а не прогноз финансового эффекта.</p> : null}
                      <p>Изменение за сохранённый период: {marketHistories[`${item.category}:${item.instrument}`].change_over_period_pct === null ? "недостаточно точек" : `${marketHistories[`${item.category}:${item.instrument}`].change_over_period_pct}%`}</p>
                      <ol>{marketHistories[`${item.category}:${item.instrument}`].items.map((point, index) => <li key={`${point.source_timestamp}-${index}`}>{point.source_timestamp}: {point.price} {point.currency} · получено {new Date(point.observed_at).toLocaleString("ru-RU")} · <a href={point.source_url} target="_blank" rel="noreferrer">источник MOEX</a></li>)}</ol>
                    </div> : null}
                  </li>)}</ul> : <p>{realMarkets?.latest ? "В хранилище пока нет котировок. Проверьте статус и запустите ручной сбор в настройках разработчика." : "Рыночный сбор ещё не запускался. Запустить его можно в настройках разработчика."}</p>}
                  {realMarkets?.latest ? <p className="hint">Последний сбор: {new Date(realMarkets.latest.finished_at).toLocaleString("ru-RU")} · {realMarkets.latest.status}{realMarkets.delayed ? " · задержанные данные" : ""}</p> : null}
                </section>
              </>
            ) : null}
            {editingProfile ? <form className="card settings-form profile-form" onSubmit={(event) => void saveProfile(event)}>
              <h2>Профиль компании</h2>
              {profileMeta?.status === "complete" ? <button type="button" className="linkish" onClick={returnToNews}>Вернуться к новостям</button> : null}
              <p className="hint">Пустые поля останутся неизвестными. Не указывайте номера счетов или точные финансовые суммы.</p>
              <p className="hint">Для завершения профиля укажите название компании и краткое описание либо явно пропустите описание. Черновик можно сохранить и без них.</p>
              <div className="profile-grid">
                <label>Название компании / бренда *<input maxLength={200} value={profile.company_name} onChange={(event) => updateProfile("company_name", event.target.value)} /></label>
                <label>Сайт компании<input type="text" inputMode="url" placeholder="https://example.ru" maxLength={500} value={profile.website} onChange={(event) => updateProfile("website", event.target.value)} /></label>
                <label>Страна<input maxLength={100} value={profile.country} onChange={(event) => updateProfile("country", event.target.value)} /></label>
                <label>Регион<input maxLength={150} value={profile.region} onChange={(event) => updateProfile("region", event.target.value)} /></label>
                <label>Город<input maxLength={150} value={profile.city} onChange={(event) => updateProfile("city", event.target.value)} /></label>
                <label>Основная деятельность<input maxLength={200} value={profile.industry} onChange={(event) => updateProfile("industry", event.target.value)} /></label>
                <label>ОКВЭД, если знаете<input maxLength={30} value={profile.okved} onChange={(event) => updateProfile("okved", event.target.value)} /></label>
                <label>Размер компании<input placeholder="Например: 11–50 сотрудников" maxLength={100} value={profile.size_band} onChange={(event) => updateProfile("size_band", event.target.value)} /></label>
                <label>Этап бизнеса<input placeholder="Например: рост" maxLength={100} value={profile.business_stage} onChange={(event) => updateProfile("business_stage", event.target.value)} /></label>
              </div>
              <div className="site-analysis-controls">
                <label className="profile-skip">
                  <input type="checkbox" checked={siteConsent} onChange={(event) => setSiteConsent(event.target.checked)} />
                  <span>Согласен отправить текст публичных страниц выбранной модели для анализа. При облачном маршруте он будет передан внешнему API; закрытые данные отправлять не следует.</span>
                </label>
                <button type="button" onClick={() => void analyzeWebsite()} disabled={!profile.website || !siteConsent || profileDirty || siteBusy}>
                  {siteBusy ? "Анализируем…" : "Подтянуть данные с сайта"}
                </button>
                {profileDirty && profile.website ? <small>Сохраните изменения профиля перед запуском анализа.</small> : null}
              </div>
              <label>Кратко о бизнесе<textarea maxLength={2000} rows={3} value={profile.description} onChange={(event) => updateProfile("description", event.target.value)} /></label>
              <label className="profile-skip"><input type="checkbox" checked={profile.description_skipped} onChange={(event) => updateProfile("description_skipped", event.target.checked)} />Пока пропустить описание</label>
              <fieldset className="profile-tags">
                <legend>Отраслевые теги</legend>
                {profileOptions.industry_tags.map((tag) => (
                  <label className="profile-tag" key={tag.id} title={tag.description}>
                    <input type="checkbox" checked={profile.industry_tags.includes(tag.id)} onChange={(event) => toggleTag("industry_tags", tag.id, event.target.checked)} />
                    <span><strong>{tag.label}</strong><small>{tag.description}</small></span>
                  </label>
                ))}
              </fieldset>
              <label>Товары и услуги<textarea maxLength={1500} rows={2} value={profile.products_services} onChange={(event) => updateProfile("products_services", event.target.value)} /></label>
              <div className="profile-grid">
                <label>Тип клиентов<input maxLength={500} value={profile.customer_types} onChange={(event) => updateProfile("customer_types", event.target.value)} /></label>
                <label>Модель бизнеса<input maxLength={500} value={profile.business_model} onChange={(event) => updateProfile("business_model", event.target.value)} /></label>
              </div>
              <h3>Что актуально для компании?</h3>
              <div className="profile-grid">
                <label>Кредитование / ставки<select value={profile.credit_interest} onChange={(event) => updateProfile("credit_interest", event.target.value as Interest)}><option value="unknown">Не знаю</option><option value="interested">Интересно</option><option value="not_relevant">Неактуально</option></select></label>
                <label>Депозиты / ликвидность<select value={profile.deposit_interest} onChange={(event) => updateProfile("deposit_interest", event.target.value as Interest)}><option value="unknown">Не знаю</option><option value="interested">Интересно</option><option value="not_relevant">Неактуально</option></select></label>
                <label>Валютные операции<select value={profile.currency_interest} onChange={(event) => updateProfile("currency_interest", event.target.value as Interest)}><option value="unknown">Не знаю</option><option value="interested">Интересно</option><option value="not_relevant">Неактуально</option></select></label>
                <label>Экспорт / импорт<select value={profile.export_import_interest} onChange={(event) => updateProfile("export_import_interest", event.target.value as Interest)}><option value="unknown">Не знаю</option><option value="interested">Интересно</option><option value="not_relevant">Неактуально</option></select></label>
                <label>Лизинг<select value={profile.leasing_interest} onChange={(event) => updateProfile("leasing_interest", event.target.value as Interest)}><option value="unknown">Не знаю</option><option value="interested">Интересно</option><option value="not_relevant">Неактуально</option></select></label>
                <label>Гарантии<select value={profile.guarantees_interest} onChange={(event) => updateProfile("guarantees_interest", event.target.value as Interest)}><option value="unknown">Не знаю</option><option value="interested">Интересно</option><option value="not_relevant">Неактуально</option></select></label>
                <label>Факторинг<select value={profile.factoring_interest} onChange={(event) => updateProfile("factoring_interest", event.target.value as Interest)}><option value="unknown">Не знаю</option><option value="interested">Интересно</option><option value="not_relevant">Неактуально</option></select></label>
                <label>Расчёты и зарплатные проекты<select value={profile.settlements_interest} onChange={(event) => updateProfile("settlements_interest", event.target.value as Interest)}><option value="unknown">Не знаю</option><option value="interested">Интересно</option><option value="not_relevant">Неактуально</option></select></label>
              </div>
              <label>География рынков и поставок, через запятую<input maxLength={1000} value={profile.market_geographies.join(", ")} onChange={(event) => updateGeographies(event.target.value)} /></label>
              <label>Зависимость от сырья, энергии или поставок<textarea maxLength={1000} rows={2} value={profile.dependencies} onChange={(event) => updateProfile("dependencies", event.target.value)} /></label>
              <fieldset className="profile-tags">
                <legend>Темы, за которыми следить</legend>
                {profileOptions.focus_topics.map((tag) => (
                  <label className="profile-tag" key={tag.id} title={tag.description}>
                    <input type="checkbox" checked={profile.focus_topics.includes(tag.id)} onChange={(event) => toggleTag("focus_topics", tag.id, event.target.checked)} />
                    <span><strong>{tag.label}</strong><small>{tag.description}</small></span>
                  </label>
                ))}
              </fieldset>
              <label>Внешние факторы, влияющие на бизнес<textarea maxLength={1500} rows={2} value={profile.external_factors} onChange={(event) => updateProfile("external_factors", event.target.value)} /></label>
              <label>Дополнительный контекст<textarea maxLength={1500} rows={2} value={profile.context_note} onChange={(event) => updateProfile("context_note", event.target.value)} /></label>
              <button type="submit" disabled={profileBusy}>{profileBusy ? "Сохраняем…" : "Сохранить профиль"}</button>
              <p className="hint">Сайт разбирается кнопкой выше. Найденные значения сразу записываются в пустые поля: проверьте их и сохраните правки.</p>
            </form> : null}
            {editingProfile && profileMeta?.status !== "complete" ? <section className="card">
              <h2>Свежие новости</h2>
              <p className="hint">Профиль ещё заполнен не полностью, поэтому показываем общие материалы без заявления о персональной релевантности.</p>
              {realFeed?.items.length ? <ul className="hits">{realFeed.items.slice(0, 10).map((item) => <li key={item.id}>
                <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a><br />
                <small>{item.source} · {item.published_at}</small>
              </li>)}</ul> : <p>{realFeed ? "В свежем окне пока нет сохранённых публикаций." : "Загружаем свежие публикации…"}</p>}
            </section> : null}
            </>}
          </>
        ) : ready ? (
          <>
            <p className="lead">
              {registering
                ? "Создайте аккаунт, чтобы заполнить профиль компании и получить персональную ленту."
                : "Войдите в свой аккаунт или зарегистрируйтесь."}
            </p>
            <form className="card settings-form auth-form" onSubmit={(event) => void submit(event)}>
              <label>
                Логин
                <input
                  autoComplete="username"
                  minLength={3}
                  maxLength={64}
                  value={login}
                  onChange={(event) => setLogin(event.target.value)}
                  required
                />
              </label>
              <label>
                Пароль
                <input
                  type="password"
                  autoComplete={registering ? "new-password" : "current-password"}
                  minLength={10}
                  maxLength={128}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                />
              </label>
              {registering ? (
                <label>
                  Повторите пароль
                  <input
                    type="password"
                    autoComplete="new-password"
                    minLength={10}
                    maxLength={128}
                    value={confirmPassword}
                    onChange={(event) => setConfirmPassword(event.target.value)}
                    required
                  />
                </label>
              ) : null}
              {error ? <p className="error" role="alert">{error}</p> : null}
              {notice ? <p role="status">{notice}</p> : null}
              <button type="submit" disabled={busy || !csrf}>
                {busy ? "Подождите…" : registering ? "Зарегистрироваться" : "Войти"}
              </button>
            </form>
            <p className="hint auth-hint">
              Локальная регистрация прототипа. Восстановление пароля пока не подключено.
            </p>
            <button className="linkish" type="button" onClick={() => {
              setRegistering((current) => !current);
              setPassword("");
              setConfirmPassword("");
              setError("");
              setNotice("");
            }}>
              {registering ? "Уже есть аккаунт? Войти" : "Нет аккаунта? Зарегистрироваться"}
            </button>
          </>
        ) : null}
      </main>
    </div>
  );
}
