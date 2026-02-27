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
@click.option("--dedup/--no-dedup", default=True, help="Skip if pending job from same source exists")
@click.option("--tag", default=None, help="Optional tag for grouping/filtering")
@click.option("--max-retries", default=0, type=int, help="Max automatic retries before DLQ")
@click.argument("command", nargs=-1, required=True)
@click.pass_context
def submit(ctx, source, model, priority, timeout, dedup, tag, max_retries, command):
    """Submit a job to the queue."""
    db = ctx.obj["db"]
    cmd_str = " ".join(command)

    # Dedup: skip if a pending job from the same source already exists
    if dedup:
        pending = db.get_pending_jobs()
        existing = [j for j in pending if j.get("source") == source]
        if existing:
            click.echo(f"Skipped: pending job #{existing[0]['id']} from source={source} already queued")
            return

    settings = db.get_all_settings()
    p = priority if priority is not None else settings.get("default_priority", 5)
    t = timeout if timeout is not None else settings.get("default_timeout_seconds", 600)
    job_id = db.submit_job(cmd_str, model, p, t, source, tag=tag, max_retries=max_retries)
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
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def _parse_interval(interval_str: str) -> int:
    """Parse interval string like 6h, 30m, 90s, 1d → seconds."""
    unit_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if interval_str[-1] in unit_map:
        return int(interval_str[:-1]) * unit_map[interval_str[-1]]
    return int(interval_str)  # assume seconds


@main.group()
def schedule():
    """Manage recurring scheduled jobs."""
    pass


@schedule.command("add")
@click.option("--name", required=True, help="Unique job name")
@click.option("--interval", required=True, help="Interval: 6h, 30m, 90s, 1d")
@click.option("--model", default=None)
@click.option("--priority", default=5, type=int)
@click.option("--timeout", default=600, type=int)
@click.option("--tag", default=None)
@click.option("--source", default=None)
@click.option("--max-retries", default=0, type=int)
@click.option("--profile", default="ollama", help="Resource profile: ollama|any")
@click.argument("command", nargs=-1, required=True)
@click.pass_context
def schedule_add(ctx, name, interval, model, priority, timeout, tag, source, max_retries, profile, command):
    db = ctx.obj["db"]
    from ollama_queue.scheduler import Scheduler
    interval_seconds = _parse_interval(interval)
    rj_id = db.add_recurring_job(
        name=name, command=" ".join(command), interval_seconds=interval_seconds,
        model=model, priority=priority, timeout=timeout, source=source or name,
        tag=tag, resource_profile=profile, max_retries=max_retries,
    )
    Scheduler(db).rebalance()
    click.echo(f"Added recurring job '{name}' (id={rj_id}) — interval={interval}, rebalanced.")


@schedule.command("list")
@click.pass_context
def schedule_list(ctx):
    db = ctx.obj["db"]
    import datetime
    jobs = db.list_recurring_jobs()
    if not jobs:
        click.echo("No recurring jobs.")
        return
    click.echo(f"{'NAME':<20} {'INTERVAL':>10} {'PRIORITY':>8} {'TAG':<12} {'ENABLED':>7} {'NEXT RUN'}")
    click.echo("-" * 75)
    for rj in jobs:
        next_run = datetime.datetime.fromtimestamp(rj["next_run"]).strftime("%Y-%m-%d %H:%M") if rj["next_run"] else "—"
        secs = rj["interval_seconds"]
        if secs % 86400 == 0:
            interval_str = f"{secs // 86400}d"
        elif secs % 3600 == 0:
            interval_str = f"{secs // 3600}h"
        elif secs % 60 == 0:
            interval_str = f"{secs // 60}m"
        else:
            interval_str = f"{secs}s"
        enabled = "yes" if rj["enabled"] else "no"
        click.echo(f"{rj['name']:<20} {interval_str:>10} {rj['priority']:>8} {rj.get('tag') or '—':<12} {enabled:>7}  {next_run}")


@schedule.command("enable")
@click.argument("name")
@click.pass_context
def schedule_enable(ctx, name):
    db = ctx.obj["db"]
    if db.set_recurring_job_enabled(name, True):
        from ollama_queue.scheduler import Scheduler
        Scheduler(db).rebalance()
        click.echo(f"Enabled '{name}' and rebalanced.")
    else:
        click.echo(f"Job '{name}' not found.", err=True)


@schedule.command("disable")
@click.argument("name")
@click.pass_context
def schedule_disable(ctx, name):
    db = ctx.obj["db"]
    if db.set_recurring_job_enabled(name, False):
        click.echo(f"Disabled '{name}'.")
    else:
        click.echo(f"Job '{name}' not found.", err=True)


@schedule.command("remove")
@click.argument("name")
@click.pass_context
def schedule_remove(ctx, name):
    db = ctx.obj["db"]
    if db.delete_recurring_job(name):
        click.echo(f"Removed '{name}'.")
    else:
        click.echo(f"Job '{name}' not found.", err=True)


@schedule.command("rebalance")
@click.pass_context
def schedule_rebalance(ctx):
    db = ctx.obj["db"]
    from ollama_queue.scheduler import Scheduler
    changes = Scheduler(db).rebalance()
    click.echo(f"Rebalanced {len(changes)} jobs.")
    for c in changes:
        click.echo(f"  {c['name']}: next_run shifted")


@main.group()
def dlq():
    """Manage the dead letter queue."""
    pass


@dlq.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include resolved entries")
@click.pass_context
def dlq_list(ctx, show_all):
    db = ctx.obj["db"]
    entries = db.list_dlq(include_resolved=show_all)
    if not entries:
        click.echo("DLQ is empty.")
        return
    for e in entries:
        click.echo(f"[{e['id']}] {e['command'][:50]} — {e['failure_reason']} (retries={e.get('retry_count', 0)})")


@dlq.command("retry")
@click.argument("dlq_id", type=int)
@click.pass_context
def dlq_retry(ctx, dlq_id):
    db = ctx.obj["db"]
    new_id = db.retry_dlq_entry(dlq_id)
    if new_id:
        click.echo(f"Retried DLQ entry {dlq_id} → new job #{new_id}")
    else:
        click.echo(f"DLQ entry {dlq_id} not found.", err=True)


@dlq.command("retry-all")
@click.pass_context
def dlq_retry_all(ctx):
    db = ctx.obj["db"]
    entries = db.list_dlq()
    count = 0
    for e in entries:
        if db.retry_dlq_entry(e["id"]):
            count += 1
    click.echo(f"Retried {count} DLQ entries.")


@dlq.command("dismiss")
@click.argument("dlq_id", type=int)
@click.pass_context
def dlq_dismiss(ctx, dlq_id):
    db = ctx.obj["db"]
    if db.dismiss_dlq_entry(dlq_id):
        click.echo(f"Dismissed DLQ entry {dlq_id}.")
    else:
        click.echo(f"DLQ entry {dlq_id} not found.", err=True)


@dlq.command("clear")
@click.pass_context
def dlq_clear(ctx):
    db = ctx.obj["db"]
    n = db.clear_dlq()
    click.echo(f"Cleared {n} resolved DLQ entries.")
