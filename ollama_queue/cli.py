"""Click CLI entry point for ollama-queue."""

import os
import time

import click

from ollama_queue.db import Database

DEFAULT_DB = os.path.expanduser("~/.local/share/ollama-queue/queue.db")


@click.group()
@click.option("--db", default=DEFAULT_DB, help="Database path")
@click.pass_context
def main(ctx, db):
    """Ollama job queue scheduler."""
    os.makedirs(os.path.dirname(db), exist_ok=True)
    ctx.ensure_object(dict)
    database = Database(db)
    database.initialize()
    ctx.obj["db"] = database


@main.command()
@click.option("--source", required=True, help="Job source identifier")
@click.option("--model", default=None, help="Ollama model name")
@click.option("--priority", default=None, type=int, help="Priority (1=highest)")
@click.option("--timeout", default=None, type=int, help="Timeout in seconds")
@click.argument("command", nargs=-1, required=True)
@click.pass_context
def submit(ctx, source, model, priority, timeout, command):
    """Submit a job to the queue."""
    db = ctx.obj["db"]
    cmd_str = " ".join(command)
    settings = db.get_all_settings()
    p = priority if priority is not None else settings.get("default_priority", 5)
    t = timeout if timeout is not None else settings.get("default_timeout_seconds", 600)
    job_id = db.submit_job(cmd_str, model, p, t, source)
    click.echo(f"Job #{job_id} queued (priority={p}, timeout={t}s, source={source})")


@main.command()
@click.pass_context
def status(ctx):
    """Show daemon state, current job, and queue depth."""
    db = ctx.obj["db"]
    state = db.get_daemon_state()
    pending = db.get_pending_jobs()

    if state is None:
        click.echo("Daemon state: unknown (no state row)")
        return

    click.echo(f"Daemon state: {state['state']}")

    if state.get("current_job_id"):
        job = db.get_job(state["current_job_id"])
        if job:
            click.echo(f"Current job: #{job['id']} ({job['source']}) - {job['command']}")
        else:
            click.echo(f"Current job: #{state['current_job_id']} (not found)")

    if state.get("paused_reason"):
        click.echo(f"Paused reason: {state['paused_reason']}")

    click.echo(f"Queue depth: {len(pending)}")
    click.echo(f"Completed today: {state.get('jobs_completed_today', 0)}")
    click.echo(f"Failed today: {state.get('jobs_failed_today', 0)}")


@main.command()
@click.pass_context
def queue(ctx):
    """List pending jobs with priority order."""
    db = ctx.obj["db"]
    pending = db.get_pending_jobs()

    if not pending:
        click.echo("Queue is empty.")
        return

    click.echo(f"{'ID':>5}  {'Pri':>3}  {'Source':<15}  {'Model':<20}  Command")
    click.echo("-" * 75)
    for job in pending:
        click.echo(
            f"{job['id']:>5}  {job['priority']:>3}  {(job['source'] or '')::<15}  "
            f"{(job['model'] or '')::<20}  {job['command']}"
        )


@main.command()
@click.option("--all", "show_all", is_flag=True, help="Show all jobs including pending/running")
@click.option("--source", default=None, help="Filter by source")
@click.pass_context
def history(ctx, show_all, source):
    """List completed/failed jobs."""
    db = ctx.obj["db"]

    if show_all:
        # Show all jobs regardless of status
        conn = db._connect()
        if source:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE source = ? ORDER BY submitted_at DESC LIMIT 50",
                (source,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY submitted_at DESC LIMIT 50"
            ).fetchall()
        jobs = [dict(r) for r in rows]
    else:
        jobs = db.get_history(limit=50, source=source)

    if not jobs:
        click.echo("No jobs found.")
        return

    click.echo(f"{'ID':>5}  {'Status':<10}  {'Source':<15}  {'Exit':>4}  Command")
    click.echo("-" * 70)
    for job in jobs:
        exit_str = str(job.get("exit_code", "")) if job.get("exit_code") is not None else "-"
        click.echo(
            f"{job['id']:>5}  {job['status']:<10}  {(job['source'] or ''):<15}  "
            f"{exit_str:>4}  {job['command']}"
        )


@main.command()
@click.pass_context
def pause(ctx):
    """Pause the daemon (manual pause)."""
    db = ctx.obj["db"]
    db.update_daemon_state(state="paused_manual", paused_reason="manual", paused_since=time.time())
    click.echo("Daemon paused.")


@main.command()
@click.pass_context
def resume(ctx):
    """Resume the daemon from paused state."""
    db = ctx.obj["db"]
    db.update_daemon_state(state="idle", paused_reason=None, paused_since=None)
    click.echo("Daemon resumed.")


@main.command()
@click.argument("job_id", type=int)
@click.pass_context
def cancel(ctx, job_id):
    """Cancel a pending job by ID."""
    db = ctx.obj["db"]
    job = db.get_job(job_id)

    if job is None:
        click.echo(f"Job #{job_id} not found.")
        ctx.exit(1)
        return

    if job["status"] != "pending":
        click.echo(f"Job #{job_id} is {job['status']}, can only cancel pending jobs.")
        ctx.exit(1)
        return

    db.cancel_job(job_id)
    click.echo(f"Job #{job_id} cancelled.")


@main.command()
@click.option("--port", default=7683, type=int, help="Port for FastAPI server")
@click.pass_context
def serve(ctx, port):
    """Start the daemon and FastAPI server."""
    import threading

    import uvicorn

    from ollama_queue.api import create_app
    from ollama_queue.daemon import Daemon

    db = ctx.obj["db"]
    daemon = Daemon(db)

    # Start daemon polling in background thread
    daemon_thread = threading.Thread(target=daemon.run, daemon=True)
    daemon_thread.start()

    # Start FastAPI (blocks until shutdown)
    app = create_app(db)
    click.echo(f"Starting ollama-queue on port {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
