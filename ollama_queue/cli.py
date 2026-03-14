"""Click CLI entry point for ollama-queue."""

import logging
import os
import shlex
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
    cmd_str = " ".join(shlex.quote(tok) for tok in command)

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
            f"{job['id']:>5}  {job['priority']:>3}  {(job['source'] or ''):<15}  "
            f"{(job['model'] or ''):<20}  {job['command']}"
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
            rows = conn.execute("SELECT * FROM jobs ORDER BY submitted_at DESC LIMIT 50").fetchall()
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
        click.echo(f"{job['id']:>5}  {job['status']:<10}  {(job['source'] or ''):<15}  {exit_str:>4}  {job['command']}")


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
@click.option("--host", default="127.0.0.1", help="Host to bind (use 0.0.0.0 for all interfaces)")
@click.option("--debug", is_flag=True, default=False, help="Enable DEBUG logging for all ollama_queue modules")
@click.pass_context
def serve(ctx, port, host, debug):
    """Start the daemon and FastAPI server."""
    import threading

    import uvicorn

    # Scope INFO (or DEBUG with --debug) to our package only — root stays at
    # WARNING so third-party libraries don't flood the journal.
    # Lesson #246: set package logger level, not root logger level.
    _level = logging.DEBUG if debug else logging.INFO
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(_handler)
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("ollama_queue").setLevel(_level)

    from ollama_queue.app import create_app
    from ollama_queue.daemon import Daemon

    db = ctx.obj["db"]
    daemon = Daemon(db)

    # Start daemon polling in background thread
    daemon_thread = threading.Thread(target=daemon.run, daemon=True)
    daemon_thread.start()

    # Start FastAPI (blocks until shutdown)
    app = create_app(db)
    click.echo(f"Starting ollama-queue on port {port}...")
    uvicorn.run(app, host=host, port=port, log_level="warning")


def _parse_interval(interval_str: str) -> int:
    """Parse interval string like 6h, 30m, 90s, 1d → seconds."""
    unit_map = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if interval_str[-1] in unit_map:
        return int(interval_str[:-1]) * unit_map[interval_str[-1]]
    return int(interval_str)  # assume seconds


def _auto_suggest_slot(db, priority: int) -> tuple[str, float]:
    """Use Scheduler.suggest_time to pick the best available cron slot."""
    from ollama_queue.scheduling.scheduler import Scheduler

    suggestions = Scheduler(db).suggest_time(priority=priority, top_n=1)
    if not suggestions:
        raise click.UsageError("No available time slots (all blocked by pinned jobs).")
    return suggestions[0]


def _parse_schedule_spec(
    interval: str | None,
    at: str | None,
    cron: str | None,
    days: str | None,
    priority: int = 5,
    db=None,
) -> tuple[int | None, str | None, float | None]:
    """Parse mutually exclusive schedule options.

    Returns (interval_seconds, cron_expression, auto_score).
    auto_score is non-None only when --at auto is used.
    """
    from croniter import croniter as _croniter

    given = sum(x is not None for x in [interval, at, cron])
    if given == 0:
        raise click.UsageError("One of --interval, --at, or --cron is required.")
    if given > 1:
        raise click.UsageError("--interval, --at, and --cron are mutually exclusive.")
    if days is not None and at is None:
        raise click.UsageError("--days is only valid with --at.")

    if interval is not None:
        return _parse_interval(interval), None, None

    if at == "auto":
        if db is None:
            raise click.UsageError("--at auto requires a database context.")
        cron_expr, score = _auto_suggest_slot(db, priority)
        return None, cron_expr, score

    if at is not None:
        try:
            h, m = (int(p) for p in at.split(":"))
        except (ValueError, AttributeError) as err:
            raise click.UsageError(f"--at must be HH:MM or 'auto', got: {at!r}") from err
        dow = days if days is not None else "*"
        cron_expr = f"{m} {h} * * {dow}"
    else:
        cron_expr = cron or ""

    try:
        _croniter(cron_expr)
    except Exception as exc:
        raise click.UsageError(f"Invalid cron expression {cron_expr!r}: {exc}") from exc

    return None, cron_expr, None


@main.group()
def schedule():
    """Manage recurring scheduled jobs."""
    pass


@schedule.command("add")
@click.option("--name", required=True, help="Unique job name")
@click.option("--interval", default=None, help="Interval: 6h, 30m, 90s, 1d")
@click.option("--at", default=None, metavar="HH:MM|auto", help="Time of day (HH:MM) or 'auto' for best slot")
@click.option("--days", default=None, metavar="DOW", help="Days of week for --at (e.g. 1-5 or mon-fri)")
@click.option("--cron", default=None, metavar="EXPR", help="5-field cron expression (e.g. '0 7 * * 1-5')")
@click.option("--model", default=None)
@click.option("--priority", default=5, type=int)
@click.option("--timeout", default=600, type=int)
@click.option("--tag", default=None)
@click.option("--source", default=None)
@click.option("--max-retries", default=0, type=int)
@click.option("--profile", default="ollama", help="Resource profile: ollama|any")
@click.option("--pin", is_flag=True, default=False, help="Pin this job's time slot (cron jobs only)")
@click.option(
    "--check-command",
    "check_command",
    default=None,
    help="Shell command to run before job; exit 0=run, 1=skip, 2=disable",
)
@click.option("--max-runs", "max_runs", default=None, type=int, help="Auto-disable after N successful completions")
@click.argument("command", nargs=-1, required=True)
@click.pass_context
def schedule_add(
    ctx,
    name,
    interval,
    at,
    days,
    cron,
    model,
    priority,
    timeout,
    tag,
    source,
    max_retries,
    profile,
    pin,
    check_command,
    max_runs,
    command,
):
    db = ctx.obj["db"]
    from ollama_queue.scheduling.scheduler import Scheduler

    interval_seconds, cron_expression, auto_score = _parse_schedule_spec(
        interval, at, cron, days, priority=priority, db=db
    )
    rj_id = db.add_recurring_job(
        name=name,
        command=" ".join(shlex.quote(tok) for tok in command),
        interval_seconds=interval_seconds,
        cron_expression=cron_expression,
        model=model,
        priority=priority,
        timeout=timeout,
        source=source or name,
        tag=tag,
        resource_profile=profile,
        max_retries=max_retries,
        pinned=pin,
        check_command=check_command,
        max_runs=max_runs,
    )
    if interval_seconds is not None:
        Scheduler(db).rebalance()
        schedule_str = f"interval={interval}"
    elif auto_score is not None:
        schedule_str = f"cron={cron_expression!r}"
        click.echo(f"Suggested {cron_expression} (load score={auto_score:.1f}) — placed.")
    else:
        schedule_str = f"cron={cron_expression!r}"
    pin_str = " ★ pinned" if pin else ""
    click.echo(f"Added recurring job '{name}' (id={rj_id}) — {schedule_str}{pin_str}.")


@schedule.command("list")
@click.pass_context
def schedule_list(ctx):
    db = ctx.obj["db"]
    import datetime

    jobs = db.list_recurring_jobs()
    if not jobs:
        click.echo("No recurring jobs.")
        return
    click.echo(f"{'NAME':<20} {'SCHEDULE':<18} {'PRIORITY':>8} {'TAG':<12} {'ENABLED':>7} {'NEXT RUN'}")
    click.echo("-" * 82)
    for rj in jobs:
        next_run = datetime.datetime.fromtimestamp(rj["next_run"]).strftime("%Y-%m-%d %H:%M") if rj["next_run"] else "—"
        cron_expr = rj.get("cron_expression")
        if cron_expr:
            schedule_str = cron_expr
        else:
            secs = rj["interval_seconds"] or 0
            if secs % 86400 == 0:
                schedule_str = f"every {secs // 86400}d"
            elif secs % 3600 == 0:
                schedule_str = f"every {secs // 3600}h"
            elif secs % 60 == 0:
                schedule_str = f"every {secs // 60}m"
            else:
                schedule_str = f"every {secs}s"
        enabled = "yes" if rj["enabled"] else "no"
        tag = rj.get("tag") or "—"
        pin_mark = "★ " if rj.get("pinned") else "  "
        click.echo(
            f"{pin_mark}{rj['name']:<20} {schedule_str:<18} {rj['priority']:>8} {tag:<12} {enabled:>7}  {next_run}"
        )


@schedule.command("suggest")
@click.option("--priority", default=5, type=int, help="Priority for the hypothetical job (1=highest)")
@click.option("--top", default=3, type=int, help="Number of suggestions to show")
@click.pass_context
def schedule_suggest(ctx, priority, top):
    """Show optimal time slots for a new job at the given priority."""
    db = ctx.obj["db"]
    from ollama_queue.scheduling.scheduler import Scheduler

    suggestions = Scheduler(db).suggest_time(priority=priority, top_n=top)
    if not suggestions:
        click.echo("No available slots (all blocked by pinned jobs).")
        return
    click.echo(f"Top {len(suggestions)} suggested times for priority {priority}:")
    click.echo(f"{'TIME':<12} {'CRON':<18} SCORE")
    click.echo("-" * 40)
    for cron_expr, score in suggestions:
        parts = cron_expr.split()
        minute, hour = int(parts[0]), int(parts[1])
        time_str = f"{hour:02d}:{minute:02d}"
        click.echo(f"{time_str:<12} {cron_expr:<18} {score:.1f}")


@schedule.command("edit")
@click.argument("name")
@click.option("--priority", default=None, type=int, help="New priority (1=highest)")
@click.option("--interval", default=None, help="New interval: 6h, 30m, etc.")
@click.option("--command", "new_command", default=None, help="New command string")
@click.option("--pin/--no-pin", default=None, help="Pin or unpin this job's time slot")
@click.option("--check-command", "check_command", default=None, help="New check_command (empty string to clear)")
@click.option("--max-runs", "max_runs", default=None, type=int, help="New max_runs countdown")
@click.pass_context
def schedule_edit(ctx, name, priority, interval, new_command, pin, check_command, max_runs):
    """Edit a recurring job's fields."""
    db = ctx.obj["db"]
    rj = db.get_recurring_job_by_name(name)
    if rj is None:
        click.echo(f"Job '{name}' not found.", err=True)
        return
    fields: dict = {}
    if priority is not None:
        fields["priority"] = priority
    if interval is not None:
        fields["interval_seconds"] = _parse_interval(interval)
    if new_command is not None:
        fields["command"] = new_command
    if pin is not None:
        fields["pinned"] = 1 if pin else 0
    if check_command is not None:
        fields["check_command"] = check_command if check_command else None
    if max_runs is not None:
        fields["max_runs"] = max_runs
    if not fields:
        click.echo("Nothing to update — specify at least one option.")
        return
    db.update_recurring_job(rj["id"], **fields)
    schedule_fields = {"priority", "interval_seconds", "pinned"}
    if fields.keys() & schedule_fields:
        from ollama_queue.scheduling.scheduler import Scheduler

        Scheduler(db).rebalance()
    click.echo(f"Updated '{name}': {', '.join(f'{k}={v}' for k, v in fields.items())}")


@schedule.command("enable")
@click.argument("name")
@click.pass_context
def schedule_enable(ctx, name):
    db = ctx.obj["db"]
    if db.set_recurring_job_enabled(name, True):
        from ollama_queue.scheduling.scheduler import Scheduler

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
    from ollama_queue.scheduling.scheduler import Scheduler

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


@dlq.command("schedule-preview")
@click.pass_context
def dlq_schedule_preview(ctx):
    """Preview what the next DLQ sweep would reschedule."""
    db = ctx.obj["db"]
    entries = db.list_dlq(unscheduled_only=True)
    if not entries:
        click.echo("No unscheduled DLQ entries.")
        return
    from ollama_queue.sensing.system_snapshot import classify_failure

    _ct = db.get_setting("dlq.chronic_failure_threshold")
    chronic_threshold = int(_ct) if _ct is not None else 3
    eligible = []
    for e in entries:
        cat = classify_failure(e.get("failure_reason", ""), e.get("exit_code"))
        if cat == "permanent":
            continue
        if (e.get("auto_reschedule_count") or 0) >= chronic_threshold:
            continue
        eligible.append((e, cat))

    if not eligible:
        click.echo("No unscheduled DLQ entries eligible for auto-reschedule.")
        return

    for e, cat in eligible:
        click.echo(f"[{e['id']}] {e['command'][:40]} — {cat} ({e['failure_reason'][:40]})")
    click.echo(f"\n{len(eligible)} entries eligible for auto-reschedule.")


@dlq.command("reschedule")
@click.argument("dlq_id", type=int)
@click.pass_context
def dlq_reschedule(ctx, dlq_id):
    """Manually reschedule a DLQ entry as a new job."""
    import time

    db = ctx.obj["db"]
    entry = db.get_dlq_entry(dlq_id)
    if not entry:
        click.echo(f"DLQ entry {dlq_id} not found.", err=True)
        return
    if entry.get("rescheduled_job_id"):
        click.echo(
            f"DLQ #{dlq_id} already rescheduled as job #{entry['rescheduled_job_id']}.",
            err=True,
        )
        return
    new_job_id = db.submit_job(
        command=entry["command"],
        model=entry.get("model", ""),
        priority=entry.get("priority", 5),
        timeout=entry.get("timeout", 600),
        source=entry.get("source", "dlq-manual-reschedule"),
        tag=entry.get("tag"),
        resource_profile=entry.get("resource_profile", "ollama"),
    )
    db.update_dlq_reschedule(
        dlq_id,
        rescheduled_job_id=new_job_id,
        rescheduled_for=time.time(),
        reschedule_reasoning="manual CLI reschedule",
    )
    click.echo(f"DLQ #{dlq_id} → new job #{new_job_id}")


@main.command("defer")
@click.argument("job_id", type=int)
@click.option("--reason", default="manual", help="Deferral reason")
@click.pass_context
def defer_job(ctx, job_id, reason):
    """Defer a pending or queued job."""
    db = ctx.obj["db"]
    job = db.get_job(job_id)
    if not job:
        click.echo(f"Job {job_id} not found.", err=True)
        return
    if job["status"] not in ("pending", "queued"):
        click.echo(f"Cannot defer job in status '{job['status']}'.", err=True)
        return
    deferral_id = db.defer_job(job_id, reason=reason)
    click.echo(f"Job #{job_id} deferred (deferral #{deferral_id}, reason: {reason})")


@main.group()
def metrics():
    """View model performance metrics."""
    pass


@metrics.command("models")
@click.pass_context
def metrics_models(ctx):
    """Show per-model performance stats."""
    db = ctx.obj["db"]
    stats = db.get_model_stats()
    if not stats:
        click.echo("No metrics data yet — run some jobs first.")
        return
    click.echo(f"{'Model':<30} {'Runs':>5} {'tok/min':>8} {'Warmup':>8} {'Size':>6}")
    click.echo("-" * 62)
    for model, data in sorted(stats.items()):
        tok = f"{data['avg_tok_per_min']:.0f}" if data.get("avg_tok_per_min") else "—"
        warmup = f"{data['avg_warmup_s']:.1f}s" if data.get("avg_warmup_s") else "—"
        size = f"{data['model_size_gb']:.1f}GB" if data.get("model_size_gb") else "—"
        click.echo(f"{model:<30} {data['run_count']:>5} {tok:>8} {warmup:>8} {size:>6}")


@metrics.command("curve")
@click.pass_context
def metrics_curve(ctx):
    """Show fitted performance curve parameters."""
    from ollama_queue.models.performance_curve import PerformanceCurve

    db = ctx.obj["db"]
    stats = db.get_model_stats()
    curve = PerformanceCurve()
    points = [
        {
            "model_size_gb": s["model_size_gb"],
            "avg_tok_per_min": s["avg_tok_per_min"],
        }
        for s in stats.values()
        if s.get("model_size_gb") and s.get("avg_tok_per_min")
    ]
    if not points:
        click.echo("Not enough data to fit a curve — need at least 2 models with size + throughput data.")
        return
    curve.fit(points)
    data = curve.get_curve_data()
    if not data["fitted"]:
        click.echo("Could not fit curve — need more data points.")
        return
    click.echo(f"Performance curve fitted from {data['n_points']} models:")
    click.echo(f"  tok/min slope:     {data['tok_slope']:.4f}")
    click.echo(f"  tok/min intercept: {data['tok_intercept']:.4f}")
    click.echo(f"  residual std:      {data['tok_residual_std']:.4f}")
    if data.get("warmup_slope") is not None:
        click.echo(f"  warmup slope:      {data['warmup_slope']:.4f}")
        click.echo(f"  warmup intercept:  {data['warmup_intercept']:.4f}")
