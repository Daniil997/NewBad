import { useEffect, useState } from "react";

type SettingsView = {
  mode: string;
  allow_cloud: boolean;
  active_generation: string;
  model: string;
  weights_from_browser: boolean;
  local_server: string;
  placement: string;
};

export function Settings({ onBack }: { onBack: () => void }) {
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/settings", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Настройки: ${response.status}`);
        }
        return response.json() as Promise<SettingsView>;
      })
      .then(setSettings)
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Нет ответа от API");
        }
      });
    return () => controller.abort();
  }, []);

  return (
    <main className="page">
      <button className="linkish" type="button" onClick={onBack}>
        Назад к сервису
      </button>
      <h1>Настройки модели</h1>
      <p className="lead">
        Авторежим смотрит свободную видеопамять. Если модель целиком не входит, хвост остаётся в оперативной
        памяти. Браузер веса не скачивает.
      </p>
      {error ? <p className="error">{error}</p> : null}
      {settings ? (
        <section className="card">
          <dl>
            <div>
              <dt>Режим окружения</dt>
              <dd>{settings.mode}</dd>
            </div>
            <div>
              <dt>Чем отвечаем сейчас</dt>
              <dd>{settings.active_generation}</dd>
            </div>
            <div>
              <dt>Модель</dt>
              <dd>{settings.model}</dd>
            </div>
            <div>
              <dt>Облако</dt>
              <dd>{settings.allow_cloud ? "разрешено" : "выключено"}</dd>
            </div>
            <div>
              <dt>Загрузка весов из браузера</dt>
              <dd>{settings.weights_from_browser ? "да" : "нет"}</dd>
            </div>
            <div>
              <dt>Размещение</dt>
              <dd>{settings.placement}</dd>
            </div>
            <div>
              <dt>Сервер на видеокарте</dt>
              <dd>
                {settings.local_server === "up"
                  ? "отвечает"
                  : settings.local_server === "down"
                    ? "не запущен"
                    : "не используется"}
              </dd>
            </div>
          </dl>
        </section>
      ) : null}
    </main>
  );
}
