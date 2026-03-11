"""Eval variant and template CRUD endpoints."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse
from starlette.responses import Response

import ollama_queue.api as _api
from ollama_queue.eval.validation import validate_provider, validate_variant_params

_log = logging.getLogger(__name__)

router = APIRouter()


def _get_eval_variant(conn, variant_id: str) -> dict:
    """Fetch a single eval_variant row; raise 404 if missing."""
    row = conn.execute("SELECT * FROM eval_variants WHERE id = ?", (variant_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Variant '{variant_id}' not found")
    return dict(row)


def _get_eval_template(conn, template_id: str) -> dict:
    """Fetch a single eval_prompt_templates row; raise 404 if missing."""
    row = conn.execute("SELECT * FROM eval_prompt_templates WHERE id = ?", (template_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found")
    return dict(row)


# --- Eval: Variants ---
# NOTE: fixed-path routes (/generate, /generate/preview, /export, /import)
# must come before parameterized routes (/{variant_id}) to avoid shadowing.


@router.get("/api/eval/variants")
def list_eval_variants():
    """Returns all eval_variants rows with latest quality score from the most recent complete run.

    Uses AUC for bayesian/tournament runs, F1 for legacy runs. The key is always
    ``latest_f1`` for backward compatibility with existing SPA consumers.
    """
    db = _api.db
    with db._lock:
        conn = db._connect()
        variants = [dict(r) for r in conn.execute("SELECT * FROM eval_variants ORDER BY created_at").fetchall()]
        # Compute latest quality per variant from eval_runs.metrics (JSON column)
        runs = conn.execute(
            "SELECT metrics, judge_mode FROM eval_runs WHERE status = 'complete' ORDER BY id ASC"
        ).fetchall()
    latest_f1: dict[str, float | None] = {}
    for run_row in runs:
        if not run_row["metrics"]:
            continue
        try:
            metrics = json.loads(run_row["metrics"])
        except (ValueError, TypeError):
            continue
        is_bayesian = run_row["judge_mode"] in ("bayesian", "tournament")
        for var_id, var_metrics in metrics.items():
            if not isinstance(var_metrics, dict):
                continue
            # Use AUC for bayesian/tournament runs, F1 for legacy
            quality = var_metrics.get("auc") if is_bayesian else var_metrics.get("f1")
            if quality is not None:
                latest_f1[var_id] = quality
    for v in variants:
        v["latest_f1"] = latest_f1.get(v["id"])
    return variants


@router.get("/api/eval/variants/generate/preview")
def preview_eval_variants_generate(models: str = "", template_id: str | None = None):
    """Returns proposed variant labels and count WITHOUT creating anything.

    What it shows: What would be bulk-created if the user triggers /generate.
    Decision it drives: Lets the user confirm the count and names before committing.
    """
    model_list = [m.strip() for m in models.split(",") if m.strip()]
    tmpl_id = template_id or "zero-shot-causal"
    names = [f"Auto: {m} ({tmpl_id})" for m in model_list]
    return {"would_create": len(names), "names": names}


@router.get("/api/eval/variants/export")
def export_eval_variants():
    """Returns all user (is_system=0) variants and their templates as JSON.

    What it shows: Portable variant config for backup or cross-machine transfer.
    Decision it drives: Enables cloning a tuned variant set to another setup.
    """
    import datetime as _dt

    db = _api.db
    with db._lock:
        conn = db._connect()
        variants = [dict(r) for r in conn.execute("SELECT * FROM eval_variants WHERE is_system = 0").fetchall()]
        # Collect only the templates referenced by user variants
        tmpl_ids = {v["prompt_template_id"] for v in variants}
        templates = []
        if tmpl_ids:
            placeholders = ",".join("?" * len(tmpl_ids))
            templates = [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM eval_prompt_templates WHERE id IN ({placeholders})",
                    list(tmpl_ids),
                ).fetchall()
            ]
    return JSONResponse(
        content={
            "variants": variants,
            "templates": templates,
            "exported_at": _dt.datetime.now(_dt.UTC).isoformat(),
        }
    )


@router.post("/api/eval/variants/import")
def import_eval_variants(body: dict = Body(...)):
    """Bulk-import variants and templates (non-destructive, skips existing IDs).

    What it shows: N/A — write-only endpoint.
    Decision it drives: Enables restoring or copying variant configs without manual re-entry.
    """
    import datetime as _dt

    db = _api.db
    variants = body.get("variants", [])
    templates = body.get("templates", [])
    now = _dt.datetime.now(_dt.UTC).isoformat()
    variants_imported = 0
    templates_imported = 0
    with db._lock:
        conn = db._connect()
        for tmpl in templates:
            cur = conn.execute(
                """INSERT OR IGNORE INTO eval_prompt_templates
                   (id, label, instruction, format_spec, examples, is_chunked, is_system, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tmpl.get("id"),
                    tmpl.get("label"),
                    tmpl.get("instruction"),
                    tmpl.get("format_spec"),
                    tmpl.get("examples"),
                    tmpl.get("is_chunked", 0),
                    0,  # imported = user-owned
                    tmpl.get("created_at") or now,
                ),
            )
            templates_imported += cur.rowcount
        for var in variants:
            cur = conn.execute(
                """INSERT OR IGNORE INTO eval_variants
                   (id, label, prompt_template_id, model, temperature, num_ctx,
                    is_recommended, is_system, created_at,
                    params, system_prompt, training_config, provider)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    var.get("id"),
                    var.get("label"),
                    var.get("prompt_template_id"),
                    var.get("model"),
                    var.get("temperature", 0.6),
                    var.get("num_ctx", 8192),
                    var.get("is_recommended", 0),
                    0,  # imported = user-owned
                    var.get("created_at") or now,
                    var.get("params"),
                    var.get("system_prompt"),
                    var.get("training_config"),
                    var.get("provider"),
                ),
            )
            variants_imported += cur.rowcount
        conn.commit()
    return {"variants_imported": variants_imported, "templates_imported": templates_imported}


@router.post("/api/eval/variants/generate")
def generate_eval_variants(body: dict = Body(...)):
    """Bulk-create one user variant per model in the provided list.

    What it shows: N/A — write-only; created variants appear in GET /api/eval/variants.
    Decision it drives: Lets the user quickly populate variant configs for all installed models.
    """
    import datetime as _dt
    import uuid

    db = _api.db
    models_list = body.get("models", [])
    tmpl_id = body.get("template_id") or "zero-shot-causal"
    if not models_list:
        raise HTTPException(status_code=400, detail="models list is required")

    try:
        params_json = validate_variant_params(body.get("params"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        provider_str = validate_provider(body.get("provider"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    system_prompt = body.get("system_prompt")
    training_config = body.get("training_config")

    now = _dt.datetime.now(_dt.UTC).isoformat()
    created = []
    with db._lock:
        conn = db._connect()
        # Validate template exists
        _get_eval_template(conn, tmpl_id)
        for model_name in models_list:
            new_id = str(uuid.uuid4())[:8]
            label = f"Auto: {model_name} ({tmpl_id})"
            conn.execute(
                """INSERT INTO eval_variants
                   (id, label, prompt_template_id, model, temperature, num_ctx,
                    is_recommended, is_system, created_at,
                    params, system_prompt, training_config, provider)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id,
                    label,
                    tmpl_id,
                    model_name,
                    0.6,
                    8192,
                    0,
                    0,
                    now,
                    params_json,
                    system_prompt,
                    training_config,
                    provider_str,
                ),
            )
            created.append(
                {
                    "id": new_id,
                    "label": label,
                    "prompt_template_id": tmpl_id,
                    "model": model_name,
                    "temperature": 0.6,
                    "num_ctx": 8192,
                    "is_recommended": 0,
                    "is_system": 0,
                    "created_at": now,
                    "params": params_json,
                    "system_prompt": system_prompt,
                    "training_config": training_config,
                    "provider": provider_str,
                }
            )
        conn.commit()
    return {"created": len(created), "variants": created}


@router.post("/api/eval/variants")
def create_eval_variant(body: dict = Body(...)):
    """Create a new user eval variant.

    What it shows: N/A — write-only; created variant appears in GET /api/eval/variants.
    Decision it drives: Lets the user test a custom model x template x parameter combination.
    """
    import datetime as _dt
    import uuid

    db = _api.db
    label = body.get("label")
    tmpl_id = body.get("prompt_template_id")
    model = body.get("model")
    if not label or not tmpl_id or not model:
        raise HTTPException(status_code=400, detail="label, prompt_template_id, and model are required")

    try:
        params_json = validate_variant_params(body.get("params"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        provider_str = validate_provider(body.get("provider"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    system_prompt = body.get("system_prompt")
    training_config = body.get("training_config")

    now = _dt.datetime.now(_dt.UTC).isoformat()
    new_id = str(uuid.uuid4())[:8]
    with db._lock:
        conn = db._connect()
        _get_eval_template(conn, tmpl_id)
        conn.execute(
            """INSERT INTO eval_variants
               (id, label, prompt_template_id, model, temperature, num_ctx,
                is_recommended, is_system, created_at,
                params, system_prompt, training_config, provider)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id,
                label,
                tmpl_id,
                model,
                body.get("temperature", 0.6),
                body.get("num_ctx", 8192),
                1 if body.get("is_recommended") else 0,
                0,  # user-created
                now,
                params_json,
                system_prompt,
                training_config,
                provider_str,
            ),
        )
        conn.commit()
        row = _get_eval_variant(conn, new_id)
    return JSONResponse(content=row, status_code=201)


@router.get("/api/eval/variants/stability")
def get_variant_stability(data_source: str | None = None):
    """Compute cross-run F1 stability per variant (live query).

    # What it shows: Mean F1, standard deviation, and stable/unstable badge per variant
    #   across the last 20 completed runs.
    # Decision it drives: Identifies variants with inconsistent performance across runs,
    #   signaling unreliable configs that may need more data or different prompts.
    """
    from ollama_queue.eval.analysis import compute_variant_stability

    db = _api.db
    with db._lock:
        conn = db._connect()
        if data_source:
            rows = conn.execute(
                "SELECT metrics FROM eval_runs WHERE status = 'complete' AND data_source_url = ? "
                "ORDER BY id DESC LIMIT 20",
                (data_source,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT metrics FROM eval_runs WHERE status = 'complete' ORDER BY id DESC LIMIT 20",
            ).fetchall()

    run_metrics = []
    for row in rows:
        try:
            metrics = json.loads(row["metrics"]) if isinstance(row["metrics"], str) else (row["metrics"] or {})
        except (json.JSONDecodeError, TypeError):
            continue
        for vid, vm in metrics.items():
            if isinstance(vm, dict) and "f1" in vm:
                run_metrics.append({"variant": vid, "f1": vm["f1"]})

    return compute_variant_stability(run_metrics)


@router.get("/api/eval/variants/{variant_a}/diff/{variant_b}")
def get_variant_diff(variant_a: str, variant_b: str):
    """Compare two variant configs and return human-readable differences.

    # What it shows: List of config changes between two variants (model, temperature, etc.).
    # Decision it drives: Helps the user understand what changed between variants
    #   to interpret why one performs better than another.
    """
    from ollama_queue.eval.analysis import describe_config_diff

    db = _api.db
    with db._lock:
        conn = db._connect()
        row_a = conn.execute("SELECT * FROM eval_variants WHERE id = ?", (variant_a,)).fetchone()
        row_b = conn.execute("SELECT * FROM eval_variants WHERE id = ?", (variant_b,)).fetchone()

    if not row_a or not row_b:
        missing = variant_a if not row_a else variant_b
        raise HTTPException(404, f"Variant '{missing}' not found")

    config_a = dict(row_a)
    config_b = dict(row_b)
    changes = describe_config_diff(config_a, config_b)
    return {"changes": changes}


@router.get("/api/eval/variants/{variant_id}/history")
def eval_variant_history(variant_id: str):
    """Returns F1/recall/precision history across completed eval_runs for one variant.

    What it shows: Per-run quality scores for a single variant over time.
    Decision it drives: Lets the user see whether a variant is improving, stable, or regressing.
    """
    db = _api.db
    with db._lock:
        conn = db._connect()
        _get_eval_variant(conn, variant_id)
        runs = conn.execute(
            "SELECT id, started_at, metrics FROM eval_runs WHERE status = 'complete' ORDER BY id ASC"
        ).fetchall()
    history = []
    for run_row in runs:
        if not run_row["metrics"]:
            continue
        try:
            metrics = json.loads(run_row["metrics"])
        except (ValueError, TypeError):
            continue
        var_metrics = metrics.get(variant_id)
        if not var_metrics or not isinstance(var_metrics, dict):
            continue
        history.append(
            {
                "run_id": run_row["id"],
                "started_at": run_row["started_at"],
                "f1": var_metrics.get("f1"),
                "recall": var_metrics.get("recall"),
                "precision": var_metrics.get("precision"),
            }
        )
    return history


@router.post("/api/eval/variants/{variant_id}/clone")
def clone_eval_variant(variant_id: str, body: dict = Body(default={})):
    """Clone any variant (system or user) into a new user variant.

    What it shows: N/A — write-only; the new variant appears in GET /api/eval/variants.
    Decision it drives: Lets the user safely experiment by copying a baseline without losing the original.
    """
    import datetime as _dt
    import uuid

    db = _api.db
    now = _dt.datetime.now(_dt.UTC).isoformat()
    new_id = str(uuid.uuid4())[:8]
    with db._lock:
        conn = db._connect()
        original = _get_eval_variant(conn, variant_id)
        label = body.get("label") or f"{original['label']} (copy)"
        conn.execute(
            """INSERT INTO eval_variants
               (id, label, prompt_template_id, model, temperature, num_ctx,
                is_recommended, is_system, created_at,
                params, system_prompt, training_config, provider)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id,
                label,
                original["prompt_template_id"],
                original["model"],
                original["temperature"],
                original["num_ctx"],
                0,
                0,  # always user-owned
                now,
                original.get("params"),
                original.get("system_prompt"),
                original.get("training_config"),
                original.get("provider"),
            ),
        )
        conn.commit()
        row = _get_eval_variant(conn, new_id)
    return JSONResponse(content=row, status_code=201)


@router.put("/api/eval/variants/{variant_id}")
def update_eval_variant(variant_id: str, body: dict = Body(...)):
    """Update a user variant (partial update OK). Rejects system variants.

    What it shows: N/A — write-only; updated row returned.
    Decision it drives: Lets the user tune parameters without creating a new variant.
    """
    db = _api.db
    with db._lock:
        conn = db._connect()
        variant = _get_eval_variant(conn, variant_id)
        if variant["is_system"]:
            raise HTTPException(status_code=422, detail="Cannot modify system variant — clone it first.")
        updatable_fields = {
            "label",
            "prompt_template_id",
            "model",
            "temperature",
            "num_ctx",
            "is_recommended",
            "system_prompt",
            "params",
            "training_config",
            "provider",
        }
        updates = {k: v for k, v in body.items() if k in updatable_fields}
        if not updates:
            return dict(variant)
        # Validate prompt_template_id if provided
        if "prompt_template_id" in updates:
            _get_eval_template(conn, updates["prompt_template_id"])
        # Validate and normalise params if provided
        if "params" in updates:
            try:
                updates["params"] = validate_variant_params(updates["params"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        # Validate provider if provided
        if "provider" in updates:
            try:
                updates["provider"] = validate_provider(updates["provider"])
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = [*list(updates.values()), variant_id]
        conn.execute(f"UPDATE eval_variants SET {set_clause} WHERE id = ?", values)
        conn.commit()
        row = _get_eval_variant(conn, variant_id)
    return row


@router.delete("/api/eval/variants/{variant_id}")
def delete_eval_variant(variant_id: str):
    """Delete a user variant. Rejects system variants.

    What it shows: N/A — delete operation; variant disappears from GET /api/eval/variants.
    Decision it drives: Lets the user remove experiments they no longer need.
    """
    db = _api.db
    with db._lock:
        conn = db._connect()
        variant = _get_eval_variant(conn, variant_id)
        if variant["is_system"]:
            raise HTTPException(status_code=422, detail="Cannot modify system variant — clone it first.")
        conn.execute("DELETE FROM eval_variants WHERE id = ?", (variant_id,))
        conn.commit()
    return Response(status_code=204)


# --- Eval: Templates ---


@router.get("/api/eval/templates")
def list_eval_templates():
    """Returns all eval_prompt_templates rows.

    What it shows: All available prompt templates (system + user).
    Decision it drives: Lets the user pick or clone a template when creating variants.
    """
    db = _api.db
    with db._lock:
        conn = db._connect()
        return [dict(r) for r in conn.execute("SELECT * FROM eval_prompt_templates ORDER BY created_at").fetchall()]


@router.put("/api/eval/templates/{template_id}")
def update_eval_template(template_id: str, body: dict = Body(...)):
    """Update a user template (partial update OK). Rejects system templates.

    What it shows: N/A — write-only; updated row returned.
    Decision it drives: Lets the user refine prompt instructions without losing the system originals.
    """
    db = _api.db
    with db._lock:
        conn = db._connect()
        template = _get_eval_template(conn, template_id)
        if template["is_system"]:
            raise HTTPException(status_code=422, detail="Cannot modify system template — clone it first.")
        updatable_fields = {"label", "instruction", "format_spec", "examples", "is_chunked"}
        updates = {k: v for k, v in body.items() if k in updatable_fields}
        if not updates:
            return dict(template)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = [*list(updates.values()), template_id]
        conn.execute(f"UPDATE eval_prompt_templates SET {set_clause} WHERE id = ?", values)
        conn.commit()
        row = _get_eval_template(conn, template_id)
    return row


@router.post("/api/eval/templates/{template_id}/clone")
def clone_eval_template(template_id: str, body: dict = Body(default={})):
    """Clone any template (system or user) into a new user template.

    What it shows: N/A — write-only; new template appears in GET /api/eval/templates.
    Decision it drives: Lets the user safely customize a prompt without altering system defaults.
    """
    import datetime as _dt
    import uuid

    db = _api.db
    now = _dt.datetime.now(_dt.UTC).isoformat()
    new_id = str(uuid.uuid4())[:8]
    with db._lock:
        conn = db._connect()
        original = _get_eval_template(conn, template_id)
        label = body.get("label") or f"{original['label']} (copy)"
        conn.execute(
            """INSERT INTO eval_prompt_templates
               (id, label, instruction, format_spec, examples, is_chunked, is_system, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                new_id,
                label,
                original["instruction"],
                original.get("format_spec"),
                original.get("examples"),
                original["is_chunked"],
                0,  # always user-owned
                now,
            ),
        )
        conn.commit()
        row = _get_eval_template(conn, new_id)
    return JSONResponse(content=row, status_code=201)
