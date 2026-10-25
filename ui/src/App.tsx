import { useEffect, useState } from "react";
import { Home } from "./Home";
import { Settings } from "./Settings";
import { RealMode } from "./RealMode";
import { DataRefreshProvider } from "./DataRefresh";

export function App() {
  const [screen, setScreen] = useState<"home" | "settings" | "service">("home");
  useEffect(() => {
    localStorage.removeItem("newbad-app-mode");
    sessionStorage.removeItem("newbad-client");
    document.title = screen === "settings" ? "Настройки разработки" : screen === "service" ? "Что важно для вашей компании" : "Альфа-Бизнес";
  }, [screen]);
  return <DataRefreshProvider>
    {screen === "settings" ? <Settings onBack={() => setScreen("home")} />
      : screen === "service" ? <RealMode onHome={() => setScreen("home")} />
        : <Home onOpen={() => setScreen("service")} onSettings={() => setScreen("settings")} />}
  </DataRefreshProvider>;
}
