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

export function Login({
  onEnter,
  onHome,
  onSettings,
}: {
  onEnter: (clientId: string) => void;
  onHome: () => void;
  onSettings: () => void;
}) {
  const [clients, setClients] = useState<ClientSummary[]>([]);
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
      .then(setClients)
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
        <button className="bar-button" type="button" onClick={onHome}>
          Альфа-Бизнес
        </button>
      </header>
      <main className="bank-main narrow">
        <h1>Вход</h1>
        <p className="lead">Выберите компанию, от имени которой смотрите новости. Дальше лента и карточки будут только для неё.</p>
        {error ? <p className="error">{error}</p> : null}
        {clients.length === 0 && !error ? <p>Загружаем компании…</p> : null}
        <ul className="accounts">
          {clients.map((client) => (
            <li key={client.client_id}>
              <button type="button" onClick={() => onEnter(client.client_id)}>
                <strong>{client.title}</strong>
                <span>{ROLE_LABEL[client.money_role] ?? client.money_role}</span>
              </button>
            </li>
          ))}
        </ul>
        <button className="dev-link" type="button" onClick={onSettings}>
          Настройки разработки
        </button>
      </main>
    </div>
  );
}
