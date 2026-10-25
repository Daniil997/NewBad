import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import "./refresh.css";

type RefreshState = { status: string; message?: string };
const RefreshContext = createContext<{ state: RefreshState; start: () => void }>({ state: { status: "idle" }, start() {} });

export function DataRefreshProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<RefreshState>({ status: "idle" });
  const [pollKey, setPollKey] = useState(0);
  const wasRunning = useRef(false);
  const starting = useRef(false);
  useEffect(() => {
    let disposed = false;
    let timer: number | undefined;
    async function poll() {
      try {
        const response = await fetch("/api/data/refresh");
        if (!response.ok) throw new Error("Не удалось проверить обновление данных");
        const next = await response.json() as RefreshState;
        if (disposed || starting.current) return;
        setState(next);
        if (next.status === "running") {
          wasRunning.current = true;
          timer = window.setTimeout(() => void poll(), 2000);
        } else if (wasRunning.current) {
          wasRunning.current = false;
          window.dispatchEvent(new Event("business-data-updated"));
        }
      } catch (reason) {
        if (!disposed) {
          setState({ status: "error", message: (reason as Error).message });
          timer = window.setTimeout(() => void poll(), 5000);
        }
      }
    }
    void poll();
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [pollKey]);

  async function start() {
    if (starting.current || state.status === "running") return;
    starting.current = true;
    let accepted = false;
    setState({ status: "running" });
    try {
      const csrfResponse = await fetch("/api/auth/csrf");
      if (!csrfResponse.ok) throw new Error("Не удалось подготовить обновление");
      const csrf = await csrfResponse.json() as { csrf_token: string };
      const response = await fetch("/api/data/refresh", { method: "POST", headers: { "X-CSRF-Token": csrf.csrf_token } });
      const next = await response.json();
      if (!response.ok && response.status !== 409) throw new Error(next.detail || "Не удалось запустить обновление");
      wasRunning.current = true;
      accepted = true;
    } catch (reason) { setState({ status: "error", message: (reason as Error).message }); }
    finally { starting.current = false; if (accepted) setPollKey((key) => key + 1); }
  }
  return <RefreshContext.Provider value={{ state, start: () => void start() }}>{children}</RefreshContext.Provider>;
}

export function DataRefreshButton() {
  const { state, start } = useContext(RefreshContext);
  const running = state.status === "running";
  return <div className="data-refresh">
    <button className="bar-button" type="button" disabled={running} onClick={start} aria-busy={running}>
      {running ? "Обновляем данные…" : "Обновить данные"}
    </button>
    {state.message && <span className={`data-refresh-status is-${state.status}`} role={state.status === "error" ? "alert" : "status"}>{state.message}</span>}
  </div>;
}
