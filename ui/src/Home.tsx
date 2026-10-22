export function Home({
  entered,
  onOpen,
  onSettings,
}: {
  entered: boolean;
  onOpen: () => void;
  onSettings: () => void;
}) {
  return (
    <div className="bank">
      <header className="bank-bar">
        <strong>Альфа-Бизнес</strong>
        <span>интернет-банк</span>
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
          <em>{entered ? "Продолжить просмотр" : "Войти и открыть ленту"}</em>
        </button>
        <button className="dev-link" type="button" onClick={onSettings}>
          Настройки разработки
        </button>
      </main>
    </div>
  );
}
