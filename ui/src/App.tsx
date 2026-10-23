import { useEffect, useState } from "react";
import { Home } from "./Home";
import { Login } from "./Login";
import { Service } from "./Service";
import { Settings } from "./Settings";
import { RealMode } from "./RealMode";
import type { AppMode } from "./ModeSwitch";

export type Screen = "home" | "login" | "service" | "settings" | "real-start";

const SESSION = "newbad-client";
const MODE = "newbad-app-mode";

export function App() {
  const [screen, setScreen] = useState<Screen>("home");
  const [clientId, setClientId] = useState(() => sessionStorage.getItem(SESSION) ?? "");
  const [mode, setMode] = useState<AppMode>(() => {
    return localStorage.getItem(MODE) === "demo" ? "demo" : "real";
  });

  useEffect(() => {
    const titles: Record<Screen, string> = {
      home: "Альфа-Бизнес",
      login: "Вход",
      settings: "Настройки разработки",
      service: "Что важно для вашей компании",
      "real-start": "Реальный режим",
    };
    document.title = titles[screen];
  }, [screen]);

  function enter(id: string) {
    sessionStorage.setItem(SESSION, id);
    setClientId(id);
    setScreen("service");
  }

  function leave() {
    sessionStorage.removeItem(SESSION);
    setClientId("");
    setScreen("login");
  }

  function changeMode(nextMode: AppMode) {
    if (nextMode === mode) return;
    localStorage.setItem(MODE, nextMode);
    sessionStorage.removeItem(SESSION);
    setClientId("");
    setMode(nextMode);
    setScreen("home");
  }

  if (screen === "settings") {
    return <Settings onBack={() => setScreen("home")} />;
  }
  if (screen === "login") {
    return (
      <Login
        mode={mode}
        onModeChange={changeMode}
        onEnter={enter}
        onHome={() => setScreen("home")}
        onSettings={() => setScreen("settings")}
      />
    );
  }
  if (screen === "service" && mode === "demo" && clientId) {
    return (
      <Service
        clientId={clientId}
        mode={mode}
        onModeChange={changeMode}
        onHome={() => setScreen("home")}
        onLogout={leave}
      />
    );
  }
  if (screen === "real-start") {
    return <RealMode mode={mode} onModeChange={changeMode} onHome={() => setScreen("home")} />;
  }
  return (
    <Home
      mode={mode}
      onModeChange={changeMode}
      entered={mode === "demo" && Boolean(clientId)}
      onOpen={() =>
        setScreen(mode === "real" ? "real-start" : clientId ? "service" : "login")
      }
      onSettings={() => setScreen("settings")}
    />
  );
}
