export type AppMode = "real" | "demo";

export function ModeSwitch({
  mode,
  onChange,
}: {
  mode: AppMode;
  onChange: (mode: AppMode) => void;
}) {
  return (
    <div className="mode-switch" role="group" aria-label="Режим приложения">
      <button
        type="button"
        aria-pressed={mode === "real"}
        className={mode === "real" ? "active" : ""}
        onClick={() => onChange("real")}
      >
        Реальный
      </button>
      <button
        type="button"
        aria-pressed={mode === "demo"}
        className={mode === "demo" ? "active" : ""}
        onClick={() => onChange("demo")}
      >
        Тестовые данные
      </button>
    </div>
  );
}
