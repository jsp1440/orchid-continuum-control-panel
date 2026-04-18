#!/usr/bin/env python3
"""
Orchid Continuum Orchestrator
Safe companion launcher for the Orchid Continuum runtime.

IMPORTANT
---------
- This file is intentionally separate from app.py
- Do NOT use this as the Render web service start command unless you explicitly
  want orchestration behavior
- Keep Render using: uvicorn app:app --host 0.0.0.0 --port $PORT

Typical use
-----------
python orchestrator.py doctor
python orchestrator.py status
python orchestrator.py start api
python orchestrator.py start worker
python orchestrator.py start scheduler
python orchestrator.py start api worker scheduler
python orchestrator.py stop api
python orchestrator.py restart scheduler

Environment variables
---------------------
OC_API_ENABLED=true|false
OC_WORKER_ENABLED=true|false
OC_SCHEDULER_ENABLED=true|false
OC_FRONTEND_ENABLED=true|false

OC_API_PORT=8000
OC_FRONTEND_PORT=5173

OC_API_MODULE=app:app
OC_API_FILE=
OC_WORKER_FILE=worker_main.py
OC_SCHEDULER_FILE=continuous_harvest_orchestrator.py

OC_PID_DIR=.orchestrator
OC_LOG_DIR=.orchestrator/logs
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parent
PID_DIR = ROOT / os.getenv("OC_PID_DIR", ".orchestrator")
LOG_DIR = ROOT / os.getenv("OC_LOG_DIR", ".orchestrator/logs")

DEFAULT_API_MODULE = os.getenv("OC_API_MODULE", "app:app").strip()
DEFAULT_API_FILE = os.getenv("OC_API_FILE", "").strip()
DEFAULT_WORKER_FILE = os.getenv("OC_WORKER_FILE", "worker_main.py").strip()
DEFAULT_SCHEDULER_FILE = os.getenv(
    "OC_SCHEDULER_FILE", "continuous_harvest_orchestrator.py"
).strip()

API_PORT = int(os.getenv("OC_API_PORT", "8000"))
FRONTEND_PORT = int(os.getenv("OC_FRONTEND_PORT", "5173"))

COMPONENT_FLAGS = {
    "api": os.getenv("OC_API_ENABLED", "true").lower() == "true",
    "worker": os.getenv("OC_WORKER_ENABLED", "false").lower() == "true",
    "scheduler": os.getenv("OC_SCHEDULER_ENABLED", "false").lower() == "true",
    "frontend": os.getenv("OC_FRONTEND_ENABLED", "false").lower() == "true",
}


@dataclass
class ComponentSpec:
    name: str
    enabled: bool
    command: List[str]
    cwd: Path
    log_file: Path
    pid_file: Path
    description: str


def ensure_dirs() -> None:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def command_exists(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


def path_exists(path_str: str) -> bool:
    if not path_str:
        return False
    return (ROOT / path_str).exists()


def first_existing(paths: List[str]) -> Optional[str]:
    for path_str in paths:
        if path_str and path_exists(path_str):
            return path_str
    return None


def pid_file_for(name: str) -> Path:
    return PID_DIR / f"{name}.pid"


def log_file_for(name: str) -> Path:
    return LOG_DIR / f"{name}.log"


def read_pid(pid_file: Path) -> Optional[int]:
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except Exception:
        return None


def write_pid(pid_file: Path, pid: int) -> None:
    pid_file.write_text(str(pid))


def remove_pid(pid_file: Path) -> None:
    try:
        pid_file.unlink(missing_ok=True)
    except Exception:
        pass


def is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_pid(pid: int, timeout: float = 10.0) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_process_running(pid):
            return True
        time.sleep(0.25)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    time.sleep(0.5)
    return not is_process_running(pid)


def module_path_looks_valid(module_spec: str) -> bool:
    if ":" not in module_spec:
        return False
    module_name, app_name = module_spec.split(":", 1)
    if not module_name or not app_name:
        return False

    module_path = ROOT.joinpath(*module_name.split(".")).with_suffix(".py")
    return module_path.exists()


def python_file_to_module(path_str: str) -> str:
    path = Path(path_str).with_suffix("")
    return ".".join(path.parts) + ":app"


def discover_api_spec() -> Tuple[Optional[List[str]], str]:
    if not command_exists("uvicorn"):
        return None, "uvicorn not found in PATH"

    if DEFAULT_API_MODULE and module_path_looks_valid(DEFAULT_API_MODULE):
        return (
            [
                "uvicorn",
                DEFAULT_API_MODULE,
                "--host",
                "0.0.0.0",
                "--port",
                str(API_PORT),
            ],
            f"module launch via {DEFAULT_API_MODULE}",
        )

    if DEFAULT_API_FILE and path_exists(DEFAULT_API_FILE):
        return (
            [sys.executable, str(ROOT / DEFAULT_API_FILE)],
            f"file launch via {DEFAULT_API_FILE}",
        )

    likely_api_files = [
        "app.py",
        "api/main.py",
        "api/server.py",
        "app/main.py",
    ]
    found = first_existing(likely_api_files)
    if found:
        return (
            [
                "uvicorn",
                python_file_to_module(found),
                "--host",
                "0.0.0.0",
                "--port",
                str(API_PORT),
            ],
            f"discovered module from {found}",
        )

    return None, "no API entrypoint found"


def discover_worker_spec() -> Tuple[Optional[List[str]], str]:
    likely_worker_files = [
        DEFAULT_WORKER_FILE,
        "worker_main.py",
        "worker_entrypoint.py",
        "worker/worker.py",
        "workers/run_worker.py",
        "app/worker.py",
    ]
    found = first_existing(likely_worker_files)
    if not found:
        return None, "no worker entrypoint found"

    return [sys.executable, str(ROOT / found)], f"file launch via {found}"


def discover_scheduler_spec() -> Tuple[Optional[List[str]], str]:
    likely_scheduler_files = [
        DEFAULT_SCHEDULER_FILE,
        "continuous_harvest_orchestrator.py",
        "run_harvests.py",
        "scripts/run_overnight_harvest.py",
    ]
    found = first_existing(likely_scheduler_files)
    if not found:
        return None, "no scheduler/harvester entrypoint found"

    return [sys.executable, str(ROOT / found)], f"file launch via {found}"


def discover_frontend_spec() -> Tuple[Optional[List[str]], str]:
    if not (ROOT / "package.json").exists():
        return None, "no package.json found"

    if not command_exists("npm"):
        return None, "npm not found in PATH"

    return [
        "npm",
        "run",
        "dev",
        "--",
        "--port",
        str(FRONTEND_PORT),
    ], "npm dev server"


def build_components() -> Dict[str, ComponentSpec]:
    ensure_dirs()

    api_cmd, api_desc = discover_api_spec()
    worker_cmd, worker_desc = discover_worker_spec()
    scheduler_cmd, scheduler_desc = discover_scheduler_spec()
    frontend_cmd, frontend_desc = discover_frontend_spec()

    components: Dict[str, ComponentSpec] = {}

    if api_cmd:
        components["api"] = ComponentSpec(
            name="api",
            enabled=COMPONENT_FLAGS["api"],
            command=api_cmd,
            cwd=ROOT,
            log_file=log_file_for("api"),
            pid_file=pid_file_for("api"),
            description=api_desc,
        )

    if worker_cmd:
        components["worker"] = ComponentSpec(
            name="worker",
            enabled=COMPONENT_FLAGS["worker"],
            command=worker_cmd,
            cwd=ROOT,
            log_file=log_file_for("worker"),
            pid_file=pid_file_for("worker"),
            description=worker_desc,
        )

    if scheduler_cmd:
        components["scheduler"] = ComponentSpec(
            name="scheduler",
            enabled=COMPONENT_FLAGS["scheduler"],
            command=scheduler_cmd,
            cwd=ROOT,
            log_file=log_file_for("scheduler"),
            pid_file=pid_file_for("scheduler"),
            description=scheduler_desc,
        )

    if frontend_cmd:
        components["frontend"] = ComponentSpec(
            name="frontend",
            enabled=COMPONENT_FLAGS["frontend"],
            command=frontend_cmd,
            cwd=ROOT,
            log_file=log_file_for("frontend"),
            pid_file=pid_file_for("frontend"),
            description=frontend_desc,
        )

    return components


def start_component(spec: ComponentSpec) -> None:
    existing_pid = read_pid(spec.pid_file)
    if existing_pid and is_process_running(existing_pid):
        print(f"[SKIP] {spec.name}: already running with PID {existing_pid}")
        return

    spec.log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(spec.log_file, "ab") as log_handle:
        process = subprocess.Popen(
            spec.command,
            cwd=spec.cwd,
            stdout=log_handle,
            stderr=log_handle,
            start_new_session=True,
            env=os.environ.copy(),
        )

    write_pid(spec.pid_file, process.pid)
    print(f"[STARTED] {spec.name}: PID {process.pid}")
    print(f"          cmd: {' '.join(shlex.quote(part) for part in spec.command)}")
    print(f"          log: {rel(spec.log_file)}")
    print(f"          via: {spec.description}")


def stop_component(spec: ComponentSpec) -> None:
    pid = read_pid(spec.pid_file)
    if not pid:
        print(f"[SKIP] {spec.name}: no PID file")
        return

    if not is_process_running(pid):
        print(f"[CLEANUP] {spec.name}: stale PID {pid}")
        remove_pid(spec.pid_file)
        return

    if stop_pid(pid):
        print(f"[STOPPED] {spec.name}: PID {pid}")
        remove_pid(spec.pid_file)
    else:
        print(f"[ERROR] {spec.name}: failed to stop PID {pid}")


def resolve_targets(components: Dict[str, ComponentSpec], targets: List[str]) -> List[ComponentSpec]:
    order = ["api", "worker", "scheduler", "frontend"]

    if not targets:
        return [components[name] for name in order if name in components]

    selected: List[ComponentSpec] = []
    for target in targets:
        if target not in components:
            print(f"[WARN] Unknown or unavailable component: {target}")
            continue
        selected.append(components[target])
    return selected


def status_components(components: Dict[str, ComponentSpec]) -> None:
    print("\nOrchid Continuum Orchestrator Status")
    print("=" * 64)
    for name in ["api", "worker", "scheduler", "frontend"]:
        spec = components.get(name)
        if not spec:
            print(f"{name:10} MISSING   entrypoint not found")
            continue

        pid = read_pid(spec.pid_file)
        running = bool(pid and is_process_running(pid))
        state = "RUNNING" if running else "STOPPED"
        pid_display = str(pid) if pid else "-"
        print(
            f"{name:10} {state:8} enabled={str(spec.enabled):5} pid={pid_display:>6}  via={spec.description}"
        )
    print("")


def doctor(components: Dict[str, ComponentSpec]) -> None:
    print("\nOrchid Continuum Doctor")
    print("=" * 64)
    print(f"Root directory: {ROOT}")
    print(f"Python:         {sys.executable}")
    print(f"PID dir:        {PID_DIR}")
    print(f"Log dir:        {LOG_DIR}")
    print("")

    checks = [
        ("uvicorn in PATH", command_exists("uvicorn")),
        ("npm in PATH", command_exists("npm")),
        ("package.json present", (ROOT / "package.json").exists()),
        ("app.py present", (ROOT / "app.py").exists()),
        ("api/main.py present", (ROOT / "api/main.py").exists()),
        ("api/server.py present", (ROOT / "api/server.py").exists()),
        ("worker_main.py present", (ROOT / "worker_main.py").exists()),
        ("worker_entrypoint.py present", (ROOT / "worker_entrypoint.py").exists()),
        ("continuous_harvest_orchestrator.py present", (ROOT / "continuous_harvest_orchestrator.py").exists()),
        ("run_harvests.py present", (ROOT / "run_harvests.py").exists()),
        ("DATABASE_URL set", bool(os.getenv("DATABASE_URL"))),
    ]

    for label, ok in checks:
        print(f"{'[OK]' if ok else '[--]'} {label}")

    status_components(components)


def start_selected(components: Dict[str, ComponentSpec], targets: List[str]) -> None:
    for spec in resolve_targets(components, targets):
        if not spec.enabled and not targets:
            print(f"[DISABLED] {spec.name}: set OC_{spec.name.upper()}_ENABLED=true to auto-start")
            continue
        start_component(spec)


def stop_selected(components: Dict[str, ComponentSpec], targets: List[str]) -> None:
    for spec in resolve_targets(components, targets):
        stop_component(spec)


def restart_selected(components: Dict[str, ComponentSpec], targets: List[str]) -> None:
    selected = resolve_targets(components, targets)
    for spec in selected:
        stop_component(spec)
    time.sleep(0.5)
    for spec in selected:
        if not spec.enabled and not targets:
            print(f"[DISABLED] {spec.name}: set OC_{spec.name.upper()}_ENABLED=true to auto-start")
            continue
        start_component(spec)


def print_help() -> None:
    print(
        """
Orchid Continuum Orchestrator

Commands
--------
python orchestrator.py doctor
python orchestrator.py status
python orchestrator.py start
python orchestrator.py start api
python orchestrator.py start worker
python orchestrator.py start scheduler
python orchestrator.py start frontend
python orchestrator.py stop
python orchestrator.py restart

Environment variables
---------------------
OC_API_ENABLED=true|false
OC_WORKER_ENABLED=true|false
OC_SCHEDULER_ENABLED=true|false
OC_FRONTEND_ENABLED=true|false

OC_API_PORT=8000
OC_FRONTEND_PORT=5173

OC_API_MODULE=app:app
OC_API_FILE=
OC_WORKER_FILE=worker_main.py
OC_SCHEDULER_FILE=continuous_harvest_orchestrator.py
"""
    )


def main() -> int:
    ensure_dirs()
    components = build_components()

    if len(sys.argv) < 2:
        print_help()
        return 0

    command = sys.argv[1].lower()
    targets = [arg.lower() for arg in sys.argv[2:]]

    if command == "doctor":
        doctor(components)
        return 0

    if command == "status":
        status_components(components)
        return 0

    if command == "start":
        start_selected(components, targets)
        return 0

    if command == "stop":
        stop_selected(components, targets)
        return 0

    if command == "restart":
        restart_selected(components, targets)
        return 0

    if command in {"help", "-h", "--help"}:
        print_help()
        return 0

    print(f"Unknown command: {command}")
    print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
