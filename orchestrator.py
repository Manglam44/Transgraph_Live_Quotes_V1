#!/usr/bin/env python3
"""
orchestrator.py -- single-process supervisor for the IBKR -> QuestDB -> FastAPI
pipeline, replacing docker-compose's restart/health-check behavior.

Scope (deliberately):
    - Does NOT run QuestDB or IB Gateway. Those run as their own systemd
      services (see questdb.service / ibgateway.service) because they are
      not part of this Python codebase and have their own lifecycles.
    - DOES run: the FastAPI app (via uvicorn) and all six streamers, as
      subprocesses, exactly the way docker-compose.yml did.

What it reproduces from docker-compose.yml:
    - `depends_on: questdb: condition: service_healthy`
          -> wait_for_port() blocks startup until QuestDB is reachable.
    - `restart: unless-stopped`
          -> each child is restarted on unexpected exit, with exponential
             backoff so a persistently-broken process doesn't spin.
    - SIGTERM handling on `docker compose down`
          -> the same signal is forwarded to every child, so streamers get
             the chance to flush buffered rows before the process dies.

Run directly for local/dev use, or via ibkr-orchestrator.service in
production (see DEPLOYMENT.md).
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------- 
# Paths / config
# --------------------------------------------------------------------------- 

APP_DIR = Path(os.environ.get("IBKR_APP_DIR", Path(__file__).resolve().parent))
LOG_DIR = Path(os.environ.get("IBKR_LOG_DIR", "/var/log/ibkr-pipeline"))
PYTHON = os.environ.get("IBKR_PYTHON", sys.executable)

load_dotenv(APP_DIR / ".env")

QDB_HOST = os.environ.get("QDB_HOST", "127.0.0.1")
QDB_HTTP_PORT = int(os.environ.get("QDB_HTTP_PORT", "9000"))
IB_HOST = os.environ.get("IB_HOST", "127.0.0.1")
IB_PORT = int(os.environ.get("IB_PORT", "7496"))

API_HOST = os.environ.get("API_BIND_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))
API_APP_IMPORT = os.environ.get("API_APP_IMPORT", "src.api.app:app")
# The API only does read queries against QuestDB (no shared in-process
# state), so multiple uvicorn workers is safe and gives real throughput
# on a multi-vCPU EC2 box -- something the old single Docker container
# wasn't set up to do. Default to 2; raise to roughly (vCPUs - 1) if the
# streamers leave headroom, and watch QuestDB's own connection count.
API_WORKERS = int(os.environ.get("API_WORKERS", "2"))

RESTART_MIN_BACKOFF = float(os.environ.get("ORCH_RESTART_MIN_BACKOFF_SECONDS", "2"))
RESTART_MAX_BACKOFF = float(os.environ.get("ORCH_RESTART_MAX_BACKOFF_SECONDS", "60"))
STABLE_AFTER_SECONDS = float(os.environ.get("ORCH_STABLE_AFTER_SECONDS", "120"))
SHUTDOWN_TIMEOUT_SECONDS = float(os.environ.get("ORCH_SHUTDOWN_TIMEOUT_SECONDS", "20"))
QDB_WAIT_TIMEOUT_SECONDS = float(os.environ.get("ORCH_QDB_WAIT_TIMEOUT_SECONDS", "120"))
IB_WAIT_TIMEOUT_SECONDS = float(os.environ.get("ORCH_IB_WAIT_TIMEOUT_SECONDS", "30"))

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [orchestrator] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "orchestrator.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("orchestrator")


# --------------------------------------------------------------------------- 
# Process specs -- mirrors docker-compose.yml's `command:` lines 1:1
# --------------------------------------------------------------------------- 

@dataclass
class ProcSpec:
    name: str
    cmd: list[str]
    log_file: str = field(init=False)

    def __post_init__(self) -> None:
        self.log_file = f"{self.name}.log"


def build_specs() -> list[ProcSpec]:
    return [
        ProcSpec("api", [
            PYTHON, "-u", "-m", "uvicorn", API_APP_IMPORT,
            "--host", API_HOST, "--port", str(API_PORT),
            "--workers", str(API_WORKERS),
        ]),
        ProcSpec("streamer-commodity-spot", [
            PYTHON, "-u", "-m", "src.streamers.commodity_spot", "--mode", "delayed",
        ]),
        ProcSpec("streamer-commodity-futures", [
            PYTHON, "-u", "-m", "src.streamers.commodity_futures", "--mode", "live",
        ]),
        ProcSpec("streamer-commodity-options", [
            PYTHON, "-u", "-m", "src.streamers.commodity_options", "--mode", "delayed",
        ]),
        ProcSpec("streamer-currency-spot", [
            PYTHON, "-u", "-m", "src.streamers.currency_spot", "--mode", "live",
        ]),
        ProcSpec("streamer-currency-futures", [
            PYTHON, "-u", "-m", "src.streamers.currency_futures", "--mode", "delayed",
        ]),
        ProcSpec("streamer-currency-options", [
            PYTHON, "-u", "-m", "src.streamers.currency_options", "--mode", "delayed",
        ]),
    ]


# --------------------------------------------------------------------------- 
# Helpers
# --------------------------------------------------------------------------- 

def wait_for_port(host: str, port: int, timeout: float, label: str) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=3):
                log.info("%s is reachable at %s:%s", label, host, port)
                return True
        except OSError:
            time.sleep(2)
    log.warning(
        "%s not reachable at %s:%s after %.0fs -- proceeding anyway "
        "(dependent processes have their own reconnect/backoff logic)",
        label, host, port, timeout,
    )
    return False


# --------------------------------------------------------------------------- 
# Supervisor
# --------------------------------------------------------------------------- 

class Supervisor:
    def __init__(self, specs: list[ProcSpec]):
        self.specs = specs
        self.stop_event = threading.Event()
        self.procs: dict[str, subprocess.Popen] = {}
        self.threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        for spec in self.specs:
            t = threading.Thread(target=self._run_forever, args=(spec,), daemon=True)
            self.threads.append(t)
            t.start()

    def _run_forever(self, spec: ProcSpec) -> None:
        backoff = RESTART_MIN_BACKOFF
        while not self.stop_event.is_set():
            started_at = time.monotonic()
            log.info("starting %s: %s", spec.name, " ".join(spec.cmd))

            log_path = LOG_DIR / spec.log_file
            with open(log_path, "a") as lf:
                try:
                    proc = subprocess.Popen(
                        spec.cmd,
                        cwd=str(APP_DIR),
                        stdout=lf,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,  # own process group -> clean signal delivery
                    )
                except FileNotFoundError as exc:
                    log.error("%s failed to launch (%s); retrying in %.0fs",
                              spec.name, exc, backoff)
                    if self.stop_event.wait(backoff):
                        return
                    backoff = min(backoff * 2, RESTART_MAX_BACKOFF)
                    continue

                with self._lock:
                    self.procs[spec.name] = proc

                returncode = proc.wait()
                ran_for = time.monotonic() - started_at

            if self.stop_event.is_set():
                log.info("%s exited (code=%s) during shutdown", spec.name, returncode)
                return

            if ran_for >= STABLE_AFTER_SECONDS:
                backoff = RESTART_MIN_BACKOFF  # it ran fine for a while; reset
            else:
                backoff = min(backoff * 2, RESTART_MAX_BACKOFF)

            log.warning(
                "%s exited unexpectedly (code=%s) after %.0fs; restarting in %.0fs",
                spec.name, returncode, ran_for, backoff,
            )
            if self.stop_event.wait(backoff):
                return

    def shutdown(self, sig: int) -> None:
        log.info("shutdown requested (signal %s) -- forwarding to children", sig)
        self.stop_event.set()

        with self._lock:
            procs = list(self.procs.items())

        for name, proc in procs:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), sig)
                except ProcessLookupError:
                    pass

        deadline = time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS
        for name, proc in procs:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                proc.wait(timeout=remaining)
                log.info("%s stopped cleanly", name)
            except subprocess.TimeoutExpired:
                log.warning("%s did not stop in time -- sending SIGKILL", name)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass


def main() -> None:
    log.info("orchestrator starting; app dir=%s", APP_DIR)

    wait_for_port(QDB_HOST, QDB_HTTP_PORT, QDB_WAIT_TIMEOUT_SECONDS, "QuestDB")
    wait_for_port(IB_HOST, IB_PORT, IB_WAIT_TIMEOUT_SECONDS, "IB Gateway")

    supervisor = Supervisor(build_specs())

    def handle_signal(signum, _frame):
        supervisor.shutdown(signum)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    supervisor.start()

    # Block the main thread until a shutdown signal sets stop_event.
    while not supervisor.stop_event.is_set():
        time.sleep(1)

    for t in supervisor.threads:
        t.join(timeout=SHUTDOWN_TIMEOUT_SECONDS + 5)

    log.info("orchestrator stopped")


if __name__ == "__main__":
    main()
