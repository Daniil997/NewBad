import { useEffect, useState } from "react";

type Health = {
  status: string;
  service: string;
  journal: string[];
  journal_empty: boolean;
};

type ClientSummary = {
  client_id: string;
  title: string;
  money_role: string;
  exposure_ids: string[];
};

type ClientProfile = {
  client_id: string;
  title: string;
  money_role: string;
  cohort_key: string | null;
  cohort_share: number | null;
  missing_data: string[];
  exposures: { id: string; strength: number; assumptions: string[] }[];
  counterparties: { client_id: string; direction: string; same_group: boolean }[];
};

type View =
  | { kind: "loading" }
  | { kind: "ok"; health: Health }
  | { kind: "error"; message: string };

const ROLE_LABEL: Record<string, string> = {
  borrower: "заёмщик",
  saver: "вкладчик",
  both: "и кредит, и депозит",
  neither: "без кредита и депозита",
};

export function App() {
  const [view, setView] = useState<View>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      try {
        const response = await fetch("/api/health", { signal: controller.signal });
        if (!response.ok) {
          throw new Error(`Сервис ответил ${response.status}`);
        }
        const health = (await response.json()) as Health;
        setView({ kind: "ok", health });
      } catch (error) {
        if (controller.signal.aborted) {
          return;
        }
        const message = error instanceof Error ? error.message : "Нет ответа от API";
        setView({ kind: "error", message });
      }
    }

    void load();
    return () => controller.abort();
  }, []);

  return (
    <main className="page">
      <p className="eyebrow">Альфа-Бизнес</p>
      <h1>Что важно для вашей компании</h1>
      <p className="lead">Выберите компанию. Профиль собран из данных банка, без нейросети.</p>
      <HealthCard view={view} />
      <Clients />
      <CorpusSearch />
    </main>
  );
}

type SearchHit = {
  id: string;
  title: string;
  excerpt: string;
  event_type: string | null;
};

function CorpusSearch() {
  const [query, setQuery] = useState("ключевая ставка");
  const [newsHits, setNewsHits] = useState<SearchHit[]>([]);
  const [bankHits, setBankHits] = useState<SearchHit[]>([]);

  async function runSearch(nextQuery: string) {
    const response = await fetch(`/api/search?q=${encodeURIComponent(nextQuery)}`);
    if (!response.ok) {
      return;
    }
    const body = (await response.json()) as { news: SearchHit[]; bank: SearchHit[] };
    setNewsHits(body.news);
    setBankHits(body.bank);
  }

  useEffect(() => {
    void runSearch(query);
  }, []);

  return (
    <section className="card clients">
      <h2>Поиск по корпусу</h2>
      <form
        className="search"
        onSubmit={(event) => {
          event.preventDefault();
          void runSearch(query);
        }}
      >
        <input value={query} onChange={(event) => setQuery(event.target.value)} />
        <button type="submit">Найти</button>
      </form>
      <h3>Аналитика банка</h3>
      <HitList hits={bankHits} />
      <h3>Новости</h3>
      <HitList hits={newsHits} />
    </section>
  );
}

function HitList({ hits }: { hits: SearchHit[] }) {
  if (hits.length === 0) {
    return <p>Ничего не найдено.</p>;
  }
  return (
    <ul className="hits">
      {hits.map((hit) => (
        <li key={hit.id}>
          <strong>{hit.title}</strong>
          <span>{hit.event_type}</span>
          <p>{hit.excerpt}</p>
        </li>
      ))}
    </ul>
  );
}

function Clients() {
  const [clients, setClients] = useState<ClientSummary[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [profile, setProfile] = useState<ClientProfile | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    void fetch("/api/clients")
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Список компаний: ${response.status}`);
        }
        return response.json() as Promise<ClientSummary[]>;
      })
      .then((items) => {
        setClients(items);
        if (items[0]) {
          setSelected(items[0].client_id);
        }
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Не удалось загрузить компании");
      });
  }, []);

  useEffect(() => {
    if (!selected) {
      return;
    }
    const controller = new AbortController();
    void fetch(`/api/clients/${selected}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Профиль: ${response.status}`);
        }
        return response.json() as Promise<ClientProfile>;
      })
      .then(setProfile)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Не удалось загрузить профиль");
        }
      });
    return () => controller.abort();
  }, [selected]);

  return (
    <section className="card clients">
      <h2>Компании</h2>
      {error ? <p className="error">{error}</p> : null}
      <div className="switcher">
        {clients.map((client) => (
          <button
            key={client.client_id}
            type="button"
            className={client.client_id === selected ? "active" : ""}
            onClick={() => setSelected(client.client_id)}
          >
            {ROLE_LABEL[client.money_role] ?? client.money_role}
            <span>{client.client_id}</span>
          </button>
        ))}
      </div>
      {profile ? <ProfileDetails profile={profile} /> : null}
    </section>
  );
}

function ProfileDetails({ profile }: { profile: ClientProfile }) {
  return (
    <div className="profile">
      <p className="title">{profile.title}</p>
      <dl>
        <div>
          <dt>Роль</dt>
          <dd>{ROLE_LABEL[profile.money_role] ?? profile.money_role}</dd>
        </div>
        <div>
          <dt>Доля в когорте</dt>
          <dd>
            {profile.cohort_share === null
              ? "нет выручки"
              : `${Math.round(profile.cohort_share * 100)}% · ОКВЭД ${profile.cohort_key}`}
          </dd>
        </div>
        <div>
          <dt>Экспозиции</dt>
          <dd>{profile.exposures.map((item) => item.id).join(", ") || "нет"}</dd>
        </div>
        <div>
          <dt>Не хватает</dt>
          <dd>{profile.missing_data.join(", ") || "пропусков нет"}</dd>
        </div>
        <div>
          <dt>Контрагенты</dt>
          <dd>
            {profile.counterparties
              .map((item) => `${item.client_id} (${item.direction === "out" ? "исходящий" : "входящий"})`)
              .join(", ") || "нет платежей"}
          </dd>
        </div>
      </dl>
    </div>
  );
}

function HealthCard({ view }: { view: View }) {
  if (view.kind === "loading") {
    return <section className="card">Проверяем API…</section>;
  }
  if (view.kind === "error") {
    return (
      <section className="card card-bad">
        <h2>API недоступен</h2>
        <p>{view.message}</p>
      </section>
    );
  }

  const { health } = view;
  return (
    <section className="card card-ok">
      <h2>API доступен</h2>
      <dl>
        <div>
          <dt>Статус</dt>
          <dd>{health.status}</dd>
        </div>
        <div>
          <dt>Сервис</dt>
          <dd>{health.service}</dd>
        </div>
        <div>
          <dt>Журнал</dt>
          <dd>{health.journal.join(", ")}</dd>
        </div>
        <div>
          <dt>Записей</dt>
          <dd>{health.journal_empty ? "пока нет" : "есть"}</dd>
        </div>
      </dl>
    </section>
  );
}
