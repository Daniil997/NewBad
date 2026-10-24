export type Feature = {
  id: string;
  label: string;
  value: string;
  origin: string;
  source_fields: string[];
};

export type Exposure = {
  id: string;
  title: string;
  basis: string;
  source: string;
  confidence: string;
  strength: number;
  feature_refs: string[];
  assumptions: string[];
};

export type Counterparty = {
  client_id: string;
  direction: string;
  pay_amt: number | null;
  pay_cnt: number | null;
  same_group: boolean;
};

export type ClientProfile = {
  client_id: string;
  title: string;
  money_role: string;
  cohort_share: number | null;
  cohort_key: string | null;
  features: Feature[];
  missing_data: string[];
  exposures: Exposure[];
  counterparties: Counterparty[];
};

export type ClientSummary = {
  client_id: string;
  title: string;
  money_role: string;
};

export type ScoreFactor = { id: string; label: string; value: number; note: string };

export type FeedItem = {
  news_id: string;
  title: string;
  published_at: string;
  event_type: string | null;
  score: number;
  band: string;
  matched_features: string[];
  factors: ScoreFactor[];
  reason: string;
};

export type Feed = {
  top: FeedItem[];
  less_relevant: FeedItem[];
};

/** Запись корпуса новостей из GET /api/news: даёт дату, источник и тип события для списка. */
export type NewsMeta = {
  id: string;
  title: string;
  published_at: string;
  excerpt: string;
  event_type: string | null;
  event_title: string | null;
  source: string | null;
  url: string | null;
};

export const ROLE_LABEL: Record<string, string> = {
  borrower: "заёмщик",
  saver: "вкладчик",
  both: "и кредит, и депозит",
  neither: "без кредита и депозита",
};

export const ORIGIN_LABEL: Record<string, string> = {
  model: "ответ модели",
  replay: "повтор из кэша",
  template: "шаблон",
};

export const LAYER_LABEL: Record<string, string> = {
  confirmed_fact: "Из новости",
  bank_position: "Банк",
  model_inference: "Для вас",
};

/** Подписи полей профиля; совпадают с FIELD_LABELS на бэкенде (app/profile/build.py). */
export const FIELD_LABELS: Record<string, string> = {
  ORG_TYPE: "Тип организации",
  OKFS_NAME: "Форма собственности",
  COMPANY_AGE_YEARS_GRADATION: "Возраст компании",
  ECO_MARK_LABEL: "Стадия развития",
  MAINOKVED_CODE: "ОКВЭД",
  MAINOKVED_NAME: "Вид деятельности",
  NEGATIVEMARK: "Признак ключевого собственника",
  REVENUE_COMPANY: "Годовая выручка",
  GROUP_ID: "Группа компаний",
  AMOUNT_WORKERS_FNS_GRADATION: "Численность по ФНС",
  AMOUNT_WORKERS_SPARK_GRADATION: "Численность по СПАРК",
  CL_TYPE: "Тип клиента",
  CITY: "Город",
  REGION: "Регион",
  CL_PRODUCT_RKO_MARK: "Расчётно-кассовое обслуживание",
  CL_PRODUCT_GAR_MARK: "Банковские гарантии",
  CL_PRODUCT_LOA_MARK: "Кредитные продукты",
  CL_PRODUCT_LEA_MARK: "Лизинг",
  CL_PRODUCT_DEP_MARK: "Депозиты",
  CL_PRODUCT_ZPP_MARK: "Зарплатный проект",
  FTS_FLAG: "Есть данные ФТС",
  FTS_COUNTRY_NAME: "Страна в данных ФТС",
  FACTORING_MARK: "Факторинг",
};

/** Полоса ленты (high / extra / low) на языке, понятном пользователю. */
export function bandInfo(band: string): { label: string; level: "high" | "mid" | "low" } {
  if (band === "high") return { label: "Сильная связь", level: "high" };
  if (band === "extra") return { label: "Есть связь", level: "mid" };
  return { label: "Слабая связь", level: "low" };
}

export function formatNumber(value: number): string {
  return value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
}
