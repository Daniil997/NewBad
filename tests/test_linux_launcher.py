"""Launcher checks use temporary projects and ports, never the running application."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from scripts import start_linux as launcher_module
from scripts.start_linux import LaunchError, Launcher, owns_process


def test_pid_reuse_and_foreign_process_group_are_rejected(monkeypatch):
    record = {"name": "api", "pid": 456, "pgid": 456, "started": "123"}
    monkeypatch.setattr(launcher_module, "process_identity", lambda pid: {
        "pid": pid, "pgid": 456, "started": "124",
    })
    assert not owns_process(record)
    monkeypatch.setattr(launcher_module, "process_identity", lambda pid: {
        "pid": pid, "pgid": 455, "started": "123",
    })
    assert not owns_process(record)
    monkeypatch.setattr(launcher_module, "process_identity", lambda pid: {
        "pid": pid, "pgid": 456, "started": "123",
    })
    assert owns_process(record)


def test_corrupt_state_is_preserved(tmp_path):
    launcher = Launcher(tmp_path)
    launcher.runtime.mkdir()
    launcher.state_file.write_text("invalid json", encoding="utf-8")
    with pytest.raises(LaunchError, match="Не удалось прочитать"):
        launcher.stop()
    assert launcher.state_file.read_text() == "invalid json"


def test_occupied_port_is_not_taken_over(tmp_path):
    launcher = Launcher(tmp_path)
    with socket.socket() as foreign_server:
        foreign_server.bind(("127.0.0.1", 0))
        foreign_server.listen()
        launcher.ports = {"api": foreign_server.getsockname()[1]}
        with pytest.raises(LaunchError, match="занят"):
            launcher.check_ports()
        assert foreign_server.fileno() >= 0


def test_env_is_passed_as_data_and_servers_have_console_output(tmp_path, monkeypatch):
    launcher = Launcher(tmp_path)
    launcher.runtime.mkdir()
    (tmp_path / ".env").write_text("KEY=$(must_not_execute)\n", encoding="utf-8")
    monkeypatch.setattr(launcher, "check_ports", lambda: None)
    monkeypatch.setattr(launcher, "prepare", lambda: ("python", "node"))
    monkeypatch.setattr(launcher, "wait_ready", lambda *args: None)
    commands = []

    def spawn(name, command):
        commands.append(command)
        return Mock(), {"name": name, "pid": 456, "pgid": 456, "started": "123"}

    monkeypatch.setattr(launcher, "spawn", spawn)
    assert launcher.start_locked([], [])
    assert commands[0][-2:] == ["--env-file", str(tmp_path / ".env")]
    assert "--strictPort" in commands[1]
    assert not (tmp_path / "data").exists()

    popen = Mock(return_value=Mock(pid=456))
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(launcher_module, "process_identity", lambda pid: {
        "pid": pid, "pgid": pid, "started": "123",
    })
    launcher.spawn = Launcher.spawn.__get__(launcher)
    launcher.spawn("ui", ["node", "vite.js"])
    assert popen.call_args.kwargs["cwd"] == tmp_path / "ui"
    assert "stdout" not in popen.call_args.kwargs
    assert "stderr" not in popen.call_args.kwargs


def test_prepare_preserves_windows_venv_and_reuses_installed_dependencies(tmp_path, monkeypatch):
    launcher = Launcher(tmp_path)
    windows_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    windows_python.parent.mkdir(parents=True)
    windows_python.write_bytes(b"existing Windows environment")
    (tmp_path / ".env").write_text("EXISTING=keep\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    ui = tmp_path / "ui"
    ui.mkdir()
    for name in ("package.json", "package-lock.json"):
        (ui / name).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(launcher_module.shutil, "which", lambda name: name)
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[1:3] == ["-m", "venv"]:
            python = Path(command[3]) / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.touch()
        elif command == ["npm", "ci"]:
            (ui / "node_modules").mkdir(exist_ok=True)
        return True

    monkeypatch.setattr(launcher, "run", run)
    python, node = launcher.prepare()
    assert Path(python) == tmp_path / ".venv-linux" / "bin" / "python"
    assert node == "node"
    assert ["npm", "ci"] in commands
    assert any("pip" in command for command in commands)
    assert windows_python.read_bytes() == b"existing Windows environment"
    assert (tmp_path / ".env").read_text() == "EXISTING=keep\n"
    commands.clear()
    launcher.prepare()
    assert ["npm", "ci"] not in commands
    assert not any("pip" in command for command in commands)
    assert not any("venv" in command for command in commands)


LINUX_ONLY = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux /proc and process signals")


@pytest.fixture
def supervised_servers(tmp_path):
    root = tmp_path / "Проект с пробелами"
    (root / "ui").mkdir(parents=True)
    stub = root / "server.py"
    stub.write_text('''
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
if sys.argv[2] == "fail":
    raise SystemExit(4)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"service": "newbad"}).encode())
    def log_message(self, *args):
        pass
print("server output " + sys.argv[1] + " " + str(os.getpid()), flush=True)
HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
''', encoding="utf-8")
    ports = []
    for _ in range(2):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            ports.append(probe.getsockname()[1])
    driver = root / "driver.py"
    driver.write_text(f'''
import sys
from pathlib import Path
sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})
from scripts import start_linux as m
launcher = m.Launcher(Path({str(root)!r}))
launcher.ports = {{"api": {ports[0]}, "ui": {ports[1]}}}
launcher.prepare = lambda: (sys.executable, sys.executable)
original_spawn = launcher.spawn
def spawn(name, command):
    mode = "fail" if name == "ui" and "--fail-ui" in sys.argv else "ok"
    return original_spawn(name, [sys.executable, "-u", {str(stub)!r}, str(launcher.ports[name]), mode])
launcher.spawn = spawn
m.Launcher = lambda root: launcher
raise SystemExit(m.main(["start"]))
''', encoding="utf-8")
    processes = []

    def launch(*args):
        env = {**os.environ, "DISPLAY": "", "WAYLAND_DISPLAY": "", "PYTHONIOENCODING": "utf-8"}
        process = subprocess.Popen([sys.executable, str(driver), *args], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, env=env)
        processes.append(process)
        return process, root / ".newbad-run" / "processes.json"

    yield launch
    for process in processes:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        if process.stdout:
            process.stdout.close()


def wait_for_records(process, state_file):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and process.poll() is None:
        if state_file.exists():
            records = json.loads(state_file.read_text(encoding="utf-8"))["processes"]
            if len(records) == 2:
                return records
        time.sleep(0.05)
    pytest.fail("Test servers did not start")


@LINUX_ONLY
@pytest.mark.parametrize("signum", [signal.SIGINT, getattr(signal, "SIGHUP", signal.SIGTERM)])
def test_console_interrupt_stops_both_servers(supervised_servers, signum):
    process, state_file = supervised_servers()
    records = wait_for_records(process, state_file)
    process.send_signal(signum)
    output, _ = process.communicate(timeout=20)
    assert process.returncode == 128 + signum, output.decode()
    assert not state_file.exists()
    assert not any(owns_process(record) for record in records)
    assert b"server output" in output


@LINUX_ONLY
def test_failed_frontend_also_stops_api(supervised_servers):
    process, state_file = supervised_servers("--fail-ui")
    output, _ = process.communicate(timeout=20)
    assert process.returncode == 1, output.decode()
    assert not state_file.exists()
    assert b"server output" in output
    server_pids = [int(line.split()[-1]) for line in output.splitlines() if line.startswith(b"server output")]
    assert server_pids
    assert all(launcher_module.process_identity(pid) is None for pid in server_pids)


@LINUX_ONLY
def test_unexpected_server_exit_stops_other_server(supervised_servers):
    process, state_file = supervised_servers()
    records = wait_for_records(process, state_file)
    os.kill(records[0]["pid"], signal.SIGTERM)
    output, _ = process.communicate(timeout=20)
    assert process.returncode == 1, output.decode()
    assert not state_file.exists()
    assert not any(owns_process(record) for record in records)
