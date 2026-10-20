import { useEffect, useState } from "react";

type Health = {
  status: string;
  service: string;
  journal: string[];
  journal_empty: boolean;
};

type View =
  | { kind: "loading" }
  | { kind: "ok"; health: Health }
  | { kind: "error"; message: string };

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
      <p className="lead">Каркас сервиса. Лента и карточки появятся на следующих этапах.</p>
      <HealthCard view={view} />
    </main>
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
