"""Start isolated real SM2 backend + Vite, verify in a browser, and stop both.

uv run --project backend python scripts/smoke_core.py --channel chromium
Requires frontend dependencies and the selected Playwright browser installed.
No private keys or existing local workbench data are overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from start_core import prepare


def free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def wait_ready(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Server exited before readiness: {url}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise TimeoutError(f"Server did not become ready: {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--channel", default="msedge" if os.name == "nt" else "chromium"
    )
    parser.add_argument(
        "--script", action="append", help="Repository browser script; repeatable"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if node is None:
        parser.error("Node.js is required on PATH")
    runtime = (
        root / ".runtime/core-smoke" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    env = {**os.environ, **prepare(root, runtime=runtime)}
    backend_port, frontend_port = free_port(), free_port()
    while frontend_port == backend_port:
        frontend_port = free_port()
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    env.update(
        VITE_API_PROXY_TARGET=backend_url,
        AEGIS_BROWSER_URL=frontend_url,
        AEGIS_BROWSER_CHANNEL=args.channel,
        AEGIS_BROWSER_WORKSPACE=env["WORKSPACE_ROOT"],
    )
    processes = []
    # CREATE_NO_WINDOW avoids opening terminal windows on the user's desktop.
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        with (
            (runtime / "backend.log").open("wb") as backend_log,
            (runtime / "frontend.log").open("wb") as frontend_log,
        ):
            backend = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "ra_agent.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(backend_port),
                ],
                cwd=root,
                env=env,
                stdout=backend_log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
            processes.append(backend)
            wait_ready(backend_url + "/api/v1/health", backend)
            frontend = subprocess.Popen(
                [
                    node,
                    str(root / "frontend/node_modules/vite/bin/vite.js"),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(frontend_port),
                    "--strictPort",
                ],
                cwd=root / "frontend",
                env=env,
                stdout=frontend_log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
            processes.append(frontend)
            wait_ready(frontend_url + "/api/v1/health", frontend)
            for script in args.script or ["scripts/verify_core_browser.mjs"]:
                subprocess.run(
                    [
                        sys.executable if script.endswith(".py") else node,
                        str(root / script),
                    ],
                    cwd=root,
                    env=env,
                    check=True,
                    timeout=240,
                    creationflags=flags,
                    stdout=sys.stdout,
                    stderr=sys.stderr,
                )
        print(
            json.dumps(
                {
                    "passed": True,
                    "runtime": str(runtime),
                    "crypto": "real-sm2",
                    "browser": args.channel,
                }
            )
        )
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(main())
