#!/usr/bin/env bash
# backend-onboard.sh — Pull all required models on an Ollama backend.
#
# Usage:
#   ./backend-onboard.sh [BACKEND_URL]
#
# BACKEND_URL defaults to http://127.0.0.1:11434
# Run this whenever you add a new backend to OLLAMA_BACKENDS.
#
# Examples:
#   ./backend-onboard.sh                              # local
#   ./backend-onboard.sh http://100.114.197.57:11434  # remote 5080
#   ./backend-onboard.sh http://100.x.x.x:11434       # any Tailscale peer

set -euo pipefail

BACKEND_URL="${1:-http://127.0.0.1:11434}"
TIMEOUT_CONNECT=5
TIMEOUT_PULL=3600  # 1h max per model (large models take time)

# ── Canonical required models ──────────────────────────────────────────────
# Update this list when adding new models to any project.
# Source of truth: ~/.claude/docs/ollama-models.md + project configs.
#
# NOTE: bitnet:10b is intentionally excluded — it is NOT an Ollama model.
# It runs via bitnet-server.service (llama-server on port 11435) and is
# routed by the queue via the bitnet: prefix. It cannot be `ollama pull`ed.
# To check bitnet status: systemctl --user status bitnet-server
REQUIRED_MODELS=(
    "qwen3.5:9b"                         # default reasoning, graphrag, eval variant M
    "qwen3.5:4b"                         # GPU-native fast tasks
    "qwen3.5:2b"                         # ultra-fast classification (telegram-agent)
    "qwen2.5:7b"                         # ha-aria pattern analysis, fast text
    "qwen2.5-coder:14b"                  # best coder (Code Factory)
    "qwen3:14b"                          # eval variants D/E/G, high-stakes analysis
    "deepseek-r1:8b"                     # eval judge (DEFAULT_JUDGE_MODEL)
    "deepseek-r1:8b-0528-qwen3-q4_K_M"  # principle extraction, research pipeline
    "gemma3:12b"                         # binary eval judge (DEFAULT_BINARY_JUDGE_MODEL)
    "nomic-embed-text"                   # embeddings: lessons-db, notion-tools
    "functiongemma:latest"               # function calling / tool dispatch
    "fixt/home-3b-v3"                    # HA entity classification (ha-aria)
    "qwen3-vl:4b"                        # vision tasks, screenshot analysis
)

# ── Helpers ────────────────────────────────────────────────────────────────

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

check_connectivity() {
    if ! curl -sf --connect-timeout "$TIMEOUT_CONNECT" "${BACKEND_URL}/api/tags" > /dev/null 2>&1; then
        red "ERROR: Cannot reach ${BACKEND_URL} — is Ollama running on that host?"
        exit 1
    fi
}

get_installed_models() {
    curl -sf --connect-timeout "$TIMEOUT_CONNECT" "${BACKEND_URL}/api/tags" \
        | python3 -c "import json,sys; [print(m['name']) for m in json.load(sys.stdin)['models']]" \
        2>/dev/null || true
}

pull_model() {
    local model="$1"
    printf "  Pulling %-45s ... " "$model"
    local result
    result=$(curl -sf --connect-timeout "$TIMEOUT_CONNECT" \
        --max-time "$TIMEOUT_PULL" \
        --no-buffer \
        -X POST "${BACKEND_URL}/api/pull" \
        -H "Content-Type: application/json" \
        -d "{\"model\": \"${model}\"}" \
        2>&1 | tail -1)

    if echo "$result" | grep -q '"status":"success"'; then
        green "done"
        return 0
    else
        red "FAILED"
        echo "    Last response: $result" >&2
        return 1
    fi
}

# ── Main ───────────────────────────────────────────────────────────────────

bold "Ollama Backend Onboarding"
echo "  Backend: ${BACKEND_URL}"
echo ""

check_connectivity

# Get GPU info if available (via queue health endpoint on same host)
HOST=$(python3 -c "from urllib.parse import urlparse; print(urlparse('${BACKEND_URL}').hostname)")
QUEUE_URL="http://${HOST}:7683"
GPU_NAME=$(curl -sf --connect-timeout 2 "${QUEUE_URL}/api/health" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('gpu_name','unknown'))" 2>/dev/null || echo "unknown")
echo "  GPU: ${GPU_NAME}"
echo ""

# Build installed model set
mapfile -t installed < <(get_installed_models)
declare -A installed_set
for m in "${installed[@]}"; do
    installed_set["$m"]=1
done

# Audit
missing=()
present=()
for model in "${REQUIRED_MODELS[@]}"; do
    if [[ -n "${installed_set[$model]+_}" ]]; then
        present+=("$model")
    else
        missing+=("$model")
    fi
done

bold "Model Audit (${#REQUIRED_MODELS[@]} required):"
for m in "${present[@]}"; do
    printf "  ✅  %s\n" "$m"
done
for m in "${missing[@]}"; do
    printf "  ❌  %s\n" "$m"
done
echo ""

if [[ ${#missing[@]} -eq 0 ]]; then
    green "All required models present — nothing to do."
    exit 0
fi

bold "Pulling ${#missing[@]} missing models:"
failed=()
for model in "${missing[@]}"; do
    if ! pull_model "$model"; then
        failed+=("$model")
    fi
done

echo ""
if [[ ${#failed[@]} -eq 0 ]]; then
    green "All models installed successfully."
else
    yellow "Completed with ${#failed[@]} failure(s):"
    for m in "${failed[@]}"; do
        red "  - $m"
    done
    echo "  Re-run this script to retry failed models."
    exit 1
fi
