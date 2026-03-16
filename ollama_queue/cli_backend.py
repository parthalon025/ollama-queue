"""CLI subcommand group for backend agent management."""

import urllib.parse

import click
import httpx

DEFAULT_QUEUE = "http://127.0.0.1:7683"


@click.group("backend")
@click.option("--queue-url", default=DEFAULT_QUEUE, envvar="QUEUE_URL", help="Queue server URL")
@click.pass_context
def backend(ctx, queue_url):
    """Manage backend agents."""
    ctx.ensure_object(dict)
    ctx.obj["queue_url"] = queue_url.rstrip("/")


@backend.command("status")
@click.argument("url", required=False)
@click.pass_context
def backend_status(ctx, url):
    """Show backend agent status (all or specific)."""
    queue_url = ctx.obj["queue_url"]
    resp = httpx.get(f"{queue_url}/api/backends", timeout=10.0)
    backends = resp.json()
    if url:
        backends = [b for b in backends if b["url"].rstrip("/") == url.rstrip("/")]
        if not backends:
            click.echo(f"Backend {url} not found.")
            ctx.exit(1)
            return
    for b in backends:
        healthy = "OK" if b.get("healthy") else "DOWN"
        gpu = b.get("gpu_name") or "unknown"
        vram = b.get("vram_pct", 0)
        click.echo(f"  {b['url']}  [{healthy}]  GPU: {gpu}  VRAM: {vram:.0f}%")


def _dispatch_command(queue_url: str, backend_url: str, action: str):
    """Send a command to a specific backend via the queue."""
    encoded = urllib.parse.quote(backend_url, safe="")
    resp = httpx.post(
        f"{queue_url}/api/backends/{encoded}/command",
        json={"action": action},
        timeout=60.0,
    )
    if resp.status_code != 200:
        click.echo(f"Error: {resp.json().get('detail', resp.text)}")
        return
    click.echo(f"  {backend_url}: {resp.json()}")


def _dispatch_to_all_or_one(ctx, action, url):
    """Dispatch a command to one backend or all."""
    queue_url = ctx.obj["queue_url"]
    if url:
        _dispatch_command(queue_url, url, action)
    else:
        resp = httpx.get(f"{queue_url}/api/backends", timeout=10.0)
        for b in resp.json():
            _dispatch_command(queue_url, b["url"], action)


@backend.command("sync-models")
@click.argument("url", required=False)
@click.pass_context
def backend_sync_models(ctx, url):
    """Trigger model sync on backend(s)."""
    _dispatch_to_all_or_one(ctx, "sync-models", url)


@backend.command("update-ollama")
@click.argument("url", required=False)
@click.pass_context
def backend_update_ollama(ctx, url):
    """Update Ollama on backend(s)."""
    _dispatch_to_all_or_one(ctx, "update-ollama", url)
