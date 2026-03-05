"""Tests for ollama_queue.eval_engine."""

from __future__ import annotations

import json
import random

import pytest

from ollama_queue.eval_engine import (
    build_generation_prompt,
    build_judge_prompt,
    compute_metrics,
    parse_judge_response,
    render_report,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def zero_shot_template() -> dict:
    return {
        "id": "zero-shot-causal",
        "label": "Figure it out",
        "instruction": "Extract the structural principle from this coding lesson as a causal statement.",
        "format_spec": None,
        "examples": None,
        "is_chunked": 0,
        "is_system": 1,
    }


@pytest.fixture
def chunked_template() -> dict:
    return {
        "id": "chunked",
        "label": "Show examples in groups",
        "instruction": "You are extracting transferable principles from grouped examples.",
        "format_spec": None,
        "examples": None,
        "is_chunked": 1,
        "is_system": 1,
    }


@pytest.fixture
def fewshot_template() -> dict:
    return {
        "id": "fewshot",
        "label": "Learn from examples first",
        "instruction": "You are extracting transferable principles. Review these examples first.",
        "format_spec": None,
        "examples": json.dumps(
            [
                {"output": "Silent fallbacks that return default values mask upstream failures."},
                {"output": "Resources acquired in callbacks must be released in symmetric teardown."},
            ]
        ),
        "is_chunked": 0,
        "is_system": 1,
    }


@pytest.fixture
def source_item() -> dict:
    return {
        "id": "101",
        "title": "Exception swallowed in callback",
        "one_liner": "Bare except clause silently hides failures",
        "description": "When exceptions are caught and discarded, upstream callers never know the operation failed.",
        "cluster_id": "cluster-A",
        "category": "error-handling",
    }


@pytest.fixture
def cluster_items() -> list[dict]:
    return [
        {
            "id": "102",
            "title": "Silent JSON parse failure",
            "one_liner": "Returns empty dict on parse error instead of raising",
            "description": "Caller assumes success, proceeds with empty state.",
            "cluster_id": "cluster-A",
            "category": "error-handling",
        },
        {
            "id": "103",
            "title": "Missing log before fallback return",
            "one_liner": "Returns None silently when dependency unavailable",
            "description": "No log message means the failure is invisible in production.",
            "cluster_id": "cluster-A",
            "category": "error-handling",
        },
    ]


# ---------------------------------------------------------------------------
# build_generation_prompt — zero-shot
# ---------------------------------------------------------------------------


class TestBuildGenerationPromptZeroShot:
    def test_contains_instruction(self, zero_shot_template, source_item):
        prompt = build_generation_prompt(zero_shot_template, source_item)
        assert "Extract the structural principle" in prompt

    def test_contains_item_title(self, zero_shot_template, source_item):
        prompt = build_generation_prompt(zero_shot_template, source_item)
        assert "Exception swallowed in callback" in prompt

    def test_contains_item_one_liner(self, zero_shot_template, source_item):
        prompt = build_generation_prompt(zero_shot_template, source_item)
        assert "Bare except clause silently hides failures" in prompt

    def test_contains_format_guidance(self, zero_shot_template, source_item):
        prompt = build_generation_prompt(zero_shot_template, source_item)
        assert "causal" in prompt.lower() or "format" in prompt.lower()

    def test_no_cluster_items_in_zero_shot(self, zero_shot_template, source_item, cluster_items):
        # Even with cluster_items passed, zero-shot template should not include them
        prompt = build_generation_prompt(zero_shot_template, source_item, cluster_items)
        # Zero-shot should not list the sibling items
        assert "Silent JSON parse failure" not in prompt

    def test_description_truncated_to_500(self, zero_shot_template):
        item = {
            "id": "1",
            "title": "Title",
            "one_liner": "Liner",
            "description": "X" * 600,
            "cluster_id": "c",
            "category": "cat",
        }
        prompt = build_generation_prompt(zero_shot_template, item)
        # Description should be capped at 500 chars
        assert "X" * 501 not in prompt
        assert "X" * 500 in prompt

    def test_returns_string(self, zero_shot_template, source_item):
        result = build_generation_prompt(zero_shot_template, source_item)
        assert isinstance(result, str)
        assert len(result) > 50


# ---------------------------------------------------------------------------
# build_generation_prompt — chunked
# ---------------------------------------------------------------------------


class TestBuildGenerationPromptChunked:
    def test_contains_instruction(self, chunked_template, source_item, cluster_items):
        prompt = build_generation_prompt(chunked_template, source_item, cluster_items)
        assert "extracting transferable principles" in prompt

    def test_includes_source_item(self, chunked_template, source_item, cluster_items):
        prompt = build_generation_prompt(chunked_template, source_item, cluster_items)
        assert "Exception swallowed in callback" in prompt

    def test_includes_cluster_items(self, chunked_template, source_item, cluster_items):
        prompt = build_generation_prompt(chunked_template, source_item, cluster_items)
        assert "Silent JSON parse failure" in prompt
        assert "Missing log before fallback return" in prompt

    def test_numbered_items(self, chunked_template, source_item, cluster_items):
        prompt = build_generation_prompt(chunked_template, source_item, cluster_items)
        assert "1." in prompt
        assert "2." in prompt

    def test_no_cluster_items_falls_back_gracefully(self, chunked_template, source_item):
        # is_chunked=1 but no cluster_items — should not crash
        prompt = build_generation_prompt(chunked_template, source_item, None)
        assert isinstance(prompt, str)
        assert len(prompt) > 30

    def test_empty_cluster_items_falls_back(self, chunked_template, source_item):
        prompt = build_generation_prompt(chunked_template, source_item, [])
        assert isinstance(prompt, str)
        assert len(prompt) > 30


# ---------------------------------------------------------------------------
# build_judge_prompt
# ---------------------------------------------------------------------------


class TestBuildJudgePrompt:
    @pytest.fixture
    def target_item(self):
        return {
            "id": "200",
            "title": "Unhandled promise rejection",
            "one_liner": "Promise chain drops errors silently",
            "description": "No .catch() handler means errors vanish in production.",
            "cluster_id": "cluster-A",
            "category": "error-handling",
        }

    def test_contains_principle(self, target_item):
        principle = "Silent error swallowing hides failures indefinitely."
        prompt = build_judge_prompt(principle, target_item, is_same_cluster=True)
        assert "Silent error swallowing hides failures indefinitely." in prompt

    def test_contains_target_title(self, target_item):
        principle = "Any principle."
        prompt = build_judge_prompt(principle, target_item, is_same_cluster=False)
        assert "Unhandled promise rejection" in prompt

    def test_contains_scoring_criteria(self, target_item):
        prompt = build_judge_prompt("principle", target_item, is_same_cluster=True)
        assert "Transfer Recognition" in prompt
        assert "Precision" in prompt
        assert "Actionability" in prompt

    def test_requests_json_response(self, target_item):
        prompt = build_judge_prompt("principle", target_item, is_same_cluster=False)
        assert "transfer" in prompt.lower()
        assert "precision" in prompt.lower()
        assert "actionability" in prompt.lower()
        assert "JSON" in prompt

    def test_is_same_cluster_not_leaked_to_judge(self, target_item):
        # The is_same_cluster flag should not appear verbatim in the judge prompt
        # to avoid biasing the judge
        prompt_same = build_judge_prompt("p", target_item, is_same_cluster=True)
        prompt_diff = build_judge_prompt("p", target_item, is_same_cluster=False)
        # Both prompts should be identical (flag is not in the prompt text)
        assert prompt_same == prompt_diff


# ---------------------------------------------------------------------------
# parse_judge_response
# ---------------------------------------------------------------------------


class TestParseJudgeResponseValid:
    def test_extracts_scores(self):
        raw = '{"transfer": 4, "precision": 3, "actionability": 5, "reasoning": "clear match"}'
        result = parse_judge_response(raw)
        assert result["transfer"] == 4
        assert result["precision"] == 3
        assert result["actionability"] == 5
        assert result["reasoning"] == "clear match"

    def test_clamps_scores_to_1_5(self):
        raw = '{"transfer": 0, "precision": 6, "actionability": -1}'
        result = parse_judge_response(raw)
        assert result["transfer"] == 1
        assert result["precision"] == 5
        assert result["actionability"] == 1

    def test_no_error_key_on_success(self):
        raw = '{"transfer": 3, "precision": 3, "actionability": 3}'
        result = parse_judge_response(raw)
        assert "error" not in result

    def test_judge_reasoning_empty_when_no_think(self):
        raw = '{"transfer": 3, "precision": 3, "actionability": 3}'
        result = parse_judge_response(raw)
        assert result["judge_reasoning"] == ""


class TestParseJudgeResponseThinkStripped:
    def test_think_block_captured(self):
        raw = '<think>This principle relates to error propagation...</think>\n{"transfer": 4, "precision": 4, "actionability": 3}'
        result = parse_judge_response(raw)
        assert result["transfer"] == 4
        assert "error propagation" in result["judge_reasoning"]

    def test_think_block_not_in_scores(self):
        raw = '<think>reasoning here</think>\n{"transfer": 2, "precision": 2, "actionability": 2}'
        result = parse_judge_response(raw)
        # transfer should come from JSON, not from think block content
        assert result["transfer"] == 2

    def test_multiline_think_block(self):
        raw = "<think>\nLine 1\nLine 2\nLine 3\n</think>\n" '{"transfer": 5, "precision": 5, "actionability": 5}'
        result = parse_judge_response(raw)
        assert result["transfer"] == 5
        assert "Line 1" in result["judge_reasoning"]

    def test_think_block_alone_no_json(self):
        raw = "<think>no scores here</think>"
        result = parse_judge_response(raw)
        assert result["error"] == "parse_failed"
        assert result["transfer"] == 1


class TestParseJudgeResponseInvalid:
    def test_malformed_json_returns_defaults(self):
        raw = "This is not JSON at all"
        result = parse_judge_response(raw)
        assert result["transfer"] == 1
        assert result["precision"] == 1
        assert result["actionability"] == 1
        assert result["error"] == "parse_failed"

    def test_partial_json_missing_keys(self):
        raw = '{"transfer": 4}'
        result = parse_judge_response(raw)
        assert result["error"] == "parse_failed"
        assert result["transfer"] == 1

    def test_empty_string_returns_defaults(self):
        result = parse_judge_response("")
        assert result["error"] == "parse_failed"
        assert result["transfer"] == 1
        assert result["precision"] == 1
        assert result["actionability"] == 1

    def test_judge_reasoning_contains_raw_on_failure(self):
        raw = "malformed response"
        result = parse_judge_response(raw)
        assert result["judge_reasoning"] == raw


# ---------------------------------------------------------------------------
# compute_metrics
# ---------------------------------------------------------------------------


class TestComputeMetricsBasic:
    def _make_result(self, variant, is_same, transfer, precision=3, action=4):
        return {
            "variant": variant,
            "is_same_cluster": is_same,
            "effective_score_transfer": transfer,
            "effective_score_precision": precision,
            "effective_score_action": action,
        }

    def test_recall_computed_correctly(self):
        results = [
            self._make_result("A", True, 4),  # same-cluster, transfer=4
            self._make_result("A", True, 2),  # same-cluster, transfer=2
        ]
        # recall = avg_transfer_same / 5.0 = (4+2)/2/5 = 0.60
        metrics = compute_metrics(results)
        assert metrics["A"]["recall"] == pytest.approx(0.60, abs=0.01)

    def test_precision_computed_correctly(self):
        results = [
            self._make_result("A", False, 1),  # diff-cluster, transfer=1
            self._make_result("A", False, 3),  # diff-cluster, transfer=3
        ]
        # precision = 1 - avg_transfer_diff/5 = 1 - (1+3)/2/5 = 1 - 0.4 = 0.60
        metrics = compute_metrics(results)
        assert metrics["A"]["precision"] == pytest.approx(0.60, abs=0.01)

    def test_f1_harmonic_mean(self):
        results = [
            self._make_result("A", True, 5),  # recall = 1.0
            self._make_result("A", False, 0),  # precision = 1.0 (0/5 = 0, 1-0=1)
        ]
        metrics = compute_metrics(results)
        # recall=1.0, precision=1.0 -> f1=1.0
        assert metrics["A"]["f1"] == pytest.approx(1.0, abs=0.01)

    def test_four_results_per_variant(self):
        results = [
            self._make_result("A", True, 4, action=5),
            self._make_result("A", True, 2, action=3),
            self._make_result("A", False, 1, action=4),
            self._make_result("A", False, 2, action=4),
        ]
        metrics = compute_metrics(results)
        assert "A" in metrics
        assert metrics["A"]["sample_count"] == 4

    def test_per_variant_separation(self):
        results = [
            self._make_result("A", True, 5),
            self._make_result("A", False, 1),
            self._make_result("B", True, 1),
            self._make_result("B", False, 5),
        ]
        metrics = compute_metrics(results)
        assert "A" in metrics
        assert "B" in metrics
        # A should have higher recall, B should have lower precision
        assert metrics["A"]["recall"] > metrics["B"]["recall"]

    def test_actionability_averaged(self):
        results = [
            self._make_result("A", True, 3, action=4),
            self._make_result("A", False, 2, action=2),
        ]
        metrics = compute_metrics(results)
        assert metrics["A"]["actionability"] == pytest.approx(3.0, abs=0.01)


class TestComputeMetricsEmpty:
    def test_empty_returns_empty_dict(self):
        result = compute_metrics([])
        assert result == {}

    def test_no_same_cluster_results(self):
        # Only diff-cluster results — recall defaults to 0
        results = [
            {
                "variant": "A",
                "is_same_cluster": False,
                "effective_score_transfer": 2,
                "effective_score_precision": 3,
                "effective_score_action": 4,
            }
        ]
        metrics = compute_metrics(results)
        assert metrics["A"]["recall"] == 0.0
        assert metrics["A"]["f1"] == 0.0  # recall=0 -> f1=0

    def test_no_diff_cluster_results(self):
        # Only same-cluster results — precision defaults to 0
        results = [
            {
                "variant": "A",
                "is_same_cluster": True,
                "effective_score_transfer": 5,
                "effective_score_precision": 3,
                "effective_score_action": 4,
            }
        ]
        metrics = compute_metrics(results)
        assert metrics["A"]["precision"] == 0.0
        assert metrics["A"]["f1"] == 0.0  # precision=0 -> f1=0


# ---------------------------------------------------------------------------
# Reproducibility — same seed + same items = same order
# ---------------------------------------------------------------------------


class TestReproducibleSeed:
    def test_same_seed_same_order(self):
        pool = list(range(100))
        rng1 = random.Random(42)  # noqa: S311 — not crypto, eval determinism test
        rng2 = random.Random(42)  # noqa: S311

        shuffled1 = pool[:]
        rng1.shuffle(shuffled1)

        shuffled2 = pool[:]
        rng2.shuffle(shuffled2)

        assert shuffled1 == shuffled2

    def test_different_seed_different_order(self):
        pool = list(range(100))
        rng1 = random.Random(42)  # noqa: S311
        rng2 = random.Random(99)  # noqa: S311

        shuffled1 = pool[:]
        rng1.shuffle(shuffled1)

        shuffled2 = pool[:]
        rng2.shuffle(shuffled2)

        # With 100 items, extremely unlikely to match
        assert shuffled1 != shuffled2

    def test_sub_seed_reproducible(self):
        """Verify the sub-seed pattern used in run_eval_judge is reproducible."""
        rng = random.Random(1234)  # noqa: S311
        sub_seeds_1 = [rng.random() for _ in range(5)]

        rng = random.Random(1234)  # noqa: S311
        sub_seeds_2 = [rng.random() for _ in range(5)]

        assert sub_seeds_1 == sub_seeds_2


# ---------------------------------------------------------------------------
# render_report
# ---------------------------------------------------------------------------


class TestRenderReport:
    @pytest.fixture
    def minimal_metrics(self):
        return {
            "A": {"f1": 0.44, "recall": 0.88, "precision": 0.29, "actionability": 3.2, "sample_count": 8},
            "E": {"f1": 0.79, "recall": 0.85, "precision": 0.74, "actionability": 4.1, "sample_count": 8},
        }

    def test_contains_run_id(self, db, minimal_metrics):
        report = render_report(42, minimal_metrics, db)
        assert "42" in report

    def test_contains_variant_ids(self, db, minimal_metrics):
        report = render_report(1, minimal_metrics, db)
        assert "A" in report
        assert "E" in report

    def test_contains_f1_values(self, db, minimal_metrics):
        report = render_report(1, minimal_metrics, db)
        assert "0.79" in report
        assert "0.44" in report

    def test_winner_is_highest_f1(self, db, minimal_metrics):
        report = render_report(1, minimal_metrics, db)
        # E has higher F1 (0.79 > 0.44)
        assert "Variant E" in report or "**E**" in report

    def test_returns_markdown_string(self, db, minimal_metrics):
        report = render_report(1, minimal_metrics, db)
        assert isinstance(report, str)
        assert "#" in report  # Has markdown headers

    def test_empty_metrics_graceful(self, db):
        report = render_report(1, {}, db)
        assert isinstance(report, str)
        assert "No scored pairs" in report or len(report) > 0

    def test_contains_summary_table(self, db, minimal_metrics):
        report = render_report(1, minimal_metrics, db)
        # Table should have header row
        assert "|" in report
        assert "Variant" in report or "variant" in report.lower()
