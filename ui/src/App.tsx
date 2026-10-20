import { useEffect, useState } from "react";
import { Home } from "./Home";
import { Service } from "./Service";
import { Settings } from "./Settings";

export type Screen = "home" | "service" | "settings";

export function App() {
  const [screen, setScreen] = useState<Screen>("home");

  useEffect(() => {
    document.title =
      screen === "home" ? "Альфа-Бизнес" : screen === "settings" ? "Настройки модели" : "Что важно для вашей компании";
  }, [screen]);

  if (screen === "settings") {
    return <Settings onBack={() => setScreen("service")} />;
  }
  if (screen === "service") {
    return <Service onHome={() => setScreen("home")} onSettings={() => setScreen("settings")} />;
  }
  return <Home onOpen={() => setScreen("service")} />;
}
