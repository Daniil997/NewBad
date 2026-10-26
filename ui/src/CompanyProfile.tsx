import { useEffect, useState, type ReactNode } from "react";
import { SuggestionsPanel, ProfileSourcesPanel } from "./RealParts";
import { OrganizationDetails, CompanyMetrics, BankProducts, CustomsDetails, CounterpartiesEditor } from "./ProfileDetails";
import {
  EMPTY_PROFILE, PROFILE_FIELD_LABELS, PROFILE_MINIMUM_HINT, profilePayload, profileProgress, formatDateTime,
  type BusinessProfile, type Interest, type ProfileOptions, type SavedProfile, type SiteSuggestion,
} from "./realTypes";
import "./profile.css";

type Section = "about" | "activity" | "metrics" | "bank" | "finance" | "markets" | "connections" | "sources";
const SECTIONS: { id: Section; label: string; hint: string; icon: string }[] = [
  { id: "about", label: "О компании", hint: "Название и география", icon: "M4 21V5l8-3 8 3v16 M8 8h1 M15 8h1 M8 12h1 M15 12h1 M10 21v-5h4v5" },
  { id: "activity", label: "Деятельность", hint: "Отрасль, продукты и клиенты", icon: "M4 8h16v12H4z M9 8V4h6v4 M4 13h16 M10 13v3h4v-3" },
  { id: "metrics", label: "Показатели и группа", hint: "Выручка, сотрудники и связи", icon: "M4 20V10h4v10 M10 20V4h4v16 M16 20v-7h4v7" },
  { id: "bank", label: "Банковские продукты", hint: "Действующие услуги компании", icon: "M3 9l9-6 9 6 M4 10h16 M6 10v8 M12 10v8 M18 10v8 M3 21h18" },
  { id: "finance", label: "Финансовые интересы", hint: "Что актуально для бизнеса", icon: "M3 9l9-6 9 6 M4 10h16 M6 10v8 M12 10v8 M18 10v8 M3 21h18" },
  { id: "markets", label: "Рынки и риски", hint: "Темы, поставки и зависимости", icon: "M3 18l6-7 4 3 7-10 M15 4h5v5 M3 22h18" },
  { id: "connections", label: "Контрагенты", hint: "Платежи и связанные компании", icon: "M12 8V3 M5 16v-4h14v4 M12 8v8 M2 16h6v5H2z M9 16h6v5H9z M16 16h6v5h-6z" },
  { id: "sources", label: "Сайт и источники", hint: "Заполнение с подтверждением", icon: "M9 14l6-6 M8 9H6a4 4 0 0 0 0 8h4 M14 7h4a4 4 0 0 1 0 8h-2" },
];
type InterestKey = { [K in keyof BusinessProfile]: BusinessProfile[K] extends Interest ? K : never }[keyof BusinessProfile];
const INTERESTS: { key: InterestKey; label: string; description: string }[] = [
  { key: "credit_interest", label: "Кредитование и ставки", description: "Стоимость заёмных денег и условия финансирования" },
  { key: "deposit_interest", label: "Депозиты и ликвидность", description: "Размещение свободных средств и управление остатками" },
  { key: "currency_interest", label: "Валютные операции", description: "Курсы, конвертация и валютное регулирование" },
  { key: "export_import_interest", label: "Экспорт и импорт", description: "Внешняя торговля, пошлины и международные расчёты" },
  { key: "leasing_interest", label: "Лизинг", description: "Оборудование, транспорт и обновление основных средств" },
  { key: "guarantees_interest", label: "Банковские гарантии", description: "Участие в закупках и исполнение контрактов" },
  { key: "factoring_interest", label: "Факторинг", description: "Финансирование поставок с отсрочкой оплаты" },
  { key: "settlements_interest", label: "Расчёты и зарплата", description: "Платежи, расчётные счета и зарплатные проекты" },
];
const FIELD_LABELS = { ...PROFILE_FIELD_LABELS, website: "Сайт компании", okved: "ОКВЭД", size_band: "Размер компании", business_stage: "Этап бизнеса", context_note: "Дополнительный контекст", focus_topics: "Темы новостей", external_factors: "Внешние факторы" };
type TextField = "company_name" | "website" | "country" | "region" | "city" | "industry" | "okved" | "size_band" | "business_stage" | "customer_types" | "business_model";
type ProfileResponse = { profile: SavedProfile; suggestions?: SiteSuggestion[]; sources?: SiteSuggestion[]; filled_fields?: string[] };
type Props = {
  apiBase: string; initialProfile?: SavedProfile | null;
  onBack: () => void; onSaved: (profile: SavedProfile) => void;
  onDirtyChange: (dirty: boolean) => void; onBusyChange: (busy: boolean) => void;
};
async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    const fields = (data?.fields ?? []).map((field: string) => FIELD_LABELS[field.replace(/^body\./, "") as keyof typeof FIELD_LABELS] || field.replace(/^body\./, ""));
    throw new Error(`${typeof data?.detail === "string" ? data.detail : "Не удалось выполнить запрос"}${fields.length ? `: ${fields.join(", ")}` : ""}`);
  }
  return data as T;
}
async function mutation<T>(url: string, body?: unknown, method = "POST"): Promise<T> {
  const csrf = await api<{ csrf_token: string }>("/api/auth/csrf");
  return api<T>(url, { method, headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf.csrf_token }, body: body === undefined ? undefined : JSON.stringify(body) });
}
function Field({ label, hint, required, minimum, wide, children }: { label: string; hint?: string; required?: boolean; minimum?: string; wide?: boolean; children: ReactNode }) {
  return <label className={`cp-field${wide ? " cp-wide" : ""}`}><span>{label}{required && <em> *</em>}{minimum && <span className="cp-minimum-badge">{minimum}</span>}</span>{children}{hint && <small>{hint}</small>}</label>;
}
function Card({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return <section className="cp-card"><div className="cp-card-head"><h3>{title}</h3>{description && <p>{description}</p>}</div>{children}</section>;
}
function Tags({ options, selected, onChange }: { options: ProfileOptions["industry_tags"]; selected: string[]; onChange: (items: string[]) => void }) {
  return <div className="cp-tags">{options.map((option) => <label className={selected.includes(option.id) ? "is-selected" : ""} key={option.id} title={option.description}><input type="checkbox" checked={selected.includes(option.id)} onChange={(e) => onChange(e.target.checked ? [...selected, option.id] : selected.filter((id) => id !== option.id))} /><span>{option.label}</span></label>)}</div>;
}
function commaList(value: string) { return [...new Set(value.split(",").map((part) => part.trim()).filter(Boolean))]; }

export function CompanyProfile({ apiBase, initialProfile, onBack, onSaved, onDirtyChange, onBusyChange }: Props) {
  const [section, setSection] = useState<Section>("about");
  const [profile, setProfile] = useState<BusinessProfile>({ ...EMPTY_PROFILE, ...initialProfile });
  const [saved, setSaved] = useState<SavedProfile | null>(initialProfile ?? null);
  const [options, setOptions] = useState<ProfileOptions>({ industry_tags: [], focus_topics: [] });
  const [suggestions, setSuggestions] = useState<SiteSuggestion[]>([]);
  const [sources, setSources] = useState<SiteSuggestion[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [pages, setPages] = useState<{ url: string; title: string }[] | null>(null);
  const [openedUrl, setOpenedUrl] = useState("");
  const [consent, setConsent] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [geoText, setGeoText] = useState(initialProfile?.market_geographies.join(", ") ?? "");
  const [connectionDraft, setConnectionDraft] = useState(false);
  const [editReset, setEditReset] = useState(0);
  const dirty = connectionDraft || saved !== null && JSON.stringify(profilePayload(profile)) !== JSON.stringify(profilePayload({ ...EMPTY_PROFILE, ...saved }));
  const locked = loading || !!busy;
  const progress = profileProgress(profile);
  const percent = Math.round(progress.done / progress.total * 100);
  const complete = !!profile.company_name.trim() && (!!profile.description.trim() || profile.description_skipped);
  const currentSection = SECTIONS.find((item) => item.id === section)!;
  const initials = (profile.company_name.match(/[\p{L}\p{N}]+/gu) ?? ["Компания"]).slice(0, 2).map((part) => part[0]).join("").toUpperCase();

  function adopt(data: ProfileResponse, notify = true) {
    setProfile({ ...EMPTY_PROFILE, ...data.profile }); setSaved(data.profile);
    if (data.suggestions) setSuggestions(data.suggestions);
    if (data.sources) setSources(data.sources);
    if (notify) onSaved(data.profile);
  }
  async function load(signal?: AbortSignal) {
    setLoading(true); setError("");
    const results = await Promise.allSettled([
      api<ProfileResponse>(apiBase, { signal }), api<ProfileOptions>(`${apiBase}/options`, { signal }),
      api<{ suggestions: SiteSuggestion[] }>(`${apiBase}/suggestions`, { signal }), api<{ sources: SiteSuggestion[] }>(`${apiBase}/sources`, { signal }),
    ]);
    if (signal?.aborted) return;
    const [p, o, s, a] = results;
    if (p.status === "fulfilled") adopt(p.value, false);
    if (o.status === "fulfilled") setOptions(o.value);
    if (s.status === "fulfilled") setSuggestions(s.value.suggestions);
    if (a.status === "fulfilled") setSources(a.value.sources);
    const failed = results.filter((result) => result.status === "rejected");
    if (failed.length) setError(failed.map((result) => (result.reason as Error).message).join(". "));
    setLoading(false);
  }
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, [apiBase]);
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { onBusyChange(!!busy); return () => onBusyChange(false); }, [busy, onBusyChange]);
  useEffect(() => {
    if (!dirty) return;
    const prevent = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);
  useEffect(() => {
    if (commaList(geoText).join("|") !== profile.market_geographies.join("|")) setGeoText(profile.market_geographies.join(", "));
  }, [profile.market_geographies]);

  function change<K extends keyof BusinessProfile>(key: K, value: BusinessProfile[K]) {
    setProfile((current) => ({ ...current, [key]: value, ...(key === "description" && value ? { description_skipped: false } : {}) }));
    if (key === "website") { setConsent(false); setPages(null); setOpenedUrl(""); setWarnings([]); }
    setNotice("");
  }
  async function persist(): Promise<ProfileResponse | null> {
    if (connectionDraft) { setError("Добавьте редактируемую связь в профиль или отмените её редактирование, затем сохраните профиль."); return null; }
    setBusy("save"); setError(""); setNotice("");
    try {
      const data = await mutation<ProfileResponse>(apiBase, profilePayload(profile), "PUT");
      adopt(data);
      setNotice(data.profile.status === "complete" ? "Минимум сохранён. Персональная подборка и подробный разбор доступны." : `Черновик сохранён. ${PROFILE_MINIMUM_HINT}`);
      return data;
    } catch (reason) { setError((reason as Error).message); return null; }
    finally { setBusy(""); }
  }
  function cancel() {
    if (saved) { setProfile({ ...EMPTY_PROFILE, ...saved }); setGeoText(saved.market_geographies.join(", ")); }
    setEditReset((value) => value + 1); setConnectionDraft(false);
    setConsent(false); setError(""); setNotice("");
  }
  function navigateSection(next: Section) {
    if (next !== section && connectionDraft && !window.confirm("Редактирование платёжной связи не завершено. Перейти без этих изменений?")) return;
    setSection(next);
  }
  function back() {
    if (locked) return;
    if (dirty && !window.confirm("Есть несохранённые изменения профиля. Выйти без сохранения?")) return;
    onDirtyChange(false); onBack();
  }
  async function scan() {
    if (!consent || !profile.website.trim()) return;
    if (dirty && !await persist()) return;
    setBusy("site"); setError(""); setNotice(""); setWarnings([]); setPages(null); setOpenedUrl("");
    try {
      const data = await mutation<{ status: string; profile?: SavedProfile; suggestions?: SiteSuggestion[]; warnings?: string[]; pages?: { url: string; title: string }[]; opened_url?: string }>(`${apiBase}/site-analysis`, { consent: true });
      if (data.profile) adopt({ profile: data.profile });
      setSuggestions(data.suggestions ?? []); setWarnings(data.warnings ?? []); setPages(data.pages ?? []); setConsent(false);
      setOpenedUrl(data.opened_url ?? "");
      setNotice(data.status === "no_pages" ? "Сайт не удалось прочитать. Подробности указаны ниже; вы можете заполнить профиль вручную или указать другую публичную страницу компании."
        : data.status === "profile_changed" ? "Адрес сайта изменился. Запустите изучение нового сайта."
        : data.suggestions?.length ? `Найдено предложений: ${data.suggestions.length}. Проверьте цитаты и подтвердите подходящие данные.` : "Изучение сайта завершено. Новых подтверждаемых данных для пустых полей не найдено.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }
  async function decide(suggestion?: SiteSuggestion, accept = true) {
    if (dirty) { setError("Сохраните или отмените изменения перед подтверждением сведений."); return; }
    setBusy("review"); setError(""); setNotice("");
    try {
      const data = await mutation<ProfileResponse>(`${apiBase}/suggestions/${suggestion ? "decision" : "apply"}`, suggestion ? { field_name: suggestion.field_name, source_url: suggestion.source_url, accept } : undefined);
      adopt(data);
      setNotice(suggestion && !accept ? "Предложение отклонено." : "Подтверждённые сведения добавлены в профиль.");
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(""); }
  }
  function label(field: string, value: string | string[]) {
    if (!Array.isArray(value)) return value;
    const tags = field === "industry_tags" ? options.industry_tags : options.focus_topics;
    return value.map((id) => tags.find((item) => item.id === id)?.label ?? id).join(", ");
  }
  function text(key: TextField, placeholder = "", maxLength = 200) {
    return <input value={String(profile[key] ?? "")} aria-required={key === "company_name" || undefined} placeholder={placeholder} maxLength={maxLength} onChange={(e) => change(key, e.target.value)} />;
  }
  function area(key: "description" | "products_services" | "dependencies" | "external_factors" | "context_note", placeholder: string, maxLength: number, rows = 3) {
    return <textarea rows={rows} maxLength={maxLength} placeholder={placeholder} value={profile[key]} aria-required={key === "description" && !profile.description_skipped || undefined} disabled={key === "description" && profile.description_skipped} onChange={(e) => change(key, e.target.value)} />;
  }

  return <div className="cp-page">
    <div className="cp-topline"><button type="button" className="cp-back" onClick={back} disabled={locked}>← К новостям</button><span className="cp-mode">Профиль компании</span></div>
    <div className="cp-heading"><div><p className="cp-eyebrow">Компания / Профиль</p><h1>Профиль компании</h1><p>Расскажите о бизнесе, чтобы получать подходящие новости и аналитику.</p></div></div>
    <div className="cp-layout">
      <aside className="cp-sidebar">
        <div className="cp-identity"><div className="cp-avatar" aria-hidden="true">{initials}</div><strong>{profile.company_name || "Ваша компания"}</strong><span>{[profile.city || profile.region, profile.industry].filter(Boolean).join(" · ") || "Добавьте сведения о бизнесе"}</span><div className={`cp-readiness ${complete ? "is-ready" : ""}`}><i />{complete ? dirty ? "Минимум заполнен — сохраните профиль" : "Готов к подборке и разбору" : "Не заполнен минимум для запуска"}</div></div>
        <nav className="cp-nav" aria-label="Разделы профиля">{SECTIONS.map((item) => <button type="button" key={item.id} aria-current={section === item.id ? "page" : undefined} className={section === item.id ? "is-active" : ""} onClick={() => navigateSection(item.id)}><svg width="19" height="19" viewBox="0 0 24 24" stroke="currentColor" fill="none" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={item.icon} /></svg><span><strong>{item.label}</strong><small>{item.hint}</small></span>{item.id === "sources" && suggestions.length > 0 && <b>{suggestions.length}</b>}</button>)}</nav>
        <div className="cp-progress"><div><strong>Полнота профиля</strong><b>{percent}%</b></div><div className="cp-meter" role="progressbar" aria-label="Полнота профиля" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{ width: `${percent}%` }} /></div><p>{progress.done} из {progress.total} блоков заполнено</p><small>Для запуска не требуется 100%. Дополнительные сведения можно заполнить позже.</small></div>
      </aside>
      <div className="cp-content">
        <div className="cp-section-title"><div><p className="cp-eyebrow">{currentSection.hint}</p><h2>{currentSection.label}</h2></div><span className="cp-state">{dirty ? "Есть изменения" : saved ? `Версия ${saved.profile_version}` : "Загрузка…"}</span></div>
        {error && <div className="cp-alert is-error" role="alert">{error}<button type="button" className="cp-retry" disabled={dirty || locked} onClick={() => void load()}>Повторить</button><button type="button" aria-label="Закрыть ошибку" onClick={() => setError("")}>×</button></div>}
        {notice && <div className="cp-alert is-success" role="status">{notice}</div>}
        {loading ? <div className="cp-card cp-loading" role="status">Загружаем профиль компании…</div> : !saved ? <div className="cp-card"><p>Профиль не удалось загрузить.</p><button type="button" className="cp-button" onClick={() => void load()}>Повторить</button></div> : <form className="cp-form" noValidate onSubmit={(e) => { e.preventDefault(); void persist(); }}>
          <fieldset className="cp-controls" disabled={locked}>
            {(section === "about" || !complete) && <section className={`cp-minimum${complete ? " is-ready" : ""}`} aria-label="Минимум для запуска">
              <h3>Минимум для запуска</h3>
              <p>Для персональной подборки и подробного разбора достаточно двух пунктов. {PROFILE_MINIMUM_HINT}</p>
              <ul>
                <li className={profile.company_name.trim() ? "is-done" : ""}><span aria-hidden="true">{profile.company_name.trim() ? "✓" : "1"}</span><span><strong>Название компании</strong><small>{profile.company_name.trim() ? "Заполнено" : "Обязательно"}</small></span></li>
                <li className={profile.description.trim() || profile.description_skipped ? "is-done" : ""}><span aria-hidden="true">{profile.description.trim() || profile.description_skipped ? "✓" : "2"}</span><span><strong>Описание бизнеса</strong><small>{profile.description.trim() ? "Заполнено" : profile.description_skipped ? "Пропущено по вашему выбору" : "Заполните или отметьте пропуск"}</small></span></li>
              </ul>
              <p className="cp-minimum-status" role="status">{complete ? dirty ? "Минимум заполнен. Нажмите «Сохранить профиль», чтобы применить изменения." : "Минимум сохранён. Персональная подборка и подробный разбор доступны." : "После заполнения нажмите «Сохранить профиль». Остальные поля необязательны; 100% заполнения не требуется."}</p>
              {section !== "about" && <button type="button" className="cp-button is-secondary" onClick={() => navigateSection("about")}>Перейти к нужным полям</button>}
            </section>}
            {section === "about" && <>
              <Card title="Основная информация" description="Название обязательно для запуска. Сайт и географию можно добавить позже.">
                <div className="cp-fields"><Field label="Название компании или бренда" required minimum="Для запуска" wide>{text("company_name", "Например, Северная мануфактура")}</Field><Field label="Сайт компании" hint="Необязательно. Можно указать адрес без https://" wide>{text("website", "company.ru", 500)}</Field><Field label="Страна">{text("country", "Не указана", 100)}</Field><Field label="Регион">{text("region", "Область, край или республика", 150)}</Field><Field label="Город" wide>{text("city", "Где работает компания", 150)}</Field></div>
              </Card>
              <Card title="Коротко о бизнесе" description="Чем занимаетесь, что продаёте и для кого. Если пока не готовы описать бизнес, отметьте пропуск — это позволит начать работу."><Field label="Описание бизнеса" required={!profile.description_skipped} minimum="Для запуска или пропуск">{area("description", "Например: производим упаковку для пищевых компаний и поставляем её по России", 2000, 4)}</Field><div className="cp-description-foot"><label className="cp-check"><input type="checkbox" checked={profile.description_skipped} disabled={!!profile.description.trim() && !profile.description_skipped} onChange={(e) => change("description_skipped", e.target.checked)} /><span>Пока пропустить описание</span></label><small>{profile.description.length} / 2000</small></div></Card>
              <OrganizationDetails profile={profile} change={change} /><div className="cp-tip"><span aria-hidden="true">↗</span><div><strong>Можно начать с сайта</strong><p>Помощник предложит данные с цитатами. Вы решите, что добавить в профиль.</p><button type="button" onClick={() => setSection("sources")}>Перейти к заполнению с сайта →</button></div></div>
            </>}
            {section === "metrics" && <CompanyMetrics profile={profile} change={change} />}
            {section === "bank" && <BankProducts profile={profile} change={change} />}
            {section === "connections" && <CounterpartiesEditor key={editReset} profile={profile} change={change} onDraftChange={setConnectionDraft} />}
            {section === "activity" && <>
              <Card title="Отрасль и масштаб" description="Расскажите, в каком сегменте работает компания."><div className="cp-fields"><Field label="Основная деятельность" wide>{text("industry", "Например, производство упаковки")}</Field><Field label="ОКВЭД" hint="Если знаете код">{text("okved", "Например, 17.21", 30)}</Field><Field label="Размер компании" hint="Можно указать диапазон сотрудников">{text("size_band", "Например, 11–50 сотрудников", 100)}</Field><Field label="Этап бизнеса" wide>{text("business_stage", "Например, рост или выход на новые рынки", 100)}</Field></div><div className="cp-tag-section"><h4>Отраслевые теги</h4><p>Выберите одну или несколько подходящих отраслей.</p><Tags options={options.industry_tags} selected={profile.industry_tags} onChange={(value) => change("industry_tags", value)} /></div></Card>
              <Card title="Продукты и клиенты"><div className="cp-fields"><Field label="Товары и услуги" wide>{area("products_services", "Что вы производите, продаёте или оказываете", 1500)}</Field><Field label="Кто ваши клиенты">{text("customer_types", "Например, малый бизнес, розничные сети", 500)}</Field><Field label="Модель бизнеса">{text("business_model", "Например, B2B, B2C или подписка", 500)}</Field></div></Card>
            </>}
            {section === "finance" && <Card title="Что актуально для компании" description="Выберите интересующие направления. «Не знаю» оставляет вопрос открытым. Выбор интереса не означает наличие банковского продукта."><div className="cp-interests">{INTERESTS.map((item) => <fieldset className="cp-interest" key={item.key}><legend>{item.label}</legend><p>{item.description}</p><div className="cp-segmented">{([["interested", "Актуально"], ["not_relevant", "Неактуально"], ["unknown", "Не знаю"]] as const).map(([value, label]) => <label key={value} className={profile[item.key] === value ? "is-selected" : ""}><input type="radio" name={item.key} checked={profile[item.key] === value} value={value} onChange={() => change(item.key, value)} /><span>{label}</span></label>)}</div></fieldset>)}</div></Card>}
            {section === "markets" && <>
              <CustomsDetails profile={profile} change={change} />
              <Card title="Темы для отслеживания" description="Отметьте, какие события вы хотите видеть в персональной подборке."><Tags options={options.focus_topics} selected={profile.focus_topics} onChange={(value) => change("focus_topics", value)} /></Card>
              <Card title="Рынки и зависимости" description="Контекст поставок, продаж и внешних факторов помогает точнее оценивать влияние событий."><div className="cp-fields"><Field label="География рынков и поставок" hint="Названия стран и регионов через запятую" wide><input maxLength={1000} placeholder="Например, Россия, Казахстан, Китай" value={geoText} onChange={(e) => { setGeoText(e.target.value); change("market_geographies", commaList(e.target.value)); }} /></Field><Field label="От чего зависит бизнес" wide>{area("dependencies", "Сырьё, энергия, поставщики, сезонность", 1000)}</Field><Field label="Внешние факторы" wide>{area("external_factors", "Что ещё влияет на вашу работу", 1500)}</Field><Field label="Дополнительный контекст" wide>{area("context_note", "Что важно учитывать при подборке новостей", 1500)}</Field></div></Card>
            </>}
            {section === "sources" && <>
              <Card title="Помощник по сайту" description="Найдём публичные сведения о компании и предложим заполнить пустые поля. Перед добавлением вы увидите цитаты и ссылки."><Field label="Сайт компании">{text("website", "https://company.ru", 500)}</Field><div className="cp-site-steps"><span><b>1</b> Укажите сайт</span><span><b>2</b> Изучите предложения</span><span><b>3</b> Подтвердите данные</span></div><label className="cp-check cp-site-consent"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /><span>Разрешаю передать текст публичных страниц выбранной модели для анализа. При облачном маршруте текст будет отправлен внешнему провайдеру.</span></label><button type="button" className="cp-button" disabled={!profile.website.trim() || !consent || locked} onClick={() => void scan()}>{busy === "site" ? "Изучаем сайт…" : dirty ? "Сохранить и изучить сайт" : "Изучить сайт"}<span aria-hidden="true">↗</span></button>{busy === "site" && <p className="cp-small" role="status">Читаем доступные страницы и проверяем найденные сведения. Это может занять несколько минут.</p>}{!profile.website.trim() && <p className="cp-small">Сначала укажите адрес сайта компании.</p>}</Card>
              {pages !== null && <Card title={`Изучено страниц: ${pages.length}`}>{openedUrl && <p className="cp-small">Сайт перенаправил на <a href={openedUrl} target="_blank" rel="noreferrer">{openedUrl}</a>. Региональная версия сайта может содержать сведения только для выбранного региона.</p>}{pages.length > 0 && <details className="cp-pages"><summary>Показать использованные страницы</summary><ul>{pages.map((page) => <li key={page.url}><a href={page.url} target="_blank" rel="noreferrer">{page.title || page.url}</a></li>)}</ul></details>}{!pages.length && <p className="cp-small">Проверьте доступность сайта и ограничения robots.txt. Страницы, требующие JavaScript или проверки браузера, могут быть недоступны.</p>}</Card>}
              <SuggestionsPanel suggestions={suggestions} warnings={warnings} busy={!!busy} dirty={dirty} label={label} fieldLabels={FIELD_LABELS} onDecide={(item, accept) => void decide(item, accept)} onAcceptAll={() => void decide()} />
              <ProfileSourcesPanel sources={sources} profile={profile} fieldLabels={FIELD_LABELS} label={label} />
              {!suggestions.length && !sources.length && pages === null && <div className="cp-empty"><span aria-hidden="true">⌁</span><strong>Источники появятся здесь</strong><p>После изучения сайта можно проверить и подтвердить найденные сведения. Данные, введённые вручную, сохраняются сразу по кнопке «Сохранить профиль».</p></div>}
            </>}
          </fieldset>
          <footer className="cp-savebar"><div><i className={dirty ? "is-dirty" : ""} /><span>{busy === "save" ? "Сохраняем изменения…" : dirty ? "Есть несохранённые изменения" : "Все изменения сохранены"}<small>{saved ? `Обновлено ${formatDateTime(saved.updated_at)}` : ""}</small></span></div><div><button type="button" className="cp-button is-secondary" onClick={cancel} disabled={!dirty || locked}>Отменить</button><button type="submit" className="cp-button" disabled={!dirty || locked}>{busy === "save" ? "Сохраняем…" : "Сохранить профиль"}</button></div></footer>
        </form>}
      </div>
    </div>
  </div>;
}
