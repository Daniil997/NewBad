import { DataRefreshButton } from "./DataRefresh";
import "./home.css";

export function Home({
  onOpen,
  onSettings,
}: {
  onOpen: () => void;
  onSettings: () => void;
}) {
  return (
    <div className="bank home-page">
      <header className="bank-bar">
        <div className="bank-brand">
          <strong>Альфа-Бизнес</strong>
          <span>интернет-банк</span>
        </div>
        <DataRefreshButton />
      </header>
      <main className="bank-main">
        <h1>Главная</h1>
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
          <em>
            Войти или зарегистрироваться
          </em>
        </button>
        <button className="dev-link" type="button" onClick={onSettings}>
          Настройки разработки
        </button>
      </main>
    </div>
  );
}
