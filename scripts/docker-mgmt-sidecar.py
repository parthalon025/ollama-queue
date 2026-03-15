#!/usr/bin/env python3
"""docker-mgmt-sidecar.py — Minimal Docker management HTTP sidecar.

What it does: Exposes a small HTTP API that controls the Docker daemon on the
host (via mounted socket). Designed to run alongside Ollama on remote machines
so backend-onboard.sh can trigger image updates without needing SSH.

Deploy once per remote backend:
    docker run -d --name docker-mgmt \
      --restart always \
      -p 11435:11435 \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -e MGMT_TOKEN=<strong-random-token> \
      python:3.12-slim \
      bash -c "pip install fastapi uvicorn docker -q && python3 /app/docker-mgmt-sidecar.py"

Endpoints:
    GET  /health                          — liveness check (no auth)
    POST /update-ollama?token=<MGMT_TOKEN> — pull + recreate the ollama container
"""

import os
import sys
import time

import docker
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

app = FastAPI(title="docker-mgmt-sidecar", docs_url=None, redoc_url=None)

MGMT_TOKEN = os.environ.get("MGMT_TOKEN", "")
OLLAMA_CONTAINER = os.environ.get("OLLAMA_CONTAINER", "ollama")
OLLAMA_IMAGE = "ollama/ollama:latest"
PORT = int(os.environ.get("MGMT_PORT", "11435"))

if not MGMT_TOKEN:
    print("ERROR: MGMT_TOKEN env var is required", file=sys.stderr)
    sys.exit(1)


def _auth(token: str) -> None:
    if token != MGMT_TOKEN:
        raise HTTPException(status_code=401, detail="invalid token")


def _docker_client() -> docker.DockerClient:
    return docker.from_env()


@app.get("/health")
def health():
    return {"ok": True, "container": OLLAMA_CONTAINER}


@app.post("/update-ollama")
def update_ollama(token: str = Query(...)):
    """Pull ollama/ollama:latest and recreate the ollama container with same config."""
    _auth(token)
    log = []

    try:
        client = _docker_client()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Docker socket unavailable: {e}") from e

    # 1. Pull latest image
    log.append("Pulling ollama/ollama:latest...")
    try:
        client.images.pull("ollama/ollama", tag="latest")
        log.append("Pull complete.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pull failed: {e}") from e

    # 2. Inspect existing container to capture its config
    run_config: dict = {}
    try:
        old = client.containers.get(OLLAMA_CONTAINER)
        hc = old.attrs["HostConfig"]
        cfg = old.attrs["Config"]

        # Port bindings: {container_port: [{"HostPort": "..."}]}
        port_bindings = hc.get("PortBindings") or {}

        # Volume bindings: list of "name:/path" or "/host:/path"
        binds = hc.get("Binds") or []
        # Filter out the docker socket itself if somehow mounted
        binds = [b for b in binds if "docker.sock" not in b]

        run_config = {
            "image": OLLAMA_IMAGE,
            "name": OLLAMA_CONTAINER,
            "detach": True,
            "restart_policy": {"Name": (hc.get("RestartPolicy") or {}).get("Name", "always")},
            "ports": {k: v[0]["HostPort"] for k, v in port_bindings.items() if v},
            "volumes": binds,
            "environment": cfg.get("Env") or [],
            "runtime": hc.get("Runtime"),
            "device_requests": hc.get("DeviceRequests"),
        }
        # Strip None values
        run_config = {k: v for k, v in run_config.items() if v is not None}
        log.append(f"Captured config: ports={run_config.get('ports')} volumes={run_config.get('volumes')}")

        # 3. Stop and remove old container
        log.append("Stopping old container...")
        old.stop(timeout=10)
        old.remove()
        log.append("Removed old container.")

    except docker.errors.NotFound:
        # Container didn't exist — use safe defaults
        log.append(f"Container '{OLLAMA_CONTAINER}' not found — using defaults.")
        run_config = {
            "image": OLLAMA_IMAGE,
            "name": OLLAMA_CONTAINER,
            "detach": True,
            "restart_policy": {"Name": "always"},
            "ports": {"11434/tcp": "11434"},
            "volumes": ["ollama:/root/.ollama"],
        }

    # 4. Recreate container
    log.append("Starting new container...")
    try:
        container = client.containers.run(**run_config)
        # Give it a moment to start
        time.sleep(2)
        container.reload()
        status = container.status
        log.append(f"Container started (status={status}).")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Recreate failed: {e}\nLog: {log}") from e

    return JSONResponse({"ok": True, "log": log, "status": status})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")  # noqa: S104 — intentional: sidecar must bind all interfaces inside Docker
