import { useEffect, useState } from "react";
import { BankRecommendation, BANK_RECOMMENDATION_PLACEHOLDER } from "./BankRecommendation";
import { formatDay, type BankRecommendation as Recommendation } from "./realTypes";

type EditorialNews = {
  id: string; title: string; source: string; published_at: string; event_title: string; url: string;
  bank_recommendation: Recommendation;
  legacy_bank_recommendation?: { text: string; page: number | null };
};

type EditorialProfile = {
  id: string; login: string; company_name: string; industry: string; city: string; region: string;
  description: string; products_services: string; status: string; profile_version: number;
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) throw new Error(typeof data?.detail === "string" ? data.detail : "Не удалось выполнить запрос. Проверьте подключение и повторите.");
  return data as T;
}

export function NewsRecommendations({ onDirtyChange, onBusyChange }: {
  onDirtyChange: (value: boolean) => void; onBusyChange: (value: boolean) => void;
}) {
  const [items, setItems] = useState<EditorialNews[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [text, setText] = useState("");
  const [page, setPage] = useState("");
  const [query, setQuery] = useState("");
  const [profiles, setProfiles] = useState<EditorialProfile[]>([]);
  const [profileId, setProfileId] = useState("");
  const [profileQuery, setProfileQuery] = useState("");
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [retry, setRetry] = useState(0);
  const profile = profiles.find((item) => item.id === profileId);
  const selected = items.find((item) => item.id === selectedId);
  const dirty = Boolean(selected && (text !== selected.bank_recommendation.text || page !== String(selected.bank_recommendation.page ?? "")));

  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange(busy); }, [busy, onBusyChange]);
  useEffect(() => () => { onDirtyChange(false); onBusyChange(false); }, [onDirtyChange, onBusyChange]);
  useEffect(() => {
    const controller = new AbortController();
    setProfilesLoading(true);
    void request<{ profiles: EditorialProfile[] }>("/api/settings/news/recommendation-profiles", { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setProfiles(data.profiles); })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setProfilesLoading(false); });
    return () => controller.abort();
  }, [retry]);
  useEffect(() => {
    if (!profileId) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true); setError("");
    void request<{ profile_id: string; items: EditorialNews[] }>(`/api/settings/news/recommendations?profile_id=${encodeURIComponent(profileId)}`, { signal: controller.signal })
      .then((data) => {
        if (controller.signal.aborted) return;
        setItems(data.items);
        const first = data.items.find((item) => item.id === selectedId) ?? data.items[0];
        setSelectedId(first?.id ?? ""); setText(first?.bank_recommendation.text ?? "");
        setPage(String(first?.bank_recommendation.page ?? ""));
      })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [retry, profileId]);

  function changeProfile(next: string) {
    if (busy || next === profileId) return;
    if (dirty && !window.confirm("Есть несохранённая оценка. Выбрать другой профиль без сохранения?")) return;
    setItems([]); setText(""); setPage("");
    setProfileId(next); setNotice(""); setError("");
  }

  function select(item: EditorialNews) {
    if (busy || item.id === selectedId) return;
    if (dirty && !window.confirm("Есть несохранённая рекомендация. Переключиться на другую новость без сохранения?")) return;
    setSelectedId(item.id); setText(item.bank_recommendation.text);
    setPage(String(item.bank_recommendation.page ?? "")); setNotice(""); setError("");
  }

  async function save() {
    if (!selected || !profile || busy || !dirty) return;
    setBusy(true); setError(""); setNotice("");
    try {
      const csrf = await request<{ csrf_token: string }>("/api/auth/csrf");
      const saved = await request<{ bank_recommendation: Recommendation }>(`/api/settings/news/${encodeURIComponent(selected.id)}/recommendation`, {
        method: "PUT", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf.csrf_token },
        body: JSON.stringify({ profile_id: profileId, text, page: text.trim() && page !== "" ? Number(page) : null }),
      });
      setItems((current) => current.map((item) => item.id === selected.id ? { ...item, bank_recommendation: saved.bank_recommendation } : item));
      setText(saved.bank_recommendation.text); setPage(String(saved.bank_recommendation.page ?? ""));
      setNotice(saved.bank_recommendation.text ? `Оценка сохранена для компании «${profile.company_name || profile.login}».` : "Ручная оценка удалена для выбранного профиля. Подходящая аналитика банка будет подобрана автоматически.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  const needle = query.trim().toLocaleLowerCase("ru");
  const visible = items.filter((item) => !needle || `${item.title} ${item.source} ${item.event_title}`.toLocaleLowerCase("ru").includes(needle));
  const profileNeedle = profileQuery.trim().toLocaleLowerCase("ru");
  const matchingProfiles = profiles.filter((item) => `${item.company_name} ${item.login} ${item.industry} ${item.city} ${item.region}`.toLocaleLowerCase("ru").includes(profileNeedle));
  const profileOptions = profile && !matchingProfiles.some((item) => item.id === profile.id) ? [profile, ...matchingProfiles] : matchingProfiles;

  return <div className="dev-recommendations">
    <p className="dev-description">Выберите профиль компании и новость. Индивидуальная оценка Альфа-Бизнеса будет видна только выбранной компании.</p>
    {error ? <div className="dev-alert is-error" role="alert">{error}<button type="button" className="dev-button is-secondary" disabled={busy || dirty} onClick={() => setRetry((value) => value + 1)}>Повторить загрузку</button></div> : null}
    {notice ? <div className="dev-alert is-success" role="status">{notice}</div> : null}
    <div className="dev-editor-grid">
      <section className="dev-card dev-editor-list" aria-label="Профиль и новость для оценки">
        <h3>Выберите профиль компании</h3>
        <label>Поиск профиля компании<input type="search" value={profileQuery} onChange={(event) => setProfileQuery(event.target.value)} placeholder="Компания, аккаунт, отрасль или город" disabled={busy || profilesLoading} /></label>
        <label>Профиль компании<select value={profileId} onChange={(event) => changeProfile(event.target.value)} disabled={busy || profilesLoading}>
          <option value="">{profilesLoading ? "Загружаем профили…" : "Выберите профиль"}</option>
          {profileOptions.map((item) => <option key={item.id} value={item.id}>{item.company_name || "Компания без названия"} · {item.login}{item.city ? ` · ${item.city}` : ""}</option>)}
        </select></label>
        {!profilesLoading ? <p className="dev-description" role="status">{profiles.length ? `Найдено профилей: ${matchingProfiles.length}` : "Реальных профилей пока нет. Они появятся после регистрации компаний в реальном режиме."}</p> : null}
        <h3 className="dev-editor-news-heading">Выберите новость</h3>
        <label>Поиск по новостям<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Заголовок, источник или тема" disabled={!profileId || loading} /></label>
        {loading && profileId ? <p className="dev-description" role="status">Загружаем оценки компании…</p> : profileId ? <p className="dev-description" role="status">Найдено новостей: {visible.length}</p> : <p className="dev-description">Сначала выберите профиль компании.</p>}
        <ul>{visible.map((item) => <li key={item.id}><button type="button" disabled={busy || loading} aria-current={item.id === selectedId ? "true" : undefined} onClick={() => select(item)}>
          <span>{item.event_title} · {formatDay(item.published_at)}</span><strong>{item.title}</strong>
          <span>{item.source}</span><small>{item.bank_recommendation.text ? "Оценка для профиля добавлена" : "Заглушка для профиля"}</small>
        </button></li>)}</ul>
        {profileId && !loading && !visible.length ? <p className="dev-description">{items.length ? "Измените поисковый запрос." : "Собранных новостей пока нет. Запустите сбор в разделе «Новости»."}</p> : null}
      </section>
      {selected && profile ? <form className="dev-card dev-recommendation-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <p className="dev-eyebrow">Реальный режим · Индивидуальная оценка</p><h3>{selected.title}</h3>
        <div className="dev-assessment-company"><strong>{profile.company_name || profile.login}</strong>
          <p>{[profile.industry, profile.region, profile.city].filter(Boolean).join(" · ") || "Сведения о деятельности пока не заполнены"}</p>
          {profile.description ? <p>{profile.description}</p> : null}
          {profile.products_services ? <p>Товары и услуги: {profile.products_services}</p> : null}
          <small>Профиль: {profile.login} · версия {profile.profile_version}</small>
        </div>
        <p className="dev-description">{selected.source} · {formatDay(selected.published_at)}{selected.url ? <> · <a href={selected.url} target="_blank" rel="noreferrer">Первоисточник ↗</a></> : null}</p>
        {selected.legacy_bank_recommendation?.text ? <details className="dev-legacy-assessment"><summary>Ранее сохранённый общий текст</summary><p>{selected.legacy_bank_recommendation.text}</p><button type="button" className="dev-button is-secondary" disabled={busy} onClick={() => {
          if (text.trim() && !window.confirm("Заменить текущий текст прежней общей рекомендацией?")) return;
          setText(selected.legacy_bank_recommendation!.text); setPage(String(selected.legacy_bank_recommendation!.page ?? "")); setNotice("");
        }}>Использовать для этого профиля</button></details> : null}
        <label>Индивидуальная оценка<textarea rows={8} maxLength={10000} value={text} onChange={(event) => { setText(event.target.value); setNotice(""); }} placeholder={BANK_RECOMMENDATION_PLACEHOLDER} disabled={busy} /><small>{text.length} / 10 000. Пустой текст возвращает заглушку только для этого профиля.</small></label>
        {selected.bank_recommendation.origin === "corpus" ? <p className="dev-description">Текст подобран из аналитики банка с учётом профиля. Сохранённая ручная оценка получит приоритет над автоматическим подбором.</p> : null}
        <div className="dev-recommendation-preview"><p className="dev-eyebrow">Как увидит выбранная компания</p><BankRecommendation recommendation={{ text, page: page !== "" ? Number(page) : null, updated_at: null }} /></div>
        <div className="dev-editor-actions"><button type="submit" className="dev-button" disabled={!dirty || busy}>{busy ? "Сохраняем…" : "Сохранить оценку"}</button>
          <button type="button" className="dev-button is-secondary" disabled={!dirty || busy} onClick={() => { setText(selected.bank_recommendation.text); setPage(String(selected.bank_recommendation.page ?? "")); setError(""); setNotice(""); }}>Отменить изменения</button>
        </div>
        {selected.bank_recommendation.updated_at ? <p className="dev-description">Сохранено: {new Date(selected.bank_recommendation.updated_at).toLocaleString("ru-RU")}</p> : null}
      </form> : <section className="dev-card"><h3>{profileId ? "Выберите новость" : "Выберите профиль компании"}</h3><p className="dev-description">Здесь появятся контекст компании и редактор индивидуальной оценки выбранной новости.</p></section>}
    </div>
  </div>;
}
