import { useEffect, useState, type ReactNode } from "react";
import type { BusinessProfile, CompanyDetails, Presence, ProfileCounterparty } from "./realTypes";

type Props = {
  profile: BusinessProfile;
  change: <K extends keyof BusinessProfile>(key: K, value: BusinessProfile[K]) => void;
};
type TextKey = { [K in keyof CompanyDetails]: CompanyDetails[K] extends string ? K : never }[keyof CompanyDetails];
type NumberKey = { [K in keyof CompanyDetails]: CompanyDetails[K] extends number | null ? K : never }[keyof CompanyDetails];
type PresenceKey = { [K in keyof CompanyDetails]: CompanyDetails[K] extends Presence ? K : never }[keyof CompanyDetails];

function Card({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return <section className="cp-card"><div className="cp-card-head"><h3>{title}</h3>{description && <p>{description}</p>}</div>{children}</section>;
}
function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="cp-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}
function DetailsText({ profile, change, field, label, hint, max = 200 }: Props & { field: TextKey; label: string; hint?: string; max?: number }) {
  return <Field label={label} hint={hint}><input value={profile[field]} maxLength={max} placeholder="Не указано" onChange={(e) => change(field, e.target.value as BusinessProfile[typeof field])} /></Field>;
}
function DetailsNumber({ profile, change, field, label, hint, min = 0, max, step = 1, scale = 1 }: Props & { field: NumberKey; label: string; hint?: string; min?: number; max?: number; step?: number | "any"; scale?: number }) {
  return <Field label={label} hint={hint}><input type="number" inputMode={step === 1 ? "numeric" : "decimal"} min={min} max={max} step={step} placeholder="Не указано" value={profile[field] === null ? "" : profile[field]! * scale} onChange={(e) => change(field, e.target.value === "" ? null : e.target.valueAsNumber / scale)} /></Field>;
}
function PresenceField({ profile, change, field, label }: Props & { field: PresenceKey; label: string }) {
  return <Field label={label}><select value={profile[field]} onChange={(e) => change(field, e.target.value as Presence)}><option value="unknown">Не указано</option><option value="yes">Да</option><option value="no">Нет</option></select></Field>;
}

export function OrganizationDetails(props: Props) {
  return <Card title="Организация и собственность" description="Юридические сведения и характеристики компании. Заполните известные данные или уточните уже указанные.">
    <div className="cp-fields">
      <DetailsText {...props} field="organization_type" label="Тип организации" />
      <DetailsText {...props} field="ownership_form" label="Форма собственности" />
      <DetailsText {...props} field="company_age_band" label="Возраст компании" hint="Возраст или диапазон, например 8–15 лет" max={100} />
      <DetailsText {...props} field="client_type" label="Тип клиента банка" hint="Обозначение из выгрузки или ваше уточнение" max={100} />
      <PresenceField {...props} field="key_owner_mark" label="Признак ключевого собственника" />
    </div>
  </Card>;
}

export function CompanyMetrics(props: Props) {
  return <>
    <Card title="Выручка и сотрудники" description="Пустое значение означает отсутствие сведений. Нулевую выручку можно указать явно."><div className="cp-fields">
      <DetailsNumber {...props} field="annual_revenue" label="Годовая выручка" step="any" />
      <DetailsText {...props} field="employees_fns_band" label="Численность по ФНС" hint="Диапазон или точное число сотрудников" max={100} />
      <DetailsText {...props} field="employees_spark_band" label="Численность по СПАРК" hint="Отдельное значение, если сведения отличаются" max={100} />
    </div></Card>
    <Card title="Группа компаний" description="Сведения о принадлежности к группе и связанных лицах."><div className="cp-fields">
      <DetailsText {...props} field="group_id" label="Название или идентификатор группы" />
      <DetailsNumber {...props} field="group_size" label="Компаний в группе" min={1} />
      <DetailsNumber {...props} field="group_revenue_rank" label="Место по выручке в группе" min={1} hint="1 — наибольшая выручка" />
      <DetailsNumber {...props} field="related_person_count" label="Связанные физические лица" />
    </div></Card>
    <Card title="Отраслевое сравнение" description="Показатели из исходного профиля можно сохранить или внести вручную. Они не пересчитываются по всей банковской выборке при редактировании."><div className="cp-fields">
      <DetailsText {...props} field="cohort_key" label="Когорта ОКВЭД" hint="Код группы для сравнения" max={100} />
      <DetailsNumber {...props} field="cohort_revenue_share" label="Доля выручки в когорте, %" hint="В исходных данных хранится доля от 0 до 1" max={100} scale={100} step="any" />
      <DetailsText {...props} field="okved_theme" label="Отраслевая тема по ОКВЭД" hint="Обозначение из исходного профиля или ваше уточнение" />
    </div></Card>
  </>;
}

const PRODUCTS: { field: PresenceKey; label: string }[] = [
  { field: "product_rko", label: "Расчётно-кассовое обслуживание" },
  { field: "product_credit", label: "Кредитные продукты" },
  { field: "product_deposit", label: "Депозиты" },
  { field: "product_leasing", label: "Лизинг" },
  { field: "product_guarantees", label: "Банковские гарантии" },
  { field: "product_payroll", label: "Зарплатный проект" },
  { field: "product_factoring", label: "Факторинг" },
];
export function BankProducts(props: Props) {
  const { profile } = props;
  const borrower = profile.product_credit === "yes" || profile.product_leasing === "yes";
  const saver = profile.product_deposit === "yes";
  const role = borrower && saver ? "Заёмщик и вкладчик" : borrower ? "Заёмщик" : saver ? "Вкладчик" : "Нет подтверждённой роли";
  return <>
    <div className="cp-fact-summary"><span className="cp-fact-icon" aria-hidden="true">₽</span><div><small>Финансовая роль по указанным продуктам</small><strong>{role}</strong><p>Кредит и лизинг определяют заёмщика, депозит — вкладчика.</p></div></div>
    <Card title="Действующие банковские продукты" description="Укажите наличие продукта у компании. Если данных нет, оставьте «Не указано»."><div className="cp-fields">{PRODUCTS.map(({ field, label }) => <PresenceField {...props} key={field} field={field} label={label} />)}</div></Card>
  </>;
}

export function CustomsDetails(props: Props) {
  return <Card title="Внешняя торговля и ФТС" description="Сведения из таможенных данных и страны операций."><div className="cp-fields"><PresenceField {...props} field="customs_activity" label="Есть данные ФТС" /><DetailsText {...props} field="customs_countries" label="Страны в данных ФТС" hint="Можно перечислить несколько стран" max={1500} /></div></Card>;
}

const PAGE_SIZE = 12;
export function CounterpartiesEditor({ profile, change, onDraftChange }: Props & { onDraftChange: (dirty: boolean) => void }) {
  const [query, setQuery] = useState("");
  const [direction, setDirection] = useState("all");
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState<ProfileCounterparty>({ client_id: "", direction: "out", pay_amt: null, pay_cnt: null, same_group: null });
  const [error, setError] = useState("");
  useEffect(() => { onDraftChange(editing !== null); return () => onDraftChange(false); }, [editing, onDraftChange]);
  const rows = profile.counterparties;
  const filtered = rows.map((row, index) => ({ row, index })).filter(({ row }) => row.client_id.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()) && (direction === "all" || row.direction === direction));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages - 1);
  const visible = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);
  function edit(index: number | "new") {
    setEditing(index); setError("");
    setDraft(index === "new" ? { client_id: "", direction: "out", pay_amt: null, pay_cnt: null, same_group: null } : { ...rows[index] });
  }
  function apply() {
    if (!draft.client_id.trim()) { setError("Укажите название или идентификатор контрагента."); return; }
    if ((draft.pay_amt !== null && (!Number.isFinite(draft.pay_amt) || draft.pay_amt < 0)) || (draft.pay_cnt !== null && (!Number.isSafeInteger(draft.pay_cnt) || draft.pay_cnt < 0))) { setError("Сумма должна быть неотрицательной, а количество платежей — целым неотрицательным числом."); return; }
    const row = { ...draft, client_id: draft.client_id.trim() };
    if (rows.some((item, index) => index !== editing && item.client_id === row.client_id && item.direction === row.direction)) { setError("Связь с этим контрагентом в выбранном направлении уже есть."); return; }
    change("counterparties", editing === "new" ? [row, ...rows] : rows.map((item, index) => index === editing ? row : item));
    setEditing(null); setError(""); setPage(0);
  }
  const amount = (value: number | null) => value === null ? "Не указано" : value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
  return <>
    <div className="cp-stat-grid"><div><small>Всего связей</small><strong>{rows.length}</strong></div><div><small>Входящие платежи</small><strong>{rows.filter((row) => row.direction === "in").length}</strong></div><div><small>Исходящие платежи</small><strong>{rows.filter((row) => row.direction === "out").length}</strong></div></div>
    <Card title="Платёжные связи" description="Добавляйте контрагентов, уточняйте суммы и количество платежей. Изменения сохраняются вместе с профилем.">
      <div className="cp-connections-toolbar"><Field label="Поиск контрагента"><input type="search" value={query} placeholder="Название или идентификатор" onChange={(e) => { setQuery(e.target.value); setPage(0); }} /></Field><Field label="Направление"><select value={direction} onChange={(e) => { setDirection(e.target.value); setPage(0); }}><option value="all">Все платежи</option><option value="in">Входящие</option><option value="out">Исходящие</option></select></Field><button type="button" className="cp-button is-secondary" disabled={editing !== null || rows.length >= 5000} onClick={() => edit("new")}>+ Добавить связь</button></div>
      {editing !== null && <div className="cp-counterparty-editor"><h4>{editing === "new" ? "Новая платёжная связь" : "Редактирование связи"}</h4><div className="cp-fields">
        <Field label="Название или идентификатор контрагента"><input maxLength={200} value={draft.client_id} onChange={(e) => setDraft({ ...draft, client_id: e.target.value })} /></Field>
        <Field label="Направление платежей"><select value={draft.direction} onChange={(e) => setDraft({ ...draft, direction: e.target.value as "in" | "out" })}><option value="in">Входящие — от контрагента</option><option value="out">Исходящие — контрагенту</option></select></Field>
        <Field label="Сумма платежей"><input type="number" min={0} step="any" placeholder="Не указано" value={draft.pay_amt ?? ""} onChange={(e) => setDraft({ ...draft, pay_amt: e.target.value === "" ? null : e.target.valueAsNumber })} /></Field>
        <Field label="Количество платежей"><input type="number" min={0} step={1} placeholder="Не указано" value={draft.pay_cnt ?? ""} onChange={(e) => setDraft({ ...draft, pay_cnt: e.target.value === "" ? null : e.target.valueAsNumber })} /></Field>
        <Field label="В одной группе компаний"><select value={draft.same_group === null ? "unknown" : String(draft.same_group)} onChange={(e) => setDraft({ ...draft, same_group: e.target.value === "unknown" ? null : e.target.value === "true" })}><option value="unknown">Не указано</option><option value="true">Да</option><option value="false">Нет</option></select></Field>
      </div>{error && <p className="cp-inline-error" role="alert">{error}</p>}<div className="cp-row-actions"><button type="button" className="cp-button" onClick={apply}>{editing === "new" ? "Добавить в профиль" : "Применить изменения"}</button><button type="button" className="cp-button is-secondary" onClick={() => { setEditing(null); setError(""); }}>Отмена</button><small>Затем нажмите «Сохранить профиль».</small></div></div>}
      <div className="cp-connections-list">{visible.map(({ row, index }) => <article className="cp-connection" key={`${index}:${row.client_id}:${row.direction}`}><div className="cp-connection-heading"><strong>{row.client_id}</strong><span className={`cp-direction is-${row.direction}`}>{row.direction === "in" ? "↙ Входящие" : "↗ Исходящие"}</span></div><dl><div><dt>Сумма</dt><dd>{amount(row.pay_amt)}</dd></div><div><dt>Платежей</dt><dd>{amount(row.pay_cnt)}</dd></div><div><dt>В одной группе</dt><dd>{row.same_group === null ? "Не указано" : row.same_group ? "Да" : "Нет"}</dd></div></dl><div className="cp-connection-actions"><button type="button" disabled={editing !== null} onClick={() => edit(index)} aria-label={`Редактировать связь ${row.client_id}, ${row.direction === "in" ? "входящие" : "исходящие"}`}>Редактировать</button><button type="button" disabled={editing !== null} className="is-danger" onClick={() => change("counterparties", rows.filter((_, i) => i !== index))} aria-label={`Удалить связь ${row.client_id}, ${row.direction === "in" ? "входящие" : "исходящие"}`}>Удалить</button></div></article>)}</div>
      {!visible.length && <div className="cp-empty"><strong>{rows.length ? "Связи не найдены" : "Платёжных связей пока нет"}</strong><p>{rows.length ? "Измените запрос или направление платежей." : "Добавьте контрагента вручную, когда появятся сведения."}</p></div>}
      <div className="cp-pagination"><span>{filtered.length ? `${currentPage * PAGE_SIZE + 1}–${Math.min((currentPage + 1) * PAGE_SIZE, filtered.length)} из ${filtered.length}` : "0 связей"}</span><div><button type="button" className="cp-button is-secondary" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)} aria-label="Предыдущая страница связей">←</button><span>{currentPage + 1} / {pages}</span><button type="button" className="cp-button is-secondary" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)} aria-label="Следующая страница связей">→</button></div></div>
    </Card>
  </>;
}
