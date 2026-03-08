# Ollama MITM Proxy — Research Report
**Date:** 2026-03-08
**Scope:** FastAPI streaming proxy, iptables/nftables transparent redirect, Ollama wire format

---

## 1. Ollama Streaming Wire Format

Source: `https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md`

### Content-Type
Ollama does **not** set `Content-Type: text/event-stream` (SSE). It returns **NDJSON** (newline-delimited JSON). Each chunk is a complete JSON object followed by `\n`. There are no `data:` prefixes. Do not treat it as SSE.

### stream=true vs stream=false

| Aspect | stream=true | stream=false |
|---|---|---|
| Response | Multiple JSON objects, one per line | Single JSON object |
| Timing | First token arrives immediately | Waits for full completion |
| `done` field | `false` on intermediate chunks, `true` on final | Always `true` |
| Metrics | Only in final chunk | In the single response |

### Chunk format — `/api/generate`
```
{"model":"llama3.2","created_at":"2023-08-04T08:52:19.385406455-07:00","response":"The","done":false}
{"model":"llama3.2","created_at":"2023-08-04T08:52:19.385406455-07:00","response":" sky","done":false}
{"model":"llama3.2","created_at":"2023-08-04T19:22:45.499127Z","response":"","done":true,"context":[1,2,3],"total_duration":10706818083,"load_duration":6338219291,"prompt_eval_count":26,"prompt_eval_duration":130079000,"eval_count":259,"eval_duration":4232710000}
```

### Chunk format — `/api/chat`
```
{"model":"llama3.2","created_at":"2023-08-04T08:52:19.385406455-07:00","message":{"role":"assistant","content":"The","images":null},"done":false}
{"model":"llama3.2","created_at":"2023-08-04T19:22:45.499127Z","message":{"role":"assistant","content":""},"done":true,"total_duration":4883583458,"load_duration":1334875,"prompt_eval_count":26,"prompt_eval_duration":342546000,"eval_count":282,"eval_duration":4535599000}
```

### Key observations for proxy design
- The proxy can intercept at the chunk level (one JSON object per yield) or at the raw byte level (passthrough).
- To inspect tokens without buffering, parse `\n`-delimited lines and decode each as JSON.
- The final `done=true` chunk is the hook point for post-completion actions (logging, metrics, cost tracking).
- No special headers needed beyond standard `Content-Type: application/json`. Ollama does **not** require `Accept: text/event-stream`.

---

## 2. FastAPI Streaming Proxy Passthrough

### Canonical pattern — transparent passthrough

The authoritative pattern from the FastAPI community (discussion #7382):

```python
import httpx
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse
from starlette.background import BackgroundTask

app = FastAPI()
# Single shared client — do NOT create per-request (connection pool overhead)
_client = httpx.AsyncClient(base_url="http://127.0.0.1:11434")

async def _reverse_proxy(request: Request):
    url = httpx.URL(
        path=request.url.path,
        query=request.url.query.encode("utf-8"),
    )
    rp_req = _client.build_request(
        request.method,
        url,
        headers=request.headers.raw,   # forward all upstream headers verbatim
        content=request.stream(),       # stream request body, don't buffer
    )
    rp_resp = await _client.send(rp_req, stream=True)  # MUST be stream=True
    return StreamingResponse(
        rp_resp.aiter_raw(),            # zero-copy raw byte passthrough
        status_code=rp_resp.status_code,
        headers=rp_resp.headers,
        background=BackgroundTask(rp_resp.aclose),  # cleanup on disconnect
    )

app.add_route("/{path:path}", _reverse_proxy, ["GET", "POST", "DELETE"])
```

**Critical details:**
- `stream=True` on `client.send()` is mandatory — without it, httpx buffers the entire response before returning.
- `aiter_raw()` yields raw bytes (no decoding). Use `aiter_bytes()` for the same but with response decoding applied. For NDJSON passthrough, `aiter_raw()` is correct.
- `BackgroundTask(rp_resp.aclose)` ensures the upstream connection is closed after the client disconnects (prevents connection leak).
- The shared `httpx.AsyncClient` instance maintains a connection pool to Ollama. Do not instantiate it inside the handler.

### Pattern for chunk-level inspection (MITM mode)

When you need to read/modify each chunk before forwarding:

```python
async def _mitm_proxy(request: Request):
    url = httpx.URL(path=request.url.path, query=request.url.query.encode())
    rp_req = _client.build_request(
        request.method, url,
        headers=request.headers.raw,
        content=request.stream(),
    )
    rp_resp = await _client.send(rp_req, stream=True)

    async def inspected_stream():
        buffer = b""
        async for raw_chunk in rp_resp.aiter_raw():
            buffer += raw_chunk
            # Process complete newline-delimited JSON objects
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                    # --- intercept here: log, modify, enrich ---
                    yield json.dumps(obj).encode() + b"\n"
                except json.JSONDecodeError:
                    yield line + b"\n"  # passthrough malformed
        if buffer.strip():
            yield buffer  # flush remaining

    return StreamingResponse(
        inspected_stream(),
        status_code=rp_resp.status_code,
        headers=rp_resp.headers,
        background=BackgroundTask(rp_resp.aclose),
    )
```

**Warning:** This pattern adds per-chunk Python overhead. For pure passthrough, use `aiter_raw()` directly.

### Header filtering

Certain hop-by-hop headers must be stripped before forwarding to avoid protocol errors:

```python
HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding",
    "te", "trailer", "upgrade", "proxy-authorization", "proxy-authenticate",
}

def filter_headers(headers: httpx.Headers) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}
```

Pass `filter_headers(rp_resp.headers)` as the `headers=` argument to `StreamingResponse`.

### Existing open-source references

| Project | URL | Approach |
|---|---|---|
| eyalrot/ollama_openai | https://github.com/eyalrot/ollama_openai | FastAPI transparent proxy, Ollama→OpenAI |
| ParisNeo/ollama_proxy_server | https://github.com/ParisNeo/ollama_proxy_server | Multi-instance load balancer, streaming |
| kirel/ollama-proxy | https://github.com/kirel/ollama-proxy | Ollama→LiteLLM, streaming |
| Embedded-Nature/ollama-proxy | https://github.com/Embedded-Nature/ollama-proxy | Ollama→LM Studio translation |
| punnerud/cursor_ollama_proxy | https://github.com/punnerud/cursor_ollama_proxy | Cursor IDE→Ollama proxy |

---

## 3. iptables Transparent Redirect for Ollama

### The loop problem

The proxy listens on port X and connects to Ollama on 11434. If the proxy process itself triggers the iptables OUTPUT rule, it will be redirected back to itself — infinite loop. The solution is `--uid-owner` exclusion.

### Approach A: REDIRECT (NAT table) — simpler, works for local traffic

```bash
# Run your proxy as user "mitm" (uid e.g. 1001)
# Redirect all TCP :11434 connections EXCEPT from the proxy user
iptables -t nat -A OUTPUT \
  -p tcp \
  --dport 11434 \
  -m owner ! --uid-owner mitm \
  -j REDIRECT --to-port 8080

# To also intercept traffic arriving on lo from other processes:
# (usually not needed if Ollama only binds to 127.0.0.1)
iptables -t nat -A PREROUTING \
  -p tcp \
  --dport 11434 \
  -j REDIRECT --to-port 8080
```

**How the proxy then connects to real Ollama:**
- The proxy process (uid=mitm) connects to 127.0.0.1:11434.
- Because it runs as uid=mitm, its outbound connection is excluded from the OUTPUT rule.
- It reaches the real Ollama directly.

### Approach B: TPROXY (mangle table) — more correct, avoids NAT artifacts

REDIRECT rewrites the destination in the packet — the proxy sees 127.0.0.1:8080 as the destination, losing the original address. TPROXY preserves the original destination (needed if your proxy cares about which host was targeted, e.g. for multi-upstream routing).

```bash
# Routing setup (run once)
ip rule add fwmark 1 table 100
ip route add local 0.0.0.0/0 dev lo table 100

# mangle PREROUTING — intercept incoming connections to :11434
iptables -t mangle -N OLLAMA_MITM
iptables -t mangle -A OLLAMA_MITM -p tcp --dport 11434 \
  -j TPROXY --on-port 8080 --tproxy-mark 1
iptables -t mangle -A PREROUTING -j OLLAMA_MITM

# mangle OUTPUT — intercept locally-originated connections, skip proxy uid
iptables -t mangle -N OLLAMA_MITM_OUT
iptables -t mangle -A OLLAMA_MITM_OUT \
  -m owner --uid-owner mitm -j RETURN   # skip proxy itself
iptables -t mangle -A OLLAMA_MITM_OUT \
  -p tcp --dport 11434 -j MARK --set-mark 1
iptables -t mangle -A OUTPUT -j OLLAMA_MITM_OUT
```

The proxy must use `IP_TRANSPARENT` socket option to receive TPROXY-redirected connections:
```python
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_IP, socket.IP_TRANSPARENT, 1)
sock.bind(("0.0.0.0", 8080))
```

**Recommendation:** For a same-host MITM where you only need to intercept localhost:11434, use **REDIRECT** (Approach A). It is simpler, requires no `IP_TRANSPARENT`, and works correctly because all traffic is local. TPROXY is overkill unless you need to preserve the original destination IP.

### GID-based exclusion (alternative to UID)

If the proxy needs to run as root but still needs loop prevention, use GID:
```bash
# Create a dedicated group
groupadd mitm-proxy
# Run proxy as member of that group
iptables -t nat -A OUTPUT \
  -p tcp --dport 11434 \
  -m owner ! --gid-owner mitm-proxy \
  -j REDIRECT --to-port 8080
```

### Persistence

These rules are lost on reboot. Persist with:
```bash
iptables-save > /etc/iptables/rules.v4
# Restore on boot via iptables-restore or the iptables-persistent package
```

---

## 4. nftables vs iptables on Modern Linux

**Recommendation: use iptables for this project.**

Rationale:
- Ubuntu 24.04 ships `iptables` backed by `nftables` kernel backend via `iptables-nft` shim. The commands are iptables syntax but run on nftables kernel. No conflict.
- The `--uid-owner` / `--gid-owner` owner match module has direct nftables equivalents (`meta skuid`, `meta skgid`) but documentation and examples are sparse.
- If you want native nftables syntax, the equivalent is:

```nft
table ip nat {
    chain output {
        type nat hook output priority -100;
        tcp dport 11434 meta skuid != mitm redirect to :8080
    }
}
```

Load with: `nft -f /etc/nftables.conf`

- The `meta skuid` match works only in the OUTPUT chain (locally-generated traffic) — same constraint as `--uid-owner`.
- For TPROXY with nftables, use `meta mark` + `tproxy to :8080` in the PREROUTING chain (requires kernel 4.18+, available on Ubuntu 24.04).

**Bottom line:** iptables syntax is better-documented for this exact use case. Stick with it unless you have a specific reason to migrate.

---

## 5. Full MITM Setup — Recommended Architecture

```
Client process (any uid)
    │
    │ connect to 127.0.0.1:11434
    ▼
[iptables OUTPUT REDIRECT] ──────────────────────────────────────────────
    │ (all uids except mitm-proxy)
    ▼
MITM Proxy (FastAPI, port 8080, uid=mitm-proxy)
    │  - Receives connection
    │  - Optionally inspects/modifies request
    │  - Connects to 127.0.0.1:11434 as uid=mitm-proxy (excluded from rule)
    ▼
Real Ollama (127.0.0.1:11434)
    │
    │ Streaming NDJSON response
    ▼
MITM Proxy
    │  - Optionally inspects chunks (parse \n-delimited JSON)
    │  - Yields chunks via StreamingResponse / aiter_raw()
    ▼
Client (receives response as if from Ollama directly)
```

### iptables rules (production-ready)
```bash
# Exclude the proxy user from redirect (prevents loop)
iptables -t nat -A OUTPUT \
  -p tcp \
  --dport 11434 \
  -m owner ! --uid-owner mitm-proxy \
  -j REDIRECT --to-port 8080

# Persist
iptables-save > /etc/iptables/rules.v4
```

### FastAPI proxy (production-ready skeleton)
```python
import json
import httpx
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse
from starlette.background import BackgroundTask

OLLAMA_BASE = "http://127.0.0.1:11434"
PROXY_PORT = 8080

HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding",
    "te", "trailer", "upgrade",
}

app = FastAPI()
_client = httpx.AsyncClient(base_url=OLLAMA_BASE, timeout=None)

async def _proxy_handler(request: Request):
    url = httpx.URL(path=request.url.path, query=request.url.query.encode())
    upstream_headers = [
        (k, v) for k, v in request.headers.raw
        if k.decode().lower() not in HOP_BY_HOP
    ]
    rp_req = _client.build_request(
        request.method, url,
        headers=upstream_headers,
        content=request.stream(),
    )
    rp_resp = await _client.send(rp_req, stream=True)
    resp_headers = {
        k: v for k, v in rp_resp.headers.items()
        if k.lower() not in HOP_BY_HOP
    }
    return StreamingResponse(
        rp_resp.aiter_raw(),
        status_code=rp_resp.status_code,
        headers=resp_headers,
        background=BackgroundTask(rp_resp.aclose),
    )

app.add_route("/{path:path}", _proxy_handler, ["GET", "POST", "DELETE", "HEAD"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PROXY_PORT)
```

**Run as the excluded user:**
```bash
sudo -u mitm-proxy python mitm_proxy.py
# or via systemd with User=mitm-proxy
```

---

## 6. Open Questions / Risks

1. **Ollama TLS:** If Ollama is configured to use TLS (non-default), REDIRECT won't work transparently — you need a TLS-terminating proxy. For localhost-only setups this is not a concern.
2. **timeout=None:** Ollama model inference can take minutes. Set `httpx.AsyncClient(timeout=None)` or a very long timeout (600s+).
3. **Connection pool exhaustion:** Large numbers of concurrent streams may exhaust the httpx connection pool. Set `limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)`.
4. **Chunk boundaries:** `aiter_raw()` does not guarantee one NDJSON line per chunk. A single `yield` may contain partial lines or multiple lines. The inspection pattern above handles this with a `buffer`.
5. **Client disconnect handling:** If the client disconnects mid-stream, `rp_resp.aclose()` (via BackgroundTask) closes the upstream connection. Uvicorn handles the `asyncio.CancelledError` automatically, but test this explicitly.
6. **iptables rule ordering:** If another tool (Docker, ufw) manages iptables, rule ordering matters. Insert with `-I` (insert at top) rather than `-A` (append) if conflicts arise.

---

## Sources

- https://github.com/fastapi/fastapi/discussions/7382
- https://dasroot.net/posts/2026/03/async-streaming-responses-fastapi-comprehensive-guide/
- https://docs.mitmproxy.org/stable/howto/transparent/
- https://xtls.github.io/en/document/level-2/iptables_gid.html
- https://hev.cc/posts/2021/transparent-proxy-with-nftables/
- https://wiki.nftables.org/wiki-nftables/index.php/Performing_Network_Address_Translation_(NAT)
- https://docs.kernel.org/networking/tproxy.html
- https://raw.githubusercontent.com/ollama/ollama/main/docs/api.md
- https://github.com/eyalrot/ollama_openai
- https://github.com/ParisNeo/ollama_proxy_server
- https://github.com/kirel/ollama-proxy
- https://github.com/Embedded-Nature/ollama-proxy
