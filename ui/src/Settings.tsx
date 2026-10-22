import { useEffect, useState } from "react";

type SettingsView = {
  mode: string;
  allow_cloud: boolean;
  active_generation: string;
  model: string;
  weights_from_browser: boolean;
  local_server: string;
  placement: string;
  placement_choice: string;
};

const MODES = ["mock", "local_only", "local_dual", "hybrid", "cloud_only", "replay"] as const;
const PLACEMENTS = ["auto", "gpu", "ram"] as const;

export function Settings({ onBack }: { onBack: () => void }) {
  const [settings, setSettings] = useState<SettingsView | null>(null);
  const [mode, setMode] = useState("mock");
  const [allowCloud, setAllowCloud] = useState(false);
  const [placement, setPlacement] = useState("auto");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/settings", { signal: controller.signal })
      .then((response) => {
        if (!response.ok) {
          throw new Error(`Настройки: ${response.status}`);
        }
        return response.json() as Promise<SettingsView>;
      })
      .then((payload) => {
        setSettings(payload);
        setMode(payload.mode);
        setAllowCloud(payload.allow_cloud);
        setPlacement(payload.placement_choice || "auto");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Нет ответа от API");
        }
      });
    return () => controller.abort();
  }, []);

  async function save() {
    setBusy(true);
    setError("");
    setSaved("");
    try {
      const response = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, allow_cloud: allowCloud, placement }),
      });
      const payload = (await response.json().catch(() => null)) as (SettingsView & { detail?: string }) | null;
      if (!response.ok || !payload || !payload.mode) {
        setError(payload?.detail || `Настройки: ${response.status}`);
        return;
      }
      setSettings(payload);
      setSaved("Сохранено до перезапуска сервера. Файл .env не изменён.");
    } catch {
      setError("Нет ответа от API");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="page">
      <button className="linkish" type="button" onClick={onBack}>
        На главную
      </button>
      <h1>Настройки разработки</h1>
      <p className="lead">
        Здесь переключается режим модели у уже запущенного сервера. Обычный экран сервиса эти поля не показывает.
        Ключ API отсюда не задаётся.
      </p>
      {error ? <p className="error">{error}</p> : null}
      <form
        className="card settings-form"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <label>
          Режим
          <select value={mode} onChange={(event) => setMode(event.target.value)}>
            {MODES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={allowCloud} onChange={(event) => setAllowCloud(event.target.checked)} />
          Разрешить облако. Нужен вместе с hybrid или cloud_only.
        </label>
        <label>
          Размещение локальной модели
          <select value={placement} onChange={(event) => setPlacement(event.target.value)}>
            {PLACEMENTS.map((item) => (
              <option key={item} value={item}>
                {item === "auto" ? "auto — по свободной памяти" : item === "gpu" ? "gpu — целиком на видеокарте" : "ram — хвост в памяти"}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Сохраняем…" : "Применить"}
        </button>
        {saved ? <p>{saved}</p> : null}
      </form>
      {settings ? (
        <section className="card">
          <h2>Сейчас</h2>
          <dl>
            <div>
              <dt>Режим</dt>
              <dd>{settings.mode}</dd>
            </div>
            <div>
              <dt>Чем отвечаем</dt>
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
