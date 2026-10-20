import { useEffect, useState } from "react";

type ClientSummary = {
  client_id: string;
  title: string;
  money_role: string;
};

const ROLE_LABEL: Record<string, string> = {
  borrower: "заёмщик",
  saver: "вкладчик",
  both: "и кредит, и депозит",
  neither: "без кредита и депозита",
};

export function Home({ onOpen }: { onOpen: () => void }) {
  const [client, setClient] = useState<ClientSummary | null>(null);
  const [count, setCount] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/clients", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Список компаний: ${response.status}`);
        }
        return response.json() as Promise<ClientSummary[]>;
      })
      .then((items) => {
        const borrower = items.find((item) => item.money_role === "borrower") ?? items[0] ?? null;
        setClient(borrower);
        if (!borrower) {
          return;
        }
        return fetch(`/api/clients/${borrower.client_id}/feed`, { signal: controller.signal })
          .then((response) => {
            if (!response.ok) {
              throw new Error(`Лента: ${response.status}`);
            }
            return response.json() as Promise<{ top: unknown[] }>;
          })
          .then((feed) => setCount(feed.top.length));
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Нет ответа от API");
        }
      });
    return () => controller.abort();
  }, []);

  return (
    <div className="bank">
      <header className="bank-bar">
        <strong>Альфа-Бизнес</strong>
        <span>{client ? ROLE_LABEL[client.money_role] ?? client.title : "интернет-банк"}</span>
      </header>
      <main className="bank-main">
        <h1>Главная</h1>
        {error ? <p className="error">{error}</p> : null}
        <div className="tiles">
          <article>
            <h2>Счета</h2>
            <p>Расчётный счёт</p>
          </article>
          <article>
            <h2>Платежи</h2>
            <p>Исходящие за месяц</p>
          </article>
          <article>
            <h2>Продукты</h2>
            <p>Кредиты, лизинг, депозиты</p>
          </article>
        </div>
        <button className="widget" type="button" onClick={onOpen}>
          <span className="eyebrow">Сервис</span>
          <strong>Что важно для вашей компании</strong>
          <em>{count === null ? "Считаем события…" : `${count} событий в ленте`}</em>
        </button>
      </main>
    </div>
  );
}
