import { useEffect, useState } from "react";
import { Home } from "./Home";
import { Login } from "./Login";
import { Service } from "./Service";
import { Settings } from "./Settings";

export type Screen = "home" | "login" | "service" | "settings";

const SESSION = "newbad-client";

export function App() {
  const [screen, setScreen] = useState<Screen>("home");
  const [clientId, setClientId] = useState(() => sessionStorage.getItem(SESSION) ?? "");

  useEffect(() => {
    const titles: Record<Screen, string> = {
      home: "Альфа-Бизнес",
      login: "Вход",
      settings: "Настройки разработки",
      service: "Что важно для вашей компании",
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

  if (screen === "settings") {
    return <Settings onBack={() => setScreen("home")} />;
  }
  if (screen === "login") {
    return <Login onEnter={enter} onHome={() => setScreen("home")} onSettings={() => setScreen("settings")} />;
  }
  if (screen === "service" && clientId) {
    return <Service clientId={clientId} onHome={() => setScreen("home")} onLogout={leave} />;
  }
  return (
    <Home
      entered={Boolean(clientId)}
      onOpen={() => setScreen(clientId ? "service" : "login")}
      onSettings={() => setScreen("settings")}
    />
  );
}
