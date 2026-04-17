#!/usr/bin/env python3
"""
Orchid Continuum Orchestrator
Single entrypoint to launch and manage the Orchid Continuum runtime.

Purpose
-------
This script acts as a system orchestrator for the Orchid Continuum workspace.
It can launch:
  - API backend
  - worker process
  - scheduler / harvester process
  - frontend dev server (optional)

Design goals
------------
- Safe: only launches files that actually exist
- Configurable: behavior controlled by environment variables
- Additive: does not require deleting existing systems
- Transparent: prints exactly what it is doing
- Portable: works in Replit, local shell, or Linux-like environments

Usage
-----
python app.py status
python app.py start
python app.py start api
python app.py start worker
python app.py start scheduler
python app.py start frontend
python app.py stop
python app.py restart
python app.py doctor

Environment Variables
---------------------
OC_API_ENABLED=true|false
OC_WORKER_ENABLED=true|false
OC_SCHEDULER_ENABLED=true|false
OC_FRONTEND_ENABLED=true|false

OC_API_PORT=8000
OC_FRONTEND_PORT=5173

OC_API_MODULE=api.main:app
OC_API_FILE=
OC_WORKER_FILE=worker_main.py
OC_SCHEDULER_FILE=continuous_harvest_orchestrator.py

OC_PID_DIR=.orchestrator
OC_LOG_DIR=.orchestrator/logs

Notes
-----
1. This script prefers module-based uvicorn launch for the API.
2. If OC_API_MODULE fails or does not exist, you can set OC_API_FILE instead.
3. The frontend launch assumes npm is available and package.json exists.
4. This is an orchestrator, not a supervisor daemon. It starts processes and records PIDs.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# --------------------------------------------------
# CONFIG
# --------------------------------------------------

ROOT = Path(__file__).resolve().parent
PID_DIR = ROOT / os.getenv("OC_PID_DIR", ".orchestrator")
LOG_DIR = ROOT / os.getenv("OC_LOG_DIR", ".orchestrator/logs")

DEFAULT_API_MODULE = os.getenv("OC_API_MODULE", "api.main:app")
DEFAULT_API_FILE = os.getenv("OC_API_FILE", "").strip()
DEFAULT_WORKER_FILE = os.getenv("OC_WORKER_FILE", "worker_main.py").strip()
DEFAULT_SCHEDULER_FILE = os.getenv(
    "OC_SCHEDULER_FILE", "continuous_harvest_orchestrator.py"
).strip()

API_PORT = int(os.getenv("OC_API_PORT", "8000"))
FRONTEND_PORT = int(os.getenv("OC_FRONTEND_PORT", "5173"))

COMPONENT_FLAGS = {
    "api": os.getenv("OC_API_ENABLED", "true").lower() == "true",
    "worker": os.getenv("OC_WORKER_ENABLED", "true").lower() == "true",
    "scheduler": os.getenv("OC_SCHEDULER_ENABLED", "true").lower() == "true",
    "frontend": os.getenv("OC_FRONTEND_ENABLED", "false").lower() == "true",
}


# --------------------------------------------------
# DATA STRUCTURES
# --------------------------------------------------

@dataclass
class ComponentSpec:
    name: str
    enabled: bool
    command: List[str]
    cwd: Path
    log_file: Path
    pid_file: Path
    description: str


# --------------------------------------------------
# FILESYSTEM HELPERS
# --------------------------------------------------

def ensure_dirs() -> None:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def path_exists(path_str: str) -> bool:
    if not path_str:
        return False
    return (ROOT / path_str).exists()


def first_existing(paths: List[str]) -> Optional[str]:
    for p in paths:
        if p and (ROOT / p).exists():
            return p
    return None


def command_exists(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None


# --------------------------------------------------
# PID / PROCESS HELPERS
# --------------------------------------------------

def pid_file_for(name: str) -> Path:
    return PID_DIR / f"{name}.pid"


def log_file_for(name: str) -> Path:
    return LOG_DIR / f"{name}.log"


def read_pid(pid_file: Path) -> Optional[int]:
    if not pid_file.exists():
        return None
    try:
        content = pid_file.read_text().strip()
        return int(content)
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


# --------------------------------------------------
# DISCOVERY
# --------------------------------------------------

def discover_api_spec() -> Tuple[Optional[List[str]], str]:
    """
    Prefer uvicorn module launch.
    Fall back to uvicorn with file reference only if explicitly configured.
    """
    if not command_exists("uvicorn"):
        return None, "uvicorn not found in PATH"

    if module_path_looks_valid(DEFAULT_API_MODULE):
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
        api_file = ROOT / DEFAULT_API_FILE
        return (
            [
                sys.executable,
                str(api_file),
            ],
            f"file launch via {rel(api_file)}",
        )

    likely_api_files = [
        "api/main.py",
        "api/server.py",
        "app/main.py",
        "app.py",
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
        "python3 scripts/run_overnight_harvest.py",
    ]
    found = first_existing(likely_scheduler_files)
    if not found:
        return None, "no scheduler/harvester entrypoint found"

    weird_prefix = "python3 "
    if found.startswith(weird_prefix):
        actual = found[len(weird_prefix):]
        if path_exists(actual):
            return [sys.executable, str(ROOT / actual)], f"file launch via {actual}"
        return None, f"scheduler path string exists but target missing: {actual}"

    return [sys.executable, str(ROOT / found)], f"file launch via {found}"


def discover_frontend_spec() -> Tuple[Optional[List[str]], str]:
    package_json = ROOT / "package.json"
    if not package_json.exists():
        return None, "no package.json found"

    if command_exists("npm"):
        return ["npm", "run", "dev", "--", "--port", str(FRONTEND_PORT)], "npm dev server"

    return None, "npm not found in PATH"


def module_path_looks_valid(module_spec: str) -> bool:
    if ":" not in module_spec:
        return False
    module_name, app_name = module_spec.split(":", 1)
    if not module_name or not app_name:
        return False

    parts = module_name.split(".")
    candidate = ROOT.joinpath(*parts).with_suffix(".py")
    return candidate.exists()


def python_file_to_module(path_str: str) -> str:
    path = Path(path_str)
    without_suffix = path.with_suffix("")
    return ".".join(without_suffix.parts) + ":app"


# --------------------------------------------------
# COMPONENT BUILDERS
# --------------------------------------------------

def build_components() -> Dict[str, ComponentSpec]:
    ensure_dirs()

    api_cmd, api_msg = discover_api_spec()
    worker_cmd, worker_msg = discover_worker_spec()
    scheduler_cmd, scheduler_msg = discover_scheduler_spec()
    frontend_cmd, frontend_msg = discover_frontend_spec()

    components: Dict[str, ComponentSpec] = {}

    if api_cmd:
        components["api"] = ComponentSpec(
            name="api",
            enabled=COMPONENT_FLAGS["api"],
            command=api_cmd,
            cwd=ROOT,
            log_file=log_file_for("api"),
            pid_file=pid_file_for("api"),
            description=api_msg,
        )

    if worker_cmd:
        components["worker"] = ComponentSpec(
            name="worker",
            enabled=COMPONENT_FLAGS["worker"],
            command=worker_cmd,
            cwd=ROOT,
            log_file=log_file_for("worker"),
            pid_file=pid_file_for("worker"),
            description=worker_msg,
        )

    if scheduler_cmd:
        components["scheduler"] = ComponentSpec(
            name="scheduler",
            enabled=COMPONENT_FLAGS["scheduler"],
            command=scheduler_cmd,
            cwd=ROOT,
            log_file=log_file_for("scheduler"),
            pid_file=pid_file_for("scheduler"),
            description=scheduler_msg,
        )

    if frontend_cmd:
        components["frontend"] = ComponentSpec(
            name="frontend",
            enabled=COMPONENT_FLAGS["frontend"],
            command=frontend_cmd,
            cwd=ROOT,
            log_file=log_file_for("frontend"),
            pid_file=pid_file_for("frontend"),
            description=frontend_msg,
        )

    return components


# --------------------------------------------------
# START / STOP / STATUS
# --------------------------------------------------

def start_component(spec: ComponentSpec) -> None:
    existing_pid = read_pid(spec.pid_file)
    if existing_pid and is_process_running(existing_pid):
        print(f"[SKIP] {spec.name}: already running with PID {existing_pid}")
        return

    spec.log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(spec.log_file, "ab") as log:
        process = subprocess.Popen(
            spec.command,
            cwd=spec.cwd,
            stdout=log,
            stderr=log,
            start_new_session=True,
            env=os.environ.copy(),
        )
    write_pid(spec.pid_file, process.pid)
    print(f"[STARTED] {spec.name}: PID {process.pid}")
    print(f"          cmd: {' '.join(shlex.quote(x) for x in spec.command)}")
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

    ok = stop_pid(pid)
    if ok:
        print(f"[STOPPED] {spec.name}: PID {pid}")
        remove_pid(spec.pid_file)
    else:
        print(f"[ERROR] {spec.name}: failed to stop PID {pid}")


def status_components(components: Dict[str, ComponentSpec]) -> None:
    print("\nOrchid Continuum Orchestrator Status")
    print("=" * 60)
    for name in ["api", "worker", "scheduler", "frontend"]:
        spec = components.get(name)
        if not spec:
            print(f"{name:10} MISSING   entrypoint not found")
            continue

        pid = read_pid(spec.pid_file)
        if pid and is_process_running(pid):
            state = "RUNNING"
        else:
            state = "STOPPED"
        print(
            f"{name:10} {state:8} enabled={spec.enabled!s:5} "
            f"pid={pid if pid else '-':>6}  via={spec.description}"
        )
    print("")


def doctor(components: Dict[str, ComponentSpec]) -> None:
    print("\nOrchid Continuum Doctor")
    print("=" * 60)
    print(f"Root directory: {ROOT}")
    print(f"Python:         {sys.executable}")
    print(f"PID dir:        {PID_DIR}")
    print(f"Log dir:        {LOG_DIR}")
    print("")

    checks = [
        ("uvicorn in PATH", command_exists("uvicorn")),
        ("npm in PATH", command_exists("npm")),
        ("package.json", (ROOT / "package.json").exists()),
        ("api/main.py", (ROOT / "api/main.py").exists()),
        ("api/server.py", (ROOT / "api/server.py").exists()),
        ("worker_main.py", (ROOT / "worker_main.py").exists()),
        ("worker_entrypoint.py", (ROOT / "worker_entrypoint.py").exists()),
        ("continuous_harvest_orchestrator.py", (ROOT / "continuous_harvest_orchestrator.py").exists()),
        ("run_harvests.py", (ROOT / "run_harvests.py").exists()),
        ("DATABASE_URL set", bool(os.getenv("DATABASE_URL"))),
    ]

    for label, ok in checks:
        print(f"{'[OK]' if ok else '[--]'} {label}")

    print("")
    status_components(components)


def start_selected(components: Dict[str, ComponentSpec], targets: List[str]) -> None:
    selected = resolve_targets(components, targets)
    for spec in selected:
        if not spec.enabled and not targets:
            print(f"[DISABLED] {spec.name}: set OC_{spec.name.upper()}_ENABLED=true to auto-start")
            continue
        start_component(spec)


def stop_selected(components: Dict[str, ComponentSpec], targets: List[str]) -> None:
    selected = resolve_targets(components, targets)
    for spec in selected:
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


def resolve_targets(components: Dict[str, ComponentSpec], targets: List[str]) -> List[ComponentSpec]:
    ordered_names = ["api", "worker", "scheduler", "frontend"]

    if not targets:
        return [components[name] for name in ordered_names if name in components]

    selected = []
    for name in targets:
        if name not in components:
            print(f"[WARN] Unknown or unavailable component: {name}")
            continue
        selected.append(components[name])
    return selected


# --------------------------------------------------
# CLI
# --------------------------------------------------

def print_help() -> None:
    print(
        """
Orchid Continuum Orchestrator

Commands
--------
python app.py status
python app.py doctor
python app.py start
python app.py start api
python app.py start worker
python app.py start scheduler
python app.py start frontend
python app.py stop
python app.py restart

Examples
--------
python app.py doctor
python app.py start
python app.py start api frontend
python app.py stop worker scheduler

Environment variables
---------------------
OC_API_ENABLED=true|false
OC_WORKER_ENABLED=true|false
OC_SCHEDULER_ENABLED=true|false
OC_FRONTEND_ENABLED=true|false

OC_API_MODULE=api.main:app
OC_API_FILE=
OC_WORKER_FILE=worker_main.py
OC_SCHEDULER_FILE=continuous_harvest_orchestrator.py

OC_API_PORT=8000
OC_FRONTEND_PORT=5173
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

    if command == "status":
        status_components(components)
        return 0

    if command == "doctor":
        doctor(components)
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
