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

# ── Docker update options ───────────────────────────────────────────────────
# --no-docker-update  skip the Ollama Docker update step entirely
# SSH_KEY             override SSH identity file (default: ~/.ssh/id_ed25519)
SKIP_DOCKER_UPDATE=false
SSH_KEY="${SSH_KEY:-${HOME}/.ssh/id_ed25519}"
OLLAMA_CONTAINER="${OLLAMA_CONTAINER:-ollama}"  # docker container name on remote host
MGMT_TOKEN="${MGMT_TOKEN:-}"                    # token for docker-mgmt-sidecar (port 11435)
MGMT_PORT="${MGMT_PORT:-11435}"

for arg in "$@"; do
    [[ "$arg" == "--no-docker-update" ]] && SKIP_DOCKER_UPDATE=true
done

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

update_ollama_docker() {
    local host="$1"
    local is_local=false
    [[ "$host" == "127.0.0.1" || "$host" == "localhost" ]] && is_local=true

    bold "Ollama Docker Update:"

    # Shared helper: pull new image then stop/rm/run to actually use it
    # docker restart keeps the old image — must recreate the container
    _recreate_ollama() {
        local run_cmd="$1"
        echo "  Pulling latest ollama/ollama image..."
        if ! docker pull ollama/ollama:latest 2>&1 | tail -3; then
            yellow "  docker pull failed — skipping recreate."
            return 1
        fi
        echo "  Stopping and removing old container '${OLLAMA_CONTAINER}'..."
        docker stop "$OLLAMA_CONTAINER" > /dev/null 2>&1 || true
        docker rm   "$OLLAMA_CONTAINER" > /dev/null 2>&1 || true
        echo "  Starting new container..."
        if eval "$run_cmd" > /dev/null 2>&1; then
            green "  '${OLLAMA_CONTAINER}' recreated with latest Ollama image."
        else
            yellow "  Container recreate failed — check docker run args."
            return 1
        fi
    }

    if $is_local; then
        if docker inspect "$OLLAMA_CONTAINER" > /dev/null 2>&1; then
            # Capture the original run command so we can recreate with same flags
            local run_args
            run_args=$(docker inspect --format \
                'docker run -d --name {{.Name}} --restart {{.HostConfig.RestartPolicy.Name}} {{range $p,$b := .HostConfig.PortBindings}}-p {{(index $b 0).HostPort}}:{{$p}} {{end}}{{range .Mounts}}-v {{.Name}}:{{.Destination}} {{end}}{{.Config.Image}}' \
                "$OLLAMA_CONTAINER" 2>/dev/null | sed 's|^/||')
            _recreate_ollama "$run_args"
        else
            yellow "  No local Docker container named '${OLLAMA_CONTAINER}' found — skipping."
        fi
        echo ""
        return
    fi

    # Remote: try sidecar API first (docker-mgmt-sidecar on port 11435), then SSH
    local mgmt_url="http://${host}:${MGMT_PORT}"
    if [[ -n "$MGMT_TOKEN" ]] && curl -sf --connect-timeout 3 "${mgmt_url}/health" > /dev/null 2>&1; then
        echo "  docker-mgmt-sidecar reachable at ${mgmt_url} — updating via API..."
        local resp
        resp=$(curl -sf --connect-timeout 5 --max-time 120 \
            -X POST "${mgmt_url}/update-ollama?token=${MGMT_TOKEN}" 2>&1)
        if echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); [print('  ' + l) for l in d['log']]; sys.exit(0 if d['ok'] else 1)" 2>/dev/null; then
            green "  Ollama updated via sidecar on ${host}."
        else
            yellow "  Sidecar update failed: ${resp}"
        fi
        echo ""
        return
    fi

    # Remote: try SSH
    local ssh_opts="-o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes"
    if [[ -f "$SSH_KEY" ]]; then
        ssh_opts+=" -i ${SSH_KEY}"
    fi

    echo "  Attempting SSH to ${host}..."
    # shellcheck disable=SC2086
    if ssh $ssh_opts "$host" true 2>/dev/null; then
        echo "  SSH connected — updating Ollama Docker image..."
        # Capture existing run args on remote, then recreate
        local remote_run_args
        # shellcheck disable=SC2086
        remote_run_args=$(ssh $ssh_opts "$host" \
            "docker inspect --format 'docker run -d --name {{.Name}} --restart {{.HostConfig.RestartPolicy.Name}} \$(docker inspect --format \"{{range \\\$p,\\\$b := .HostConfig.PortBindings}}-p {{(index \\\$b 0).HostPort}}:{{\\\$p}} {{end}}\" ${OLLAMA_CONTAINER}) \$(docker inspect --format \"{{range .Mounts}}-v {{.Name}}:{{.Destination}} {{end}}\" ${OLLAMA_CONTAINER}) {{.Config.Image}}' ${OLLAMA_CONTAINER}" 2>/dev/null \
            | sed 's|^/||' || echo "")
        if [[ -z "$remote_run_args" ]]; then
            # Fallback: simple default run command
            remote_run_args="docker run -d --name ${OLLAMA_CONTAINER} --restart always -p 11434:11434 -v ollama:/root/.ollama ollama/ollama:latest"
            yellow "  Could not inspect existing container — using default run args."
        fi
        # shellcheck disable=SC2086
        ssh $ssh_opts "$host" "
            docker pull ollama/ollama:latest
            docker stop ${OLLAMA_CONTAINER} 2>/dev/null || true
            docker rm   ${OLLAMA_CONTAINER} 2>/dev/null || true
            ${remote_run_args}
        " 2>&1 | tail -5 \
            && green "  Remote '${OLLAMA_CONTAINER}' recreated with latest Ollama image." \
            || yellow "  Remote recreate failed — update manually on ${host}."
    else
        yellow "  SSH to ${host} not available — skipping Docker update."
        echo "  To update manually on ${host}:"
        echo "    docker pull ollama/ollama:latest"
        echo "    docker stop ${OLLAMA_CONTAINER} && docker rm ${OLLAMA_CONTAINER}"
        echo "    docker run -d --name ${OLLAMA_CONTAINER} --restart always -p 11434:11434 -v ollama:/root/.ollama ollama/ollama:latest"
    fi
    echo ""
}

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

HOST=$(python3 -c "from urllib.parse import urlparse; print(urlparse('${BACKEND_URL}').hostname)")

check_connectivity

# Optionally update Ollama Docker image on the backend host
if ! $SKIP_DOCKER_UPDATE; then
    update_ollama_docker "$HOST"
fi

# Get GPU info if available (via queue health endpoint on same host)
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
