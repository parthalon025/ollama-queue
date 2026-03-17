# setup-remote-agent.ps1 — One-command backend agent setup for Windows Docker Desktop hosts.
#
# Usage (PowerShell):
#   .\setup-remote-agent.ps1 -QueueUrl http://100.68.34.41:7683
#
# Or with explicit backend URL:
#   .\setup-remote-agent.ps1 -QueueUrl http://100.68.34.41:7683 -BackendUrl http://100.91.20.72:11434
#
# Prerequisites: Docker Desktop running, Tailscale connected, NVIDIA GPU drivers installed.

param(
    [Parameter(Mandatory=$true)]
    [string]$QueueUrl,

    [string]$BackendUrl = "",
    [string]$AgentImage = "ghcr.io/parthalon025/ollama-backend-agent:latest",
    [string]$AgentContainer = "ollama-backend-agent",
    [int]$AgentPort = 11435,
    [int]$TimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"

# ── Helpers ──────────────────────────────────────────────────────────────────

function Write-Status($msg) { Write-Host "  $msg" -ForegroundColor Cyan }
function Write-Ok($msg) { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  WARN  $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "  FAIL  $msg" -ForegroundColor Red }

# ── Auto-detect Tailscale IP ─────────────────────────────────────────────────

if (-not $BackendUrl) {
    Write-Status "Auto-detecting Tailscale IP..."
    try {
        $tsIp = (tailscale ip -4 2>$null).Trim()
        if ($tsIp) {
            $BackendUrl = "http://${tsIp}:11434"
            Write-Ok "Tailscale IP: $tsIp -> BackendUrl: $BackendUrl"
        } else {
            Write-Err "Could not detect Tailscale IP. Pass -BackendUrl explicitly."
            exit 1
        }
    } catch {
        Write-Err "Tailscale not found or not connected. Pass -BackendUrl explicitly."
        exit 1
    }
}

# ── Banner ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  Ollama Backend Agent Setup" -ForegroundColor White -BackgroundColor DarkBlue
Write-Host ""
Write-Host "  Queue:   $QueueUrl"
Write-Host "  Backend: $BackendUrl"
Write-Host "  Image:   $AgentImage"
Write-Host ""

# ── Check Docker ─────────────────────────────────────────────────────────────

Write-Status "Checking Docker..."
try {
    $null = docker version --format '{{.Server.Version}}' 2>$null
    Write-Ok "Docker is running"
} catch {
    Write-Err "Docker is not running. Start Docker Desktop first."
    exit 1
}

# ── Check GPU ────────────────────────────────────────────────────────────────

$gpuFlags = @()
Write-Status "Checking GPU..."
try {
    $gpuInfo = nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null
    if ($LASTEXITCODE -eq 0 -and $gpuInfo) {
        Write-Ok "GPU: $($gpuInfo.Trim())"
        $gpuFlags = @("--gpus", "all")
    } else {
        Write-Warn "nvidia-smi returned no data (agent will run without GPU metrics)"
    }
} catch {
    Write-Warn "nvidia-smi not found (agent will run without GPU metrics)"
}

# ── Check Ollama ─────────────────────────────────────────────────────────────

Write-Status "Checking Ollama..."
try {
    $ollamaResp = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 -ErrorAction Stop
    $modelCount = ($ollamaResp.models | Measure-Object).Count
    Write-Ok "Ollama is running ($modelCount models installed)"
} catch {
    Write-Warn "Ollama not reachable on localhost:11434 (agent will start but heartbeat will report unhealthy)"
}

# ── Remove old container ────────────────────────────────────────────────────

$existing = docker ps -a --filter "name=$AgentContainer" --format '{{.Names}}' 2>$null
if ($existing -eq $AgentContainer) {
    Write-Status "Removing existing agent container..."
    docker stop $AgentContainer 2>$null | Out-Null
    docker rm $AgentContainer 2>$null | Out-Null
    Write-Ok "Old container removed"
}

# Also clean up legacy docker-mgmt sidecar
$legacy = docker ps -a --filter "name=docker-mgmt" --format '{{.Names}}' 2>$null
if ($legacy -eq "docker-mgmt") {
    Write-Status "Removing legacy docker-mgmt sidecar..."
    docker stop docker-mgmt 2>$null | Out-Null
    docker rm docker-mgmt 2>$null | Out-Null
    Write-Ok "Legacy sidecar removed"
}

# ── Pull image ──────────────────────────────────────────────────────────────

Write-Status "Pulling agent image..."
docker pull $AgentImage
if ($LASTEXITCODE -ne 0) {
    Write-Err "Failed to pull image. Check network connectivity."
    exit 1
}
Write-Ok "Image pulled"

# ── Start container ─────────────────────────────────────────────────────────

Write-Status "Starting backend agent..."
$dockerArgs = @(
    "run", "-d",
    "--name", $AgentContainer,
    "--restart", "unless-stopped"
) + $gpuFlags + @(
    "-p", "${AgentPort}:${AgentPort}",
    "-v", "/var/run/docker.sock:/var/run/docker.sock",
    "-v", "ollama-agent-data:/data",
    "-e", "QUEUE_URL=$QueueUrl",
    "-e", "OLLAMA_URL=http://host.docker.internal:11434",
    "-e", "BACKEND_URL=$BackendUrl",
    $AgentImage
)
docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    Write-Err "Failed to start container. Check docker logs $AgentContainer"
    exit 1
}
Write-Ok "Container started"

# ── Wait for local health ──────────────────────────────────────────────────

Write-Status "Waiting for agent health..."
$healthy = $false
for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod -Uri "http://localhost:$AgentPort/health" -TimeoutSec 2 -ErrorAction Stop
        $healthy = $true
        break
    } catch {
        Write-Host "." -NoNewline
    }
}
Write-Host ""

if (-not $healthy) {
    Write-Err "Agent health check timed out after 30s"
    Write-Host "  Check logs: docker logs $AgentContainer"
    exit 1
}
Write-Ok "Agent is healthy"

# ── Verify queue registration (heartbeat landed) ───────────────────────────

Write-Status "Waiting for queue registration (heartbeat)..."
$registered = $false
$backendNorm = $BackendUrl.TrimEnd("/")
for ($i = 0; $i -lt $TimeoutSeconds; $i += 5) {
    Start-Sleep -Seconds 5
    try {
        $backends = Invoke-RestMethod -Uri "$QueueUrl/api/backends" -TimeoutSec 5 -ErrorAction Stop
        foreach ($b in $backends) {
            if ($b.url.TrimEnd("/") -eq $backendNorm) {
                $registered = $true
                # Show registration details
                Write-Host ""
                Write-Ok "Registered with queue!"
                Write-Host ""
                Write-Host "  --- Backend Status ---" -ForegroundColor White
                Write-Host "  URL:          $($b.url)"
                Write-Host "  Healthy:      $($b.healthy)"
                Write-Host "  GPU:          $($b.gpu_name)"
                Write-Host "  VRAM:         $($b.vram_pct)%"
                Write-Host "  Models:       $($b.model_count)"
                Write-Host "  Loaded:       $($b.loaded_models -join ', ')"
                break
            }
        }
        if ($registered) { break }
    } catch {
        Write-Host "." -NoNewline
    }
}
Write-Host ""

if (-not $registered) {
    Write-Warn "Queue registration not confirmed after ${TimeoutSeconds}s"
    Write-Host "  The agent is running but the queue may not have received the heartbeat yet."
    Write-Host "  Check: curl $QueueUrl/api/backends"
    Write-Host "  Logs:  docker logs $AgentContainer"
    exit 0
}

# ── Summary ──────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  Setup Complete" -ForegroundColor White -BackgroundColor DarkGreen
Write-Host ""
Write-Host "  Agent:     http://localhost:$AgentPort"
Write-Host "  Status:    http://localhost:$AgentPort/status"
Write-Host "  Queue:     $QueueUrl"
Write-Host "  Backend:   $BackendUrl"
Write-Host ""
Write-Host "  Model reconciliation starts automatically (~10s)."
Write-Host "  Heartbeat pushes every 30s."
Write-Host ""
Write-Host "  Commands:"
Write-Host "    docker logs $AgentContainer          # View logs"
Write-Host "    docker restart $AgentContainer       # Restart agent"
Write-Host "    Invoke-RestMethod http://localhost:${AgentPort}/status  # Check status"
Write-Host ""
