"""Eval pipeline business logic — generate, judge, analyze, promote.

Re-exports commonly used names so that ``from ollama_queue.eval import X``
works for the primary API surface. For internal helpers (prompt builders,
signal computers, tournament/Bayesian metrics), import from the specific
submodule: ``engine``, ``generate``, ``judge``, ``metrics``, ``promote``,
or ``analysis``.
"""

from ollama_queue.eval.analysis import (
    bootstrap_f1_ci,
    compute_per_item_breakdown,
    compute_variant_stability,
    describe_config_diff,
    extract_failure_cases,
)
from ollama_queue.eval.engine import (
    compute_run_analysis,
    create_eval_run,
    get_eval_run,
    get_eval_variant,
    insert_eval_result,
    run_eval_session,
    update_eval_run,
    update_eval_variant,
)
from ollama_queue.eval.judge import (
    build_judge_prompt,
    parse_judge_response,
    run_eval_judge,
)
from ollama_queue.eval.metrics import (
    compute_metrics,
    render_report,
)
from ollama_queue.eval.promote import (
    check_auto_promote,
    do_promote_eval_run,
    generate_eval_analysis,
)

__all__ = [
    "bootstrap_f1_ci",
    "build_judge_prompt",
    "check_auto_promote",
    "compute_metrics",
    "compute_per_item_breakdown",
    "compute_run_analysis",
    "compute_variant_stability",
    "create_eval_run",
    "describe_config_diff",
    "do_promote_eval_run",
    "extract_failure_cases",
    "generate_eval_analysis",
    "get_eval_run",
    "get_eval_variant",
    "insert_eval_result",
    "parse_judge_response",
    "render_report",
    "run_eval_judge",
    "run_eval_session",
    "update_eval_run",
    "update_eval_variant",
]
