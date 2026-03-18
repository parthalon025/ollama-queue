"""Forge v2 — evaluation engine with oracle-calibrated scoring."""

from ollama_queue.forge.archive import (
    ArchiveCell,
    compute_coverage,
    compute_qd_score,
    get_elites,
    grid_to_heatmap,
    try_insert,
)
from ollama_queue.forge.calibrator import apply_calibration, fit_calibration
from ollama_queue.forge.descriptors import (
    DEFAULT_GRID_SIZE,
    compute_default_descriptors,
    get_descriptor_axes,
    normalize_to_bin,
)
from ollama_queue.forge.embedder import content_hash, embed_items
from ollama_queue.forge.engine import run_forge_cycle
from ollama_queue.forge.engine_evolve import run_evolve_phase
from ollama_queue.forge.evolver import (
    crossover_prompts,
    evolve_generation,
    mutate_prompt,
    tournament_select,
)
from ollama_queue.forge.goodhart import (
    check_goodhart_divergence,
    compute_metric_staleness,
    compute_monitoring_composite,
)
from ollama_queue.forge.judge import build_judge_prompt, parse_judge_response
from ollama_queue.forge.metrics import (
    compute_forge_metrics,
    score_variance,
    spearman_rank_correlation,
)
from ollama_queue.forge.oracle import (
    compute_kappa,
    compute_per_group_kappa,
    select_oracle_sample,
)
from ollama_queue.forge.pairs import (
    build_similarity_matrix,
    cosine_similarity,
    select_stratified_pairs,
)
from ollama_queue.forge.settings import FORGE_DEFAULTS, get_forge_setting
from ollama_queue.forge.splits import TEST, TRAIN, VALIDATION, assign_split, split_items
from ollama_queue.forge.thompson import ThompsonBudget
from ollama_queue.forge.types import (
    AutonomyLevel,
    ForgeDataSource,
    ForgeResult,
    ForgeRunStatus,
    PairQuartile,
)

__all__ = [
    "DEFAULT_GRID_SIZE",
    "FORGE_DEFAULTS",
    "TEST",
    "TRAIN",
    "VALIDATION",
    "ArchiveCell",
    "AutonomyLevel",
    "ForgeDataSource",
    "ForgeResult",
    "ForgeRunStatus",
    "PairQuartile",
    "ThompsonBudget",
    "apply_calibration",
    "assign_split",
    "build_judge_prompt",
    "build_similarity_matrix",
    "check_goodhart_divergence",
    "compute_coverage",
    "compute_default_descriptors",
    "compute_forge_metrics",
    "compute_kappa",
    "compute_metric_staleness",
    "compute_monitoring_composite",
    "compute_per_group_kappa",
    "compute_qd_score",
    "content_hash",
    "cosine_similarity",
    "crossover_prompts",
    "embed_items",
    "evolve_generation",
    "fit_calibration",
    "get_descriptor_axes",
    "get_elites",
    "get_forge_setting",
    "grid_to_heatmap",
    "mutate_prompt",
    "normalize_to_bin",
    "parse_judge_response",
    "run_evolve_phase",
    "run_forge_cycle",
    "score_variance",
    "select_oracle_sample",
    "select_stratified_pairs",
    "spearman_rank_correlation",
    "split_items",
    "tournament_select",
    "try_insert",
]
