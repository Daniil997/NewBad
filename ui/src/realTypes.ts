export type User = { id: string; login: string; created_at: string };
export type Interest = "interested" | "not_relevant" | "unknown";
export type Presence = "unknown" | "yes" | "no";
export type ProfileCounterparty = {
  client_id: string; direction: "in" | "out"; pay_amt: number | null;
  pay_cnt: number | null; same_group: boolean | null;
};
export type CompanyDetails = {
  organization_type: string; ownership_form: string; company_age_band: string; client_type: string;
  key_owner_mark: Presence; annual_revenue: number | null;
  employees_fns_band: string; employees_spark_band: string; group_id: string;
  group_size: number | null; group_revenue_rank: number | null; related_person_count: number | null;
  cohort_key: string; cohort_revenue_share: number | null; okved_theme: string;
  product_rko: Presence; product_guarantees: Presence; product_credit: Presence;
  product_leasing: Presence; product_deposit: Presence; product_payroll: Presence;
  product_factoring: Presence; customs_activity: Presence; customs_countries: string;
  counterparties: ProfileCounterparty[];
};
export type BusinessProfile = CompanyDetails & {
  company_name: string; website: string; country: string; region: string; city: string;
  description: string; description_skipped: boolean; industry: string; okved: string; industry_tags: string[];
  products_services: string; customer_types: string; business_model: string;
  size_band: string; business_stage: string; credit_interest: Interest;
  deposit_interest: Interest; currency_interest: Interest; export_import_interest: Interest;
  leasing_interest: Interest; guarantees_interest: Interest; factoring_interest: Interest;
  settlements_interest: Interest; market_geographies: string[]; dependencies: string;
  focus_topics: string[]; external_factors: string; context_note: string; language: "ru" | "en";
};
export type SavedProfile = BusinessProfile & { profile_version: number; status: "incomplete" | "complete"; updated_at: string };
export type TagOption = { id: string; label: string; description: string };
export type ProfileOptions = { industry_tags: TagOption[]; focus_topics: TagOption[] };
export type SiteSuggestion = {
  field_name: string; value: string | string[]; evidence: string; source_url: string;
  source_title: string; extracted_at: string; confidence: number;
};
export type ChatCitation = { title: string; url: string; published_at: string; kind: string };
export type ChatMessage = { role: "user" | "assistant"; text: string; citations: ChatCitation[]; created_at: string; request_id?: string; request_status?: "pending" | "failed" | "completed" };
export type ChatSession = { id: string; title: string; focused_news_id: string | null; updated_at: string; message_count: number; preview: string | null };
export type BankRecommendation = { text: string; page: number | null; updated_at: string | null };
export type RealNewsItem = {
  id: string; title: string; published_at: string; excerpt: string; event_type: string;
  event_title: string; source: string; url: string;
  bank_recommendation?: BankRecommendation | null;
  analysis: null | { relevant: boolean; score: number; reasons: string[]; method: string; analyzed_at: string };
  report: null | { summary: string; relevance: string; risks: string; opportunities: string; next_step?: string; impact_channel?: string; origin?: string; questions: string[]; evidence_ids: string[]; citations: ChatCitation[]; created_at: string };
};
export type RealFeed = {
  items: RealNewsItem[]; insight: RealNewsItem[]; profile_version: number;
  insight_markets: { category: string; instrument: string; title: string; price: number; change_pct: number | null; currency: string; source_url: string; source_timestamp: string; reason: string }[];
  insight_updated_at: string | null; insight_status: string; insight_stale: boolean; insight_refresh_mode: string;
  lookback_days: number; analysis_method: string; analyzed_count: number;
  pending_count: number; job: null | { status: string; updated_at: string; error: string };
  trigger: string; personalization_available: boolean;
};
export type RealMarkets = {
  provider: string; delayed: boolean; latest: null | { status: string; finished_at: string };
  snapshot: { stored_count: number; items: { category: string; instrument: string; name: string; price: number; change_pct: number | null; currency: string; source_url: string; source_timestamp: string; observed_at: string; profile_reason: string }[] };
};
export type MarketHistory = {
  items: { price: number; currency: string; source_timestamp: string; observed_at: string; source_url: string }[];
  change_over_period_pct: number | null;
};

export const PROFILE_FIELD_LABELS: Record<string, string> = {
  company_name: "Название компании", country: "Страна", region: "Регион", city: "Город",
  description: "Описание бизнеса", industry: "Основная деятельность", industry_tags: "Отраслевые теги",
  products_services: "Товары и услуги", customer_types: "Тип клиентов", business_model: "Модель бизнеса",
  dependencies: "Зависимости бизнеса", market_geographies: "География рынков и поставок",
  organization_type: "Тип организации", ownership_form: "Форма собственности", company_age_band: "Возраст компании",
  client_type: "Тип клиента банка", key_owner_mark: "Признак ключевого собственника", annual_revenue: "Годовая выручка",
  employees_fns_band: "Численность по ФНС", employees_spark_band: "Численность по СПАРК", group_id: "Группа компаний",
  group_size: "Компаний в группе", group_revenue_rank: "Место по выручке в группе", related_person_count: "Связанные физические лица",
  cohort_key: "Когорта ОКВЭД", cohort_revenue_share: "Доля выручки в когорте", okved_theme: "Отраслевая тема по ОКВЭД",
  product_rko: "Расчётно-кассовое обслуживание", product_guarantees: "Банковские гарантии", product_credit: "Кредитные продукты",
  product_leasing: "Лизинг", product_deposit: "Депозиты", product_payroll: "Зарплатный проект", product_factoring: "Факторинг",
  customs_activity: "Данные ФТС", customs_countries: "Страны в данных ФТС", counterparties: "Платёжные связи",
};

export const EMPTY_PROFILE: BusinessProfile = {
  organization_type: "", ownership_form: "", company_age_band: "", client_type: "",
  key_owner_mark: "unknown", annual_revenue: null, employees_fns_band: "", employees_spark_band: "",
  group_id: "", group_size: null, group_revenue_rank: null, related_person_count: null,
  cohort_key: "", cohort_revenue_share: null, okved_theme: "",
  product_rko: "unknown", product_guarantees: "unknown", product_credit: "unknown",
  product_leasing: "unknown", product_deposit: "unknown", product_payroll: "unknown",
  product_factoring: "unknown", customs_activity: "unknown", customs_countries: "", counterparties: [],
  company_name: "", website: "", country: "", region: "", city: "", description: "",
  description_skipped: false, industry: "", okved: "", industry_tags: [], products_services: "", customer_types: "",
  business_model: "", size_band: "", business_stage: "", credit_interest: "unknown",
  deposit_interest: "unknown", currency_interest: "unknown", export_import_interest: "unknown",
  leasing_interest: "unknown", guarantees_interest: "unknown", factoring_interest: "unknown",
  settlements_interest: "unknown", market_geographies: [], dependencies: "", focus_topics: [],
  external_factors: "", context_note: "", language: "ru",
};

export function profilePayload(source: BusinessProfile): BusinessProfile {
  const payload = { ...EMPTY_PROFILE };
  (Object.keys(EMPTY_PROFILE) as (keyof BusinessProfile)[]).forEach((key) => {
    (payload as Record<string, unknown>)[key] = source[key];
  });
  return payload;
}

/** «сегодня», «вчера» или «12 октября» по дате публикации YYYY-MM-DD. */
export function formatDay(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.round((today.getTime() - date.getTime()) / 86400000);
  if (diff === 0) return "сегодня";
  if (diff === 1) return "вчера";
  return date.toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
}

export function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
}

export function formatPrice(value: number): string {
  return value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
}

export function relevanceInfo(score: number): { label: string; level: "high" | "mid" | "low" } {
  if (score >= 0.80) return { label: "Сильная связь", level: "high" };
  if (score >= 0.55) return { label: "Есть связь", level: "mid" };
  return { label: "Слабая связь", level: "low" };
}

/** Сколько ключевых блоков профиля заполнено, чтобы показать прогресс. */
export function profileProgress(p: BusinessProfile): { done: number; total: number; missing: string[] } {
  const checks: [string, boolean][] = [
    ["название компании", Boolean(p.company_name.trim())],
    ["описание бизнеса", Boolean(p.description.trim()) || p.description_skipped],
    ["чем занимается компания", Boolean(p.industry.trim()) || p.industry_tags.length > 0],
    ["регион или город", Boolean(p.region.trim() || p.city.trim())],
    ["товары и услуги", Boolean(p.products_services.trim())],
    ["клиенты", Boolean(p.customer_types.trim())],
    [
      "что актуально по услугам банка",
      [p.credit_interest, p.deposit_interest, p.currency_interest, p.export_import_interest,
        p.leasing_interest, p.guarantees_interest, p.factoring_interest, p.settlements_interest]
        .some((value) => value !== "unknown"),
    ],
    ["темы для отслеживания", p.focus_topics.length > 0],
  ];
  return {
    done: checks.filter(([, ok]) => ok).length,
    total: checks.length,
    missing: checks.filter(([, ok]) => !ok).map(([label]) => label),
  };
}
