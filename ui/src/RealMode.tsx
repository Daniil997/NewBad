import { useEffect, useRef, useState, type FormEvent } from "react";
import "./real.css";
import { ModeSwitch, type AppMode } from "./ModeSwitch";
import {
  EMPTY_PROFILE, PROFILE_FIELD_LABELS, profilePayload,
  type BusinessProfile, type MarketHistory, type ProfileOptions, type RealFeed, type RealMarkets,
  type SavedProfile, type SiteSuggestion, type User,
} from "./realTypes";
import {
  ChatPanel, CurrencyRatesPanel, isCurrencyRatesNews, MarketsPanel, NewsDetail, NewsList, ProfileForm, SuggestionsPanel,
} from "./RealParts";

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
  const [showPassword, setShowPassword] = useState(false);
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
  const [selectedNewsId, setSelectedNewsId] = useState("");
  const [activeChatId, setActiveChatId] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [newsReportBusy, setNewsReportBusy] = useState("");
  const detailRef = useRef<HTMLElement | null>(null);
  const tabInitialized = useRef(false);

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

  // Пока профиль не заполнен, персональная вкладка пуста — открываем «Все новости» один раз.
  useEffect(() => {
    if (realFeed && !tabInitialized.current) {
      tabInitialized.current = true;
      if (!realFeed.personalization_available) setFeedTab("all");
    }
  }, [realFeed]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 10000);
    return () => window.clearTimeout(timer);
  }, [notice]);

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
      setSelectedNewsId("");
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

  function scrollToDetail() {
    if (window.matchMedia("(max-width: 860px)").matches) {
      window.setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 0);
    }
  }

  function selectNews(id: string) {
    setSelectedNewsId(id);
    setActiveChatId("");
    scrollToDetail();
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
      scrollToDetail();
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
      const complete = payload.profile.status === "complete";
      setEditingProfile(!complete);
      setNotice(complete ? "Профиль сохранён. Подборка новостей обновится под вашу компанию." : "Черновик профиля сохранён. Заполните название и описание, чтобы завершить профиль.");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить профиль");
      window.scrollTo({ top: 0, behavior: "smooth" });
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

  /* ───── производные данные для ленты ───── */

  const allItems = realFeed?.items ?? [];
  const currencyRatesItem = allItems.find(isCurrencyRatesNews) ?? null;
  const newsItems = allItems.filter((item) => !isCurrencyRatesNews(item));
  const feedItems = newsItems.filter((item) => {
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
  const feedCategories = [...new Map(newsItems.filter((item) => item.event_type !== "unknown").map((item) => [item.event_type, item.event_title])).entries()];
  const personalCount = newsItems.filter((item) => item.analysis?.relevant).length;
  const selectedItem = newsItems.find((item) => item.id === selectedNewsId) ?? feedItems[0] ?? null;
  const analysisRunning = Boolean(realFeed?.job && ["queued", "running"].includes(realFeed.job.status));
  const filtersActive = feedCategory !== "all" || feedPeriod !== "window";
  const profileComplete = profileMeta?.status === "complete";

  /* ───── общие куски разметки ───── */

  const header = (
    <header className="bank-bar">
      <button className="bar-button" type="button" onClick={onHome}>Альфа-Бизнес</button>
      <ModeSwitch mode={mode} onChange={onModeChange} />
      {user ? (
        <div className="bar-user">
          <span>{user.login}</span>
          <button className="bar-button bar-small" type="button" onClick={() => void logout()} disabled={busy || profileBusy}>
            {busy ? "Выходим…" : "Выйти"}
          </button>
        </div>
      ) : null}
    </header>
  );

  const banners = (
    <div className="banners">
      {error ? (
        <div className="banner banner-error" role="alert">
          <span>{error}</span>
          <button type="button" aria-label="Закрыть сообщение" onClick={() => setError("")}>×</button>
        </div>
      ) : null}
      {notice ? (
        <div className="banner banner-ok" role="status">
          <span>{notice}</span>
          <button type="button" aria-label="Закрыть сообщение" onClick={() => setNotice("")}>×</button>
        </div>
      ) : null}
    </div>
  );

  /* ───── вход / регистрация ───── */

  if (!user) {
    return (
      <div className="bank real">
        {header}
        <main className="bank-main narrow">
          {!ready ? <p className="hint">Проверяем сессию…</p> : (
            <section className="card auth-card">
              <p className="eyebrow">Что важно для вашей компании</p>
              <h1>{registering ? "Создайте аккаунт" : "Вход"}</h1>
              <p className="lead">
                {registering
                  ? "Заполните профиль компании — и лента новостей станет персональной."
                  : "Войдите, чтобы увидеть новости, важные именно для вашего бизнеса."}
              </p>
              <form className="auth-form-real" onSubmit={(event) => void submit(event)}>
                <label className="field">
                  <span className="field-label">Логин</span>
                  <input className="input" autoComplete="username" minLength={3} maxLength={64} value={login} onChange={(event) => setLogin(event.target.value)} required />
                  {registering ? <small className="field-hint">От 3 символов</small> : null}
                </label>
                <label className="field">
                  <span className="field-label">Пароль</span>
                  <input className="input" type={showPassword ? "text" : "password"} autoComplete={registering ? "new-password" : "current-password"} minLength={10} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} required />
                  {registering ? <small className="field-hint">От 10 символов</small> : null}
                </label>
                {registering ? (
                  <label className="field">
                    <span className="field-label">Повторите пароль</span>
                    <input className="input" type={showPassword ? "text" : "password"} autoComplete="new-password" minLength={10} maxLength={128} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} required />
                  </label>
                ) : null}
                <label className="check"><input type="checkbox" checked={showPassword} onChange={(event) => setShowPassword(event.target.checked)} /><span>Показать пароль</span></label>
                {banners}
                <button type="submit" className="btn btn-primary wide" disabled={busy || !csrf}>
                  {busy ? "Подождите…" : registering ? "Зарегистрироваться" : "Войти"}
                </button>
              </form>
              <p className="hint small">Локальная регистрация прототипа. Восстановление пароля пока не подключено.</p>
              <button className="btn-link" type="button" onClick={() => {
                setRegistering((current) => !current);
                setPassword("");
                setConfirmPassword("");
                setError("");
                setNotice("");
              }}>
                {registering ? "Уже есть аккаунт? Войти" : "Нет аккаунта? Зарегистрироваться"}
              </button>
            </section>
          )}
        </main>
      </div>
    );
  }

  /* ───── профиль компании ───── */

  if (editingProfile) {
    return (
      <div className="bank real">
        {header}
        <main className="bank-main form-main">
          <div className="real-head">
            <div>
              <h1>Профиль компании</h1>
              <p className="session-line">
                {profileMeta ? `${profileComplete ? "Профиль заполнен" : "Профиль заполнен частично"} · версия ${profileMeta.profile_version}` : "Расскажите о компании"}
              </p>
            </div>
            <div className="real-head-actions">
              <button type="button" className="btn btn-ghost" onClick={returnToNews}>← К новостям</button>
            </div>
          </div>
          {banners}
          <SuggestionsPanel
            suggestions={siteSuggestions} warnings={siteWarnings} busy={siteBusy} dirty={profileDirty}
            label={suggestionLabel} fieldLabels={PROFILE_FIELD_LABELS} onDecide={(suggestion, accept) => void decideSuggestion(suggestion, accept)}
          />
          <ProfileForm
            profile={profile} options={profileOptions} busy={profileBusy} dirty={profileDirty}
            siteConsent={siteConsent} siteBusy={siteBusy} onConsent={setSiteConsent} onAnalyzeSite={() => void analyzeWebsite()}
            onChange={updateProfile} onToggleTag={toggleTag} onSubmit={(event) => void saveProfile(event)}
          />
        </main>
      </div>
    );
  }

  /* ───── главный экран: лента + разбор ───── */

  return (
    <div className="bank real">
      {header}
      <main className="bank-main">
        <div className="real-head">
          <div>
            <h1>Что важно для вашей компании</h1>
            <p className="session-line">
              {profile.company_name || user.login}
              {profileMeta ? ` · ${profileComplete ? "профиль заполнен" : "профиль заполнен частично"}` : ""}
            </p>
          </div>
          <div className="real-head-actions">
            <button type="button" className="btn btn-ghost" onClick={() => setEditingProfile(true)}>Профиль компании</button>
            <button type="button" className="btn btn-primary" onClick={() => void openProfileChat()} disabled={chatBusy}>
              {chatBusy ? "Открываем чат…" : "Спросить о бизнесе"}
            </button>
          </div>
        </div>
        {banners}
        {!profileComplete ? (
          <div className="nudge">
            <span>Профиль заполнен не полностью, поэтому подборка пока общая. Расскажите о компании, чтобы увидеть, что важно именно вам.</span>
            <button type="button" className="btn btn-primary small" onClick={() => setEditingProfile(true)}>Дополнить профиль</button>
          </div>
        ) : null}

        <div className="workspace real-workspace">
          <section className="card feed-card" aria-label="Новости">
            <div className="seg-tabs" role="tablist" aria-label="Какие новости показывать">
              <button type="button" role="tab" aria-selected={feedTab === "personal"} onClick={() => setFeedTab("personal")}>Для вас{realFeed ? <span className="count">{personalCount}</span> : null}</button>
              <button type="button" role="tab" aria-selected={feedTab === "all"} onClick={() => setFeedTab("all")}>Все новости{realFeed ? <span className="count">{allItems.length}</span> : null}</button>
            </div>

            {realFeed ? (
              <p className="hint small">
                {feedTab === "personal"
                  ? "Новости, у которых нашлась понятная связь с вашей компанией. "
                  : "Все свежие официальные публикации. "}
                За последние {realFeed.lookback_days} дн.
              </p>
            ) : <p className="hint">Загружаем ленту…</p>}

            {analysisRunning ? <p className="status-line" role="status">Подбираем новости под вашу компанию… осталось {realFeed?.pending_count ?? 0}</p> : null}
            {!analysisRunning && realFeed && realFeed.pending_count > 0 ? (
              <p className="status-line" role="status">Появились новые материалы: {realFeed.pending_count}. Обновите подборку, чтобы увидеть связь с вашей компанией.</p>
            ) : null}
            {realFeed?.job?.status === "failed" ? <p className="inline-error">Последний анализ завершился с ошибкой. Попробуйте запустить ещё раз.</p> : null}

            <div className="feed-tools">
              <input className="input" type="search" value={feedQuery} onChange={(event) => setFeedQuery(event.target.value)} placeholder="Поиск по теме или источнику" aria-label="Поиск по новостям" />
              <button type="button" className="btn btn-ghost" onClick={() => void analyzeNewsNow()} disabled={feedBusy || analysisRunning}>
                {feedBusy ? "Ставим в очередь…" : "Обновить подборку"}
              </button>
            </div>
            <details className="filters" open={filtersActive}>
              <summary>Фильтры{filtersActive ? " · включены" : ""}</summary>
              <div className="filters-grid">
                <label className="field"><span className="field-label">Категория</span>
                  <select className="input" value={feedCategory} onChange={(event) => setFeedCategory(event.target.value)}>
                    <option value="all">Все категории</option>
                    {feedCategories.map(([category, title]) => <option key={category} value={category}>{title}</option>)}
                  </select>
                </label>
                <label className="field"><span className="field-label">Период</span>
                  <select className="input" value={feedPeriod} onChange={(event) => setFeedPeriod(event.target.value)}>
                    <option value="window">Весь период ({realFeed?.lookback_days ?? 3} дн.)</option>
                    <option value="day">Сегодня</option>
                    <option value="three_days">3 дня</option>
                  </select>
                </label>
              </div>
            </details>

            <NewsList
              items={feedItems} selectedId={selectedItem?.id ?? ""} onSelect={selectNews}
              emptyText={!realFeed ? "" : feedTab === "personal"
                ? (personalCount === 0 && allItems.length > 0 ? "Пока нет новостей со связью с вашей компанией. Нажмите «Обновить подборку» или загляните во «Все новости»." : "По вашему запросу ничего не найдено.")
                : "За выбранный период нет сохранённых новостей из подключённых источников."}
            />
          </section>

          <section className="card real-detail" ref={detailRef} aria-label="Разбор">
            {activeChatId ? (
              <ChatPanel
                sessionId={activeChatId} csrf={csrf} companyName={profile.company_name}
                onBack={() => setActiveChatId("")} backLabel={selectedItem ? "К новости" : "К новостям"}
              />
            ) : selectedItem ? (
              <NewsDetail
                item={selectedItem} reportBusy={newsReportBusy === selectedItem.id} chatBusy={chatBusy}
                onReport={(refresh) => void requestNewsReport(selectedItem.id, refresh)}
                onChat={() => void openProfileChat(selectedItem.id)}
              />
            ) : (
              <p className="empty">{realFeed ? "Выберите новость слева — здесь появится, что она значит для вашей компании." : "Загружаем…"}</p>
            )}
          </section>
        </div>

        <div className="secondary-grid">
          <MarketsPanel markets={realMarkets} histories={marketHistories} historyBusy={marketHistoryBusy} onToggle={(category, instrument) => void toggleMarketHistory(category, instrument)} />
          {currencyRatesItem ? <CurrencyRatesPanel item={currencyRatesItem} /> : <section className="card side-card currency-rates"><h2>Курсы валют</h2><p className="empty">Данные курсов валют пока не загружены.</p></section>}
        </div>
      </main>
    </div>
  );
}
