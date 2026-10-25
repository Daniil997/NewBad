"""Linux launcher: prepare dependencies and manage only this checkout's servers."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


class LaunchError(RuntimeError):
    pass


class ShutdownRequested(BaseException):
    def __init__(self, signum: int):
        self.signum = signum


def process_identity(pid: int) -> dict | None:
    """PID + creation time prevents stopping another process after Linux PID reuse."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return {"pid": pid, "pgid": int(fields[2]), "started": fields[19]}
    except (OSError, ValueError, IndexError):
        return None


def owns_process(record: dict) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("pid"), int) or record["pid"] <= 1:
        return False
    identity = process_identity(record["pid"])
    return (identity is not None and identity == {key: record.get(key) for key in identity}
            and identity["pgid"] == record["pid"])


class Launcher:
    ports = {"api": 8000, "ui": 5173}

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.runtime = self.root / ".newbad-run"
        self.state_file = self.runtime / "processes.json"

    @contextmanager
    def operation_lock(self):
        import fcntl

        self.runtime.mkdir(parents=True, exist_ok=True)
        with (self.runtime / "operation.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LaunchError("Другая команда запуска или остановки ещё выполняется.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read_state(self) -> list[dict]:
        if not self.state_file.exists():
            return []
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
            if state["root"] != str(self.root) or not isinstance(state["processes"], list):
                raise ValueError("unexpected launcher state")
            if any(not isinstance(record, dict) for record in state["processes"]):
                raise ValueError("invalid process record")
            return state["processes"]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise LaunchError(f"Не удалось прочитать {self.state_file}. Серверы не остановлены.") from exc

    def save_state(self, records: list[dict]) -> None:
        temporary = self.state_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({"root": str(self.root), "processes": records}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_file)

    def status(self) -> None:
        records = self.read_state()
        for name, port in self.ports.items():
            active = any(record.get("name") == name and owns_process(record) for record in records)
            print(f"{name.upper()}: {'работает' if active else 'не запущен этим скриптом'} · http://127.0.0.1:{port}")

    def stop_records(self, records: list[dict]) -> bool:
        managed = [record for record in records if owns_process(record)]
        for record in managed:
            try:
                os.killpg(record["pgid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 10
        while any(owns_process(record) for record in managed) and time.monotonic() < deadline:
            time.sleep(0.1)
        for record in managed:
            if owns_process(record):
                try:
                    os.killpg(record["pgid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 2
        while any(owns_process(record) for record in managed) and time.monotonic() < deadline:
            time.sleep(0.1)
        if any(owns_process(record) for record in managed):
            raise LaunchError("Не удалось остановить серверы. Записи процессов сохранены для повторной попытки.")
        return bool(managed)

    def stop(self) -> None:
        managed = self.stop_records(self.read_state())
        self.state_file.unlink(missing_ok=True)
        print("Серверы NewBad остановлены." if managed else "Серверы, запущенные этим скриптом, не найдены.")

    def check_ports(self) -> None:
        for port in self.ports.values():
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError as exc:
                    raise LaunchError(f"Порт {port} занят. Проверьте status или используйте restart для серверов этого скрипта.") from exc

    def run(self, command: list[str], *, cwd: Path | None = None, quiet: bool = False) -> bool:
        result = subprocess.run(command, cwd=cwd or self.root,
                                stdout=subprocess.DEVNULL if quiet else None,
                                stderr=subprocess.DEVNULL if quiet else None, check=False)
        if result.returncode and not quiet:
            raise LaunchError(f"Команда подготовки завершилась с кодом {result.returncode}: {command[0]}")
        return result.returncode == 0

    def prepare(self) -> tuple[str, str]:
        if sys.version_info < (3, 11):
            raise LaunchError("Нужен Python 3.11 или новее.")
        node, npm = shutil.which("node"), shutil.which("npm")
        if not node or not npm:
            raise LaunchError("Нужны Node.js и npm. Используйте Node.js 22 LTS или совместимую версию.")
        env_file = self.root / ".env"
        if not env_file.exists():
            shutil.copyfile(self.root / ".env.example", env_file)
            print("Создан .env из .env.example. Маршруты моделей настраиваются в интерфейсе.")
        venv = self.root / ".venv"
        if venv.is_dir() and not (venv / "bin" / "python").is_file():
            if not (venv / "Scripts" / "python.exe").is_file():
                raise LaunchError("Каталог .venv существует, но Python в нём не найден. Проверьте окружение перед запуском.")
            venv = self.root / ".venv-linux"
        python = venv / "bin" / "python"
        if not python.is_file():
            print(f"Создаём окружение {venv.name} (нужен модуль python3-venv).")
            self.run([sys.executable, "-m", "venv", str(venv)])
        marker = venv / ".newbad-requirements"
        fingerprint = hashlib.sha256((self.root / "requirements.txt").read_bytes()).hexdigest()
        imports = "import fastapi, uvicorn, pydantic, httpx, yaml, numpy, fitz, rank_bm25, dotenv"
        if (not marker.exists() or marker.read_text() != fingerprint
                or not self.run([str(python), "-c", imports], quiet=True)):
            print("Устанавливаем Python-зависимости приложения…")
            self.run([str(python), "-m", "pip", "install", "-r", str(self.root / "requirements.txt")])
            self.run([str(python), "-c", imports])
            marker.write_text(fingerprint)
        self.run([str(python), "-m", "compileall", "-q", str(self.root / "app")])
        ui = self.root / "ui"
        marker = ui / "node_modules" / ".newbad-install-platform"
        fingerprint = hashlib.sha256((ui / "package-lock.json").read_bytes() + (ui / "package.json").read_bytes()
                                     + f"{sys.platform}:{platform.machine()}".encode()).hexdigest()
        if (not marker.exists() or marker.read_text() != fingerprint
                or not self.run([node, "-e", "require('esbuild').transformSync('let ready = true')"], cwd=ui, quiet=True)):
            print("Устанавливаем зависимости интерфейса для этой платформы…")
            self.run([npm, "ci"], cwd=ui)
            marker.write_text(fingerprint)
        return str(python), node

    def spawn(self, name: str, command: list[str]) -> tuple[subprocess.Popen, dict]:
        cwd = self.root / "ui" if name == "ui" else self.root
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            identity = process_identity(process.pid)
            if identity is None:
                raise LaunchError(f"{name.upper()} завершился при запуске. Подробности показаны выше в консоли.")
            return process, {"name": name, **identity}
        except BaseException:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
            raise

    def wait_ready(self, name: str, process: subprocess.Popen, *, timeout: float = 45) -> None:
        suffix = "/api/health" if name == "api" else "/"
        url = f"http://127.0.0.1:{self.ports[name]}{suffix}"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        if name != "api" or json.loads(response.read()).get("service") == "newbad":
                            return
            except (OSError, URLError, ValueError):
                pass
            time.sleep(0.25)
        raise LaunchError(f"{name.upper()} не прошёл проверку готовности. Подробности показаны выше в консоли.")

    def start_locked(self, records: list[dict], children: list[subprocess.Popen]) -> bool:
        active = [record for record in self.read_state() if owns_process(record)]
        if active:
            self.status()
            if {record["name"] for record in active} != set(self.ports):
                raise LaunchError("Обнаружен неполный запуск. Используйте restart.")
            print("Повторный запуск не требуется.")
            return False
        self.check_ports()
        python, node = self.prepare()
        api_command = [python, "-u", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.ports["api"])]
        if (self.root / ".env").is_file():
            # Let python-dotenv parse values; never execute .env as shell code.
            api_command += ["--env-file", str(self.root / ".env")]
        ui_command = [node, str(self.root / "ui" / "node_modules" / "vite" / "bin" / "vite.js"),
                      "--host", "127.0.0.1", "--port", str(self.ports["ui"]), "--strictPort"]
        for name, command in (("api", api_command), ("ui", ui_command)):
            process, record = self.spawn(name, command)
            children.append(process)
            records.append(record)
            self.save_state(records)
            self.wait_ready(name, process)
        return True

    def start(self, *, restart: bool = False) -> None:
        records, children = [], []
        try:
            with self.operation_lock():
                if restart:
                    self.stop()
                if not self.start_locked(records, children):
                    return
            self.status()
            print("Серверы работают в этой консоли. Ctrl+C или закрытие терминала остановит оба сервера.", flush=True)
            print("После изменения кода или .env используйте restart.", flush=True)
            if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
                try:
                    webbrowser.open(f"http://127.0.0.1:{self.ports['ui']}", new=2)
                except webbrowser.Error:
                    print("Откройте адрес интерфейса в браузере вручную.")
            while all(child.poll() is None for child in children):
                time.sleep(0.25)
            # A separate stop/restart command can end this run normally.
            if self.read_state() == records:
                raise LaunchError("Один из серверов завершился. Второй сервер также будет остановлен.")
        finally:
            # Always stop exactly this run, even if a concurrent restart has already
            # written new PIDs. Never stop the replacement servers from an old terminal.
            self.stop_records(records)
            for child in children:
                child.wait(timeout=3)
            if records:
                try:
                    with self.operation_lock():
                        if self.read_state() == records:
                            self.state_file.unlink(missing_ok=True)
                except LaunchError:
                    pass  # Another stop/restart owns the state file, or it needs inspection.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NewBad: запуск, остановка и перезапуск на Linux.")
    parser.add_argument("action", nargs="?", choices=("start", "stop", "restart", "status"))
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        print("Этот скрипт предназначен для Linux. В Windows используйте Windows_start.bat.", file=sys.stderr)
        return 1
    action = args.action
    if action is None:
        print("NewBad · Linux\n1 — запустить\n2 — остановить\n3 — перезапустить\n4 — состояние\n0 — выход")
        try:
            choice = input("Выбор: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice in {"0", "exit"}:
            return 0
        action = {"1": "start", "2": "stop", "3": "restart", "4": "status"}.get(choice, choice)
        if action not in {"start", "stop", "restart", "status"}:
            print("Выберите 0, 1, 2, 3 или 4.", file=sys.stderr)
            return 1
    launcher = Launcher(Path(__file__).resolve().parents[1])
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    original_handlers = {item: signal.getsignal(item) for item in signals}

    def request_shutdown(signum, _frame):
        for item in signals:
            signal.signal(item, signal.SIG_IGN)
        raise ShutdownRequested(signum)

    for item in signals:
        signal.signal(item, request_shutdown)
    try:
        if action in {"start", "restart"}:
            launcher.start(restart=action == "restart")
        else:
            with launcher.operation_lock():
                if action == "stop":
                    launcher.stop()
                else:
                    launcher.status()
    except ShutdownRequested as exc:
        print("\nЗапуск остановлен.")
        return 128 + exc.signum
    except (LaunchError, OSError, subprocess.SubprocessError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Запуск прерван.", file=sys.stderr)
        return 130
    finally:
        for item, handler in original_handlers.items():
            signal.signal(item, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
