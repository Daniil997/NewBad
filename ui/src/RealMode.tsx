import { useEffect, useRef, useState, type FormEvent } from "react";
import "./real.css";
import { CompanyProfile } from "./CompanyProfile";
import { DataRefreshButton } from "./DataRefresh";
import {
  EMPTY_PROFILE, relevanceInfo,
  type BusinessProfile, type MarketHistory, type RealFeed, type RealMarkets,
  type SavedProfile, type User,
} from "./realTypes";
import {
  CurrencyRatesPanel, isCurrencyRatesNews, MarketsPanel, NewsDetail,
} from "./RealParts";

import { ChatWorkspace } from "./ChatWorkspace";
import { NewsFeedPanel, NewsPageHeader, NewsState, inNewsPeriod } from "./NewsWorkspace";

export function RealMode({
  onHome,
}: {
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
  const [chatOpen, setChatOpen] = useState(false);
  const [chatBusy, setChatBusy] = useState(false);
  const [newsReportBusy, setNewsReportBusy] = useState("");
  const detailRef = useRef<HTMLElement | null>(null);
  const tabInitialized = useRef(false);
  useEffect(() => {
    const refresh = () => { setFeedRefreshKey((key) => key + 1); setMarketHistories({}); };
    window.addEventListener("business-data-updated", refresh);
    return () => window.removeEventListener("business-data-updated", refresh);
  }, []);

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

  useEffect(() => {
    if (!user) return;
    try {
      const saved = JSON.parse(localStorage.getItem(`business-chat-view:${user.id}`) || "null");
      if (typeof saved?.open === "boolean") { setActiveChatId(saved.id || ""); setChatOpen(saved.open); }
    } catch { /* the saved view is optional */ }
  }, [user?.id]);

  function selectChat(id: string) {
    setActiveChatId(id); setChatOpen(true);
    try { localStorage.setItem(`business-chat-view:${user!.id}`, JSON.stringify({ id, open: true })); } catch { /* optional */ }
  }

  function closeChats() {
    setChatOpen(false);
    try { localStorage.setItem(`business-chat-view:${user!.id}`, JSON.stringify({ id: activeChatId, open: false })); } catch { /* optional */ }
  }

  async function loadProfile(): Promise<void> {
    const [response, suggestionsResponse] = await Promise.all([
      fetch("/api/real/profile"), fetch("/api/real/profile/suggestions"),
    ]);
    if (!response.ok) throw new Error("Не удалось загрузить профиль компании");
    const payload = (await response.json()) as { profile: SavedProfile };
    const suggestions = suggestionsResponse.ok ? (await suggestionsResponse.json()).suggestions : [];
    setProfile({ ...EMPTY_PROFILE, ...payload.profile });
    setProfileMeta(payload.profile);
    setEditingProfile(suggestions.length > 0 || payload.profile.status !== "complete");
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
      setActiveChatId(""); setChatOpen(false);
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
      window.setTimeout(() => detailRef.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }), 0);
    }
  }

  function selectNews(id: string) {
    setSelectedNewsId(id);
    closeChats();
    scrollToDetail();
  }

  async function openProfileChat(newsId?: string, newChat = false) {
    setChatBusy(true); setError("");
    try {
      const csrfToken = await refreshCsrf();
      const response = await fetch("/api/real/chat/sessions", {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ news_id: newsId || null, new_chat: newChat }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Не удалось открыть чат");
      selectChat(data.id as string);
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

  function navigateFromProfile(action: () => void) {
    if (profileBusy) return;
    if (profileDirty && !window.confirm("Есть несохранённые изменения профиля. Выйти без сохранения?")) return;
    setProfileDirty(false);
    action();
  }

  /* ───── производные данные для ленты ───── */

  const allItems = realFeed?.items ?? [];
  const currencyRatesItem = allItems.find(isCurrencyRatesNews) ?? null;
  const newsItems = allItems.filter((item) => !isCurrencyRatesNews(item));
  const feedItems = newsItems.filter((item) => {
    if (feedTab === "personal" && !item.analysis?.relevant) return false;
    if (feedCategory !== "all" && item.event_type !== feedCategory) return false;
    if (!inNewsPeriod(item.published_at, feedPeriod)) return false;
    const query = feedQuery.trim().toLocaleLowerCase("ru-RU");
    return !query || `${item.title} ${item.excerpt} ${item.source}`.toLocaleLowerCase("ru-RU").includes(query);
  });
  const feedCategories = [...new Map(newsItems.filter((item) => item.event_type !== "unknown").map((item) => [item.event_type, item.event_title])).entries()];
  const personalCount = newsItems.filter((item) => item.analysis?.relevant).length;
  const selectedItem = newsItems.find((item) => item.id === selectedNewsId) ?? feedItems[0] ?? null;
  const analysisRunning = Boolean(realFeed?.job && ["queued", "running"].includes(realFeed.job.status));
  const profileComplete = profileMeta?.status === "complete";

  /* ───── общие куски разметки ───── */

  const header = (
    <header className="bank-bar">
      <button className="bar-button" type="button" onClick={() => navigateFromProfile(onHome)}>Альфа-Бизнес</button>
      <DataRefreshButton />
      {user ? (
        <div className="bar-user">
          <span>{user.login}</span>
          <button className="bar-button bar-small" type="button" onClick={() => navigateFromProfile(() => void logout())} disabled={busy || profileBusy}>
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
        <main className="bank-main company-profile-main">
          <CompanyProfile
            apiBase="/api/real/profile" initialProfile={profileMeta}
            onBack={() => { setEditingProfile(false); setProfileDirty(false); }}
            onDirtyChange={setProfileDirty} onBusyChange={setProfileBusy}
            onSaved={(saved) => { setProfile({ ...EMPTY_PROFILE, ...saved }); setProfileMeta(saved); }}
          />
        </main>
      </div>
    );
  }

  /* ───── главный экран: лента + разбор ───── */

  return (
    <div className="bank real news-page">
      {header}
      <main className="bank-main">
        <NewsPageHeader company={profile.company_name || user.login}
          context={profileComplete ? "Подборка с учётом профиля компании" : "Заполните профиль для персональной подборки"}
          actions={<>
            <button type="button" className="btn btn-ghost" onClick={() => setEditingProfile(true)}>Профиль компании</button>
            <button type="button" className="btn btn-ghost" onClick={() => { setChatOpen(true); scrollToDetail(); }}>Мои чаты</button>
          </>}
        />
        {banners}
        {!profileComplete ? (
          <div className="nudge">
            <span>Профиль заполнен не полностью, поэтому подборка пока общая. Расскажите о компании, чтобы увидеть, что важно именно вам.</span>
            <button type="button" className="btn btn-primary small" onClick={() => setEditingProfile(true)}>Дополнить профиль</button>
          </div>
        ) : null}

        <div className={`workspace real-workspace${chatOpen ? " has-chat" : ""}`}>
          <NewsFeedPanel personal={feedTab === "personal"} onPersonal={(personal) => setFeedTab(personal ? "personal" : "all")}
            personalCount={personalCount} totalCount={newsItems.length}
            query={feedQuery} onQuery={setFeedQuery} category={feedCategory} onCategory={setFeedCategory} categories={feedCategories}
            period={feedPeriod} onPeriod={setFeedPeriod} periodLabel={`Весь период (${realFeed?.lookback_days ?? 3} дн.)`}
            onRefresh={() => { if (realFeed) void analyzeNewsNow(); else { setError(""); setFeedRefreshKey((value) => value + 1); } }} refreshing={feedBusy || analysisRunning} loading={!realFeed && !error}
            error={!realFeed ? error : undefined}
            status={<>
              {analysisRunning ? <p className="status-line" role="status">Подбираем новости… осталось {realFeed?.pending_count ?? 0}</p> : null}
              {!analysisRunning && Boolean(realFeed?.pending_count) ? <p className="status-line">Новых материалов: {realFeed?.pending_count}. Обновите подборку для оценки связи.</p> : null}
              {realFeed?.job?.status === "failed" ? <p className="inline-error" role="alert">Анализ не завершён. Попробуйте обновить подборку.</p> : null}
            </>}
            items={feedItems.map((item) => ({ id: item.id, title: item.title, date: item.published_at,
              category: item.event_title, source: item.source, excerpt: item.excerpt, score: item.analysis?.score,
              relevance: item.analysis?.relevant ? relevanceInfo(item.analysis.score) : null }))}
            selectedId={selectedItem?.id ?? ""} onSelect={selectNews}
            emptyText={feedTab === "personal" ? "Пока нет новостей со связью с компанией. Обновите подборку или откройте все новости." : "За выбранный период нет сохранённых публикаций."}
          />

          <section className="card real-detail" ref={detailRef} aria-label="Разбор">
            {chatOpen ? (
              <ChatWorkspace activeId={activeChatId} ownerId={user.id} csrf={csrf} companyName={profile.company_name}
                onSelect={selectChat} onCreate={() => void openProfileChat(undefined, true)}
                onBack={closeChats} creating={chatBusy} />
            ) : selectedItem ? (
              <NewsDetail
                item={selectedItem} reportBusy={newsReportBusy === selectedItem.id} chatBusy={chatBusy}
                onReport={(refresh) => void requestNewsReport(selectedItem.id, refresh)}
                onChat={() => void openProfileChat(selectedItem.id)}
              />
            ) : (
              <NewsState loading={!realFeed && !error} title={realFeed ? "Выберите новость" : error ? "Новости недоступны" : "Загружаем новости"} text="Откройте публикацию в ленте — здесь появятся факты, оценка влияния и возможные действия." />
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
