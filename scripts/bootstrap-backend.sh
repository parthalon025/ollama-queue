#!/usr/bin/env bash
# bootstrap-backend.sh — One-command setup for a new Ollama backend host.
#
# Usage:
#   curl -sL https://raw.githubusercontent.com/parthalon025/ollama-queue/main/scripts/bootstrap-backend.sh | \
#     bash -s -- --queue http://<queue-ip>:7683 --backend-url http://<this-tailscale-ip>:11434
#
# Prerequisites: Docker installed, Tailscale connected.

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────

QUEUE_URL=""
BACKEND_URL=""
OLLAMA_CONTAINER="ollama"
AGENT_IMAGE="ghcr.io/parthalon025/ollama-backend-agent:latest"
AGENT_CONTAINER="ollama-agent"
AGENT_PORT=11435

# ── Helpers ──────────────────────────────────────────────────────────────────

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

usage() {
    echo "Usage: bootstrap-backend.sh --queue <QUEUE_URL> --backend-url <BACKEND_URL>"
    echo ""
    echo "Options:"
    echo "  --queue        Queue server URL (e.g., http://100.68.34.41:7683)"
    echo "  --backend-url  This host's Tailscale-routable Ollama URL (e.g., http://100.91.20.72:11434)"
    echo "  --help         Show this help"
    exit 1
}

# ── Parse args ───────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --queue)       QUEUE_URL="$2"; shift 2 ;;
        --backend-url) BACKEND_URL="$2"; shift 2 ;;
        --help)        usage ;;
        *)             red "Unknown option: $1"; usage ;;
    esac
done

[[ -z "$QUEUE_URL" ]] && { red "ERROR: --queue is required"; usage; }

# Auto-detect Tailscale IP if --backend-url not provided
if [[ -z "$BACKEND_URL" ]]; then
    if command -v tailscale &> /dev/null; then
        TS_IP=$(tailscale ip -4 2>/dev/null | head -1)
        if [[ -n "$TS_IP" ]]; then
            BACKEND_URL="http://${TS_IP}:11434"
            yellow "Auto-detected Tailscale IP: ${TS_IP}"
        fi
    fi
    [[ -z "$BACKEND_URL" ]] && { red "ERROR: --backend-url is required (Tailscale auto-detect failed)"; usage; }
fi

# ── Checks ───────────────────────────────────────────────────────────────────

bold "Ollama Backend Bootstrap"
echo "  Queue:   ${QUEUE_URL}"
echo "  Backend: ${BACKEND_URL}"
echo ""

if ! command -v docker &> /dev/null; then
    red "ERROR: Docker is not installed."
    echo "  Install: https://docs.docker.com/engine/install/"
    exit 1
fi

# ── Detect GPU ───────────────────────────────────────────────────────────────

GPU_FLAGS=""
if command -v nvidia-smi &> /dev/null; then
    bold "NVIDIA GPU detected"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || true
    GPU_FLAGS="--gpus all"
    echo ""
fi

# ── Start Ollama ─────────────────────────────────────────────────────────────

if docker inspect "$OLLAMA_CONTAINER" > /dev/null 2>&1; then
    green "Ollama container '${OLLAMA_CONTAINER}' already running."
else
    bold "Starting Ollama container..."
    # shellcheck disable=SC2086
    docker run -d --name "$OLLAMA_CONTAINER" \
        --restart always \
        $GPU_FLAGS \
        -p 11434:11434 \
        -v ollama:/root/.ollama \
        ollama/ollama:latest
    green "Ollama started."
fi
echo ""

# ── Start Agent ──────────────────────────────────────────────────────────────

# Remove old sidecar if present
if docker inspect docker-mgmt > /dev/null 2>&1; then
    yellow "Removing old docker-mgmt sidecar..."
    docker stop docker-mgmt 2>/dev/null || true
    docker rm docker-mgmt 2>/dev/null || true
fi

# Remove old agent if present (for re-runs)
if docker inspect "$AGENT_CONTAINER" > /dev/null 2>&1; then
    yellow "Removing existing agent container..."
    docker stop "$AGENT_CONTAINER" 2>/dev/null || true
    docker rm "$AGENT_CONTAINER" 2>/dev/null || true
fi

bold "Pulling backend agent image..."
docker pull "$AGENT_IMAGE"

bold "Starting backend agent..."
docker run -d --name "$AGENT_CONTAINER" \
    --restart always \
    -p ${AGENT_PORT}:${AGENT_PORT} \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v ollama:/ollama:ro \
    -v ollama-agent-data:/data \
    -e QUEUE_URL="$QUEUE_URL" \
    -e OLLAMA_URL="http://host.docker.internal:11434" \
    -e BACKEND_URL="$BACKEND_URL" \
    "$AGENT_IMAGE"

# ── Wait for health ──────────────────────────────────────────────────────────

bold "Waiting for agent health check..."
AGENT_HEALTHY=false
for i in $(seq 1 15); do
    if curl -sf --connect-timeout 2 "http://localhost:${AGENT_PORT}/health" > /dev/null 2>&1; then
        AGENT_HEALTHY=true
        green "Agent is healthy!"
        echo ""
        bold "Agent Status:"
        curl -sf "http://localhost:${AGENT_PORT}/health" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for k, v in d.items():
    print(f'  {k}: {v}')
" 2>/dev/null || true
        echo ""
        break
    fi
    sleep 2
done

if ! $AGENT_HEALTHY; then
    yellow "Agent health check timed out after 30s."
    echo "  Check logs: docker logs ${AGENT_CONTAINER}"
    exit 1
fi

# ── Verify queue registration ───────────────────────────────────────────────

bold "Verifying queue registration..."
BACKEND_NORM="${BACKEND_URL%/}"
REGISTERED=false
for i in $(seq 1 12); do
    BACKENDS_JSON=$(curl -sf --connect-timeout 5 "${QUEUE_URL}/api/backends" 2>/dev/null || true)
    if [[ -n "$BACKENDS_JSON" ]] && echo "$BACKENDS_JSON" | python3 -c "
import json, sys
backends = json.load(sys.stdin)
target = '${BACKEND_NORM}'
for b in backends:
    if b['url'].rstrip('/') == target:
        print(f\"  URL:     {b['url']}\")
        print(f\"  Healthy: {b.get('healthy', '?')}\")
        print(f\"  GPU:     {b.get('gpu_name', 'unknown')}\")
        print(f\"  VRAM:    {b.get('vram_pct', '?')}%\")
        print(f\"  Models:  {b.get('model_count', '?')}\")
        sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
        REGISTERED=true
        break
    fi
    sleep 5
done

if $REGISTERED; then
    echo ""
    green "Registered with queue!"
else
    echo ""
    yellow "Queue registration not confirmed after 60s."
    echo "  Agent is running — heartbeat may still be in progress."
    echo "  Check: curl ${QUEUE_URL}/api/backends"
fi

green "Bootstrap complete!"
echo "  Agent:         http://localhost:${AGENT_PORT}"
echo "  Reconciliation will start automatically (models sync in ~10s)"
echo "  Monitor:       curl http://localhost:${AGENT_PORT}/status"
