import type { ReactNode } from "react";
import {
  FIELD_LABELS, ROLE_LABEL, formatNumber,
  type ClientProfile, type Feature,
} from "./demoTypes";

type Group = { title: string; hint: string; ids: string[] };

const GROUPS: Group[] = [
  {
    title: "О компании",
    hint: "Кто это и где работает.",
    ids: [
      "ORG_TYPE", "OKFS_NAME", "COMPANY_AGE_YEARS_GRADATION", "ECO_MARK_LABEL", "CL_TYPE",
      "CITY", "REGION", "GROUP_ID", "group_size", "group_rank", "NEGATIVEMARK",
    ],
  },
  {
    title: "Чем занимается и масштаб",
    hint: "Отрасль, размер и внешняя торговля.",
    ids: [
      "MAINOKVED_CODE", "MAINOKVED_NAME", "okved_theme", "REVENUE_COMPANY", "cohort_share",
      "AMOUNT_WORKERS_FNS_GRADATION", "AMOUNT_WORKERS_SPARK_GRADATION", "FTS_FLAG", "FTS_COUNTRY_NAME",
    ],
  },
  {
    title: "Продукты банка",
    hint: "Чем компания уже пользуется. Это влияет на то, какие новости считаются важными.",
    ids: [
      "CL_PRODUCT_RKO_MARK", "CL_PRODUCT_GAR_MARK", "CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK",
      "CL_PRODUCT_DEP_MARK", "CL_PRODUCT_ZPP_MARK", "FACTORING_MARK", "money_role",
    ],
  },
];

const ORIGIN: Record<string, string> = {
  bank_data: "данные банка",
  derived: "рассчитано",
  payments: "платежи",
  client_input: "со слов клиента",
};

const CONFIDENCE: Record<string, string> = { high: "высокая", medium: "средняя", low: "низкая" };

const EXTRA_REF_LABELS: Record<string, string> = {
  okved_theme: "Товарная тема по ОКВЭД",
  counterparties: "Платёжные связи с контрагентами",
};

function renderValue(feature: Feature): ReactNode {
  const { id, value } = feature;
  if (value === "есть") return <span className="chip chip-high">есть</span>;
  if (value === "нет") return <span className="chip chip-low">нет</span>;
  if (id === "cohort_share") {
    const share = Number(value);
    return Number.isFinite(share) ? `${(share * 100).toFixed(1).replace(".", ",")}%` : value;
  }
  if (id === "REVENUE_COMPANY") {
    const amount = Number(value);
    return Number.isFinite(amount) ? formatNumber(amount) : value;
  }
  if (id === "money_role") return ROLE_LABEL[value] ?? value;
  return value;
}

function Row({ label, feature }: { label: string; feature: Feature | null }) {
  return (
    <li className={`pf-row${feature ? "" : " empty"}`}>
      <span className="pf-label">{label}</span>
      <span className="pf-value">{feature ? renderValue(feature) : "Не указано"}</span>
      <span className="pf-origin">{feature ? (ORIGIN[feature.origin] ?? feature.origin) : "нет в данных"}</span>
    </li>
  );
}

/** Профиль демо-компании: только просмотр, без редактирования. */
export function DemoProfile({ profile, onBack }: { profile: ClientProfile; onBack: () => void }) {
  const byId = new Map<string, Feature>(profile.features.map((feature): [string, Feature] => [feature.id, feature]));
  const missing = new Set(profile.missing_data);
  const filled = profile.features.filter((feature) => feature.origin === "bank_data").length;
  const total = filled + profile.missing_data.length;
  const percent = total ? Math.round((filled / total) * 100) : 100;
  const used = new Set(GROUPS.flatMap((group) => group.ids));
  const others = profile.features.filter((feature) => !used.has(feature.id));
  const groups: Group[] = others.length
    ? [...GROUPS, { title: "Прочие признаки", hint: "Дополнительные сведения из профиля.", ids: others.map((feature) => feature.id) }]
    : GROUPS;
  const refLabel = (ref: string) => byId.get(ref)?.label ?? FIELD_LABELS[ref] ?? EXTRA_REF_LABELS[ref] ?? ref;
  const counterparties = profile.counterparties;
  const exposureStep = groups.length + 1;

  return (
    <>
      <div className="real-head">
        <div>
          <h1>Профиль компании</h1>
          <p className="session-line">
            {profile.title} · {ROLE_LABEL[profile.money_role] ?? profile.money_role} · номер в данных {profile.client_id}
          </p>
        </div>
        <div className="real-head-actions">
          <button type="button" className="btn btn-ghost" onClick={onBack}>← К новостям</button>
        </div>
      </div>

      <p className="demo-note" role="note">
        Это тестовый профиль: данные синтетические, поэтому его можно только смотреть. В реальном режиме
        вы заполняете профиль своей компании сами.
      </p>

      <div className="profile-flow">
        <section className="card progress-card">
          <div className="progress-line">
            <strong>Заполнено {filled} из {total} полей банка</strong>
            <span>{percent}%</span>
          </div>
          <div className="progress-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
            <span style={{ width: `${percent}%` }} />
          </div>
          <p className="hint small">
            {profile.missing_data.length
              ? `Не указано: ${profile.missing_data.map((id) => (FIELD_LABELS[id] ?? id).toLocaleLowerCase("ru")).join(", ")}. `
              : "Все поля заполнены. "}
            Если поля нет, лента и карточки об этом прямо скажут и не будут ничего додумывать.
          </p>
        </section>

        {groups.map((group, index) => {
          const rows = group.ids.flatMap((id) => {
            const feature = byId.get(id) ?? null;
            if (feature) return [<Row key={id} label={feature.label} feature={feature} />];
            if (missing.has(id)) return [<Row key={id} label={FIELD_LABELS[id] ?? id} feature={null} />];
            return [];
          });
          if (rows.length === 0) return null;
          return (
            <section className="card form-section" key={group.title}>
              <h2><span className="step">{index + 1}</span> {group.title}</h2>
              <p className="hint">{group.hint}</p>
              <ul className="pf-list">{rows}</ul>
            </section>
          );
        })}

        <section className="card form-section">
          <h2><span className="step">{exposureStep}</span> Что учитываем в ленте</h2>
          <p className="hint">
            Эти признаки сопоставляются с типами новостей. Данные банка весят больше, чем отраслевые гипотезы.
          </p>
          {profile.exposures.length === 0 ? (
            <p className="empty">Для этой компании не найдено признаков, на которые влияют новости, поэтому лента получается общей.</p>
          ) : (
            profile.exposures.map((exposure) => (
              <article className="pf-exposure" key={exposure.id}>
                <header>
                  <strong>{exposure.title}</strong>
                  <span>
                    <span className={`chip chip-${exposure.basis === "confirmed" ? "high" : "mid"}`}>
                      {exposure.basis === "confirmed" ? "Подтверждено данными" : "Гипотеза по отрасли"}
                    </span>{" "}
                    <small>уверенность: {CONFIDENCE[exposure.confidence] ?? exposure.confidence}</small>
                  </span>
                </header>
                {exposure.feature_refs.length ? (
                  <p className="hint small">
                    Основа: {exposure.feature_refs.map(refLabel).join(", ")} ({ORIGIN[exposure.source] ?? exposure.source}).
                  </p>
                ) : null}
                {exposure.assumptions.length ? (
                  <ul className="reasons">
                    {exposure.assumptions.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                ) : null}
              </article>
            ))
          )}
        </section>

        {counterparties.length > 0 ? (
          <section className="card form-section">
            <h2><span className="step">{exposureStep + 1}</span> Платёжные связи</h2>
            <p className="hint">Контрагенты из платёжных данных. Новость, где упоминается контрагент, получает больший вес.</p>
            <ul className="pf-list">
              {counterparties.slice(0, 5).map((item) => <CounterpartyRow key={`${item.client_id}-${item.direction}`} item={item} />)}
            </ul>
            {counterparties.length > 5 ? (
              <details>
                <summary>Ещё {counterparties.length - 5}</summary>
                <ul className="pf-list">
                  {counterparties.slice(5).map((item) => <CounterpartyRow key={`${item.client_id}-${item.direction}`} item={item} />)}
                </ul>
              </details>
            ) : null}
          </section>
        ) : null}
      </div>
    </>
  );
}

function CounterpartyRow({ item }: { item: ClientProfile["counterparties"][number] }) {
  const direction = item.direction === "out" ? "платит контрагенту" : "получает от контрагента";
  const amount = item.pay_amt === null ? "сумма не определена" : formatNumber(item.pay_amt);
  const count = item.pay_cnt === null ? "" : `, платежей: ${item.pay_cnt}`;
  return (
    <li className="pf-row">
      <span className="pf-label">{item.client_id}</span>
      <span className="pf-value">{direction}: {amount}{count}</span>
      <span className="pf-origin">{item.same_group ? "та же группа" : "платежи"}</span>
    </li>
  );
}
