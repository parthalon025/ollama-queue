"""API subpackage — assembles all route modules into the FastAPI app.

Each route module accesses db via:
    import ollama_queue.api as _api
    # then in each handler: db = _api.db
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ollama_queue.db import Database

# Module-level db reference, set by register_routes() at startup.
# Each route module accesses this via ``import ollama_queue.api as _api; _api.db``.
db: Database | None = None

# Re-export create_app for backward compatibility (tests, cli, etc.)
# Re-export _call_generate_description so tests can import it from ollama_queue.api
from ollama_queue.api.schedule import (
    _call_generate_description as _call_generate_description,
)
from ollama_queue.app import create_app as create_app


def register_routes(app, db_instance: Database) -> None:
    """Set the module db reference and include all route routers."""
    global db
    db = db_instance

    # Import route modules (each defines a ``router`` APIRouter)
    from ollama_queue.api import (
        consumers,
        dlq,
        eval_runs,
        eval_settings,
        eval_trends,
        eval_variants,
        health,
        jobs,
        models,
        proxy,
        schedule,
        settings,
    )

    app.include_router(jobs.router)
    app.include_router(health.router)
    app.include_router(settings.router)
    app.include_router(proxy.router)
    app.include_router(schedule.router)
    app.include_router(dlq.router)
    app.include_router(models.router)
    app.include_router(consumers.router)
    # Eval: order matters — fixed paths before parameterized
    app.include_router(eval_variants.router)
    app.include_router(eval_runs.router)
    app.include_router(eval_settings.router)
    app.include_router(eval_trends.router)
