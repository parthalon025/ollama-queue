"""Forge v2 — evaluation engine with oracle-calibrated scoring."""

from ollama_queue.forge.calibrator import apply_calibration, fit_calibration
from ollama_queue.forge.embedder import content_hash, embed_items
from ollama_queue.forge.engine import run_forge_cycle
from ollama_queue.forge.judge import build_judge_prompt, parse_judge_response
from ollama_queue.forge.metrics import compute_forge_metrics, score_variance, spearman_rank_correlation
from ollama_queue.forge.oracle import compute_kappa, compute_per_group_kappa, select_oracle_sample
from ollama_queue.forge.pairs import build_similarity_matrix, cosine_similarity, select_stratified_pairs
from ollama_queue.forge.settings import FORGE_DEFAULTS, get_forge_setting
from ollama_queue.forge.types import AutonomyLevel, ForgeDataSource, ForgeResult, ForgeRunStatus, PairQuartile

__all__ = [
    "FORGE_DEFAULTS",
    "AutonomyLevel",
    "ForgeDataSource",
    "ForgeResult",
    "ForgeRunStatus",
    "PairQuartile",
    "apply_calibration",
    "build_judge_prompt",
    "build_similarity_matrix",
    "compute_forge_metrics",
    "compute_kappa",
    "compute_per_group_kappa",
    "content_hash",
    "cosine_similarity",
    "embed_items",
    "fit_calibration",
    "get_forge_setting",
    "parse_judge_response",
    "run_forge_cycle",
    "score_variance",
    "select_oracle_sample",
    "select_stratified_pairs",
    "spearman_rank_correlation",
]
