"""Tests for ollama_queue.eval_engine."""

from __future__ import annotations

import json
import random
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ollama_queue.eval_engine import (
    _call_proxy,
    _ProxyDownError,
    _should_throttle,
    build_analysis_prompt,
    build_generation_prompt,
    build_judge_prompt,
    check_auto_promote,
    compute_metrics,
    create_eval_run,
    generate_eval_analysis,
    insert_eval_result,
    parse_judge_response,
    render_report,
    run_eval_generate,
    run_eval_judge,
    update_eval_run,
    update_eval_variant,
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


class TestShouldThrottle:
    def _make_db_with_health(self, health: dict) -> MagicMock:
        """Return a mock db with get_current_health() returning health."""
        db = MagicMock()
        db.get_current_health.return_value = health
        return db

    def test_returns_true_when_ram_high(self):
        db = self._make_db_with_health({"ram_pct": 90, "vram_pct": 10, "cpu_pct": 10})
        assert _should_throttle(db) is True

    def test_returns_true_when_vram_high(self):
        db = self._make_db_with_health({"ram_pct": 10, "vram_pct": 85, "cpu_pct": 10})
        assert _should_throttle(db) is True

    def test_returns_true_when_cpu_high(self):
        db = self._make_db_with_health({"ram_pct": 10, "vram_pct": 10, "cpu_pct": 95})
        assert _should_throttle(db) is True

    def test_returns_false_when_all_low(self):
        db = self._make_db_with_health({"ram_pct": 50, "vram_pct": 40, "cpu_pct": 30})
        assert _should_throttle(db) is False

    def test_returns_false_at_exactly_80(self):
        # Threshold is strict >80, not >=80
        db = self._make_db_with_health({"ram_pct": 80, "vram_pct": 80, "cpu_pct": 80})
        assert _should_throttle(db) is False

    def test_returns_true_just_above_80(self):
        db = self._make_db_with_health({"ram_pct": 80.1, "vram_pct": 0, "cpu_pct": 0})
        assert _should_throttle(db) is True

    def test_returns_false_on_exception_fail_open(self):
        """Health check failure must not stall the eval run — fail open."""
        db = MagicMock()
        db.get_current_health.side_effect = RuntimeError("health service down")
        assert _should_throttle(db) is False

    def test_uses_health_log_fallback_when_no_get_current_health(self):
        """When db lacks get_current_health, fall back to get_health_log."""
        db = MagicMock(spec=[])  # spec=[] means no attributes by default
        # Manually add get_health_log — simulates db without get_current_health
        db.get_health_log = MagicMock(return_value=[{"ram_pct": 95, "vram_pct": 0}])
        assert _should_throttle(db) is True

    def test_fallback_no_health_log_rows_returns_false(self):
        """Empty health log means no data — fail open (don't stall)."""
        db = MagicMock(spec=[])
        db.get_health_log = MagicMock(return_value=[])
        assert _should_throttle(db) is False

    def test_missing_keys_default_to_zero(self):
        """If health dict is missing keys, treat as 0 (not throttled)."""
        db = self._make_db_with_health({})
        assert _should_throttle(db) is False


# ---------------------------------------------------------------------------
# run_eval_generate scheduling modes
# ---------------------------------------------------------------------------
#
# Strategy: mock _fetch_items, _generate_one, and db interactions so no
# network or subprocess calls occur. Inject a no-op _sleep callable to avoid
# real sleeps in opportunistic-mode tests.
# ---------------------------------------------------------------------------


def _make_run_record(**overrides) -> dict:
    """Build a minimal eval_runs dict for use in mock DB lookups."""
    base = {
        "id": 1,
        "data_source_url": "http://test-host/",
        "variants": json.dumps(["A"]),
        "error_budget": 0.30,
        "run_mode": "batch",
        "item_ids": None,
        "max_runs": None,
        "max_time_s": None,
        "runs_completed": 0,
    }
    base.update(overrides)
    return base


def _make_variant() -> dict:
    return {
        "id": "A",
        "label": "Baseline",
        "prompt_template_id": "fewshot",
        "model": "test-model",
        "temperature": 0.7,
        "num_ctx": 4096,
    }


def _make_template() -> dict:
    return {
        "id": "fewshot",
        "label": "Fewshot",
        "instruction": "Test instruction",
        "examples": None,
        "is_chunked": 0,
    }


def _make_items(n: int = 5) -> list[dict]:
    return [
        {
            "id": str(i),
            "title": f"Item {i}",
            "one_liner": f"one liner {i}",
            "description": "",
            "cluster_id": "c1",
        }
        for i in range(n)
    ]


class TestRunEvalGenerateFillOpenSlots:
    """Tests for fill-open-slots mode stopping at max_runs or max_time_s."""

    def test_stops_at_max_runs(self):
        """fill-open-slots with max_runs=2 and 5 items stops after 2 submitted."""
        run = _make_run_record(run_mode="fill-open-slots", max_runs=2, max_time_s=None)
        items = _make_items(5)
        submitted_count = []

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._generate_one", side_effect=lambda **kw: submitted_count.append(1) or True),
            patch("ollama_queue.eval_engine.update_eval_run") as mock_update,
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: None)

        # Exactly 2 jobs submitted (max_runs=2)
        assert len(submitted_count) == 2
        # Final status must be judging (not failed)
        status_calls = [c for c in mock_update.call_args_list if c.kwargs.get("status") == "judging"]
        assert len(status_calls) >= 1

    def test_stops_at_max_time_s(self):
        """fill-open-slots with max_time_s stops when wall-clock budget is exhausted."""
        run = _make_run_record(run_mode="fill-open-slots", max_runs=None, max_time_s=0)
        # max_time_s=0 means any elapsed time >= 0 triggers the limit immediately
        # (checked before the first job after at least 1 iteration would trigger it)
        items = _make_items(5)
        submitted_count = []

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._generate_one", side_effect=lambda **kw: submitted_count.append(1) or True),
            patch("ollama_queue.eval_engine.update_eval_run") as mock_update,
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: None)

        # With max_time_s=0, the time check fires on the first iteration (elapsed >= 0).
        # Zero jobs are submitted because the time limit is checked BEFORE _generate_one.
        assert len(submitted_count) == 0
        status_calls = [c for c in mock_update.call_args_list if c.kwargs.get("status") == "judging"]
        assert len(status_calls) >= 1

    def test_both_limits_count_wins(self):
        """fill-open-slots with both limits: count fires first when max_runs < item count."""
        run = _make_run_record(run_mode="fill-open-slots", max_runs=1, max_time_s=9999)
        items = _make_items(5)
        submitted_count = []

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._generate_one", side_effect=lambda **kw: submitted_count.append(1) or True),
            patch("ollama_queue.eval_engine.update_eval_run"),
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: None)

        # Count cap (max_runs=1) fires before time cap (9999s)
        assert len(submitted_count) == 1

    def test_no_limits_runs_all_items(self):
        """fill-open-slots with no limits behaves like batch — all items processed."""
        run = _make_run_record(run_mode="fill-open-slots", max_runs=None, max_time_s=None)
        items = _make_items(3)
        submitted_count = []

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._generate_one", side_effect=lambda **kw: submitted_count.append(1) or True),
            patch("ollama_queue.eval_engine.update_eval_run"),
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: None)

        assert len(submitted_count) == 3


class TestRunEvalGenerateOpportunistic:
    """Tests for opportunistic mode throttling behaviour."""

    def test_sleeps_when_throttled(self):
        """opportunistic mode calls sleep when _should_throttle returns True."""
        run = _make_run_record(run_mode="opportunistic")
        items = _make_items(2)
        sleep_calls: list = []

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._generate_one", return_value=True),
            patch("ollama_queue.eval_engine._should_throttle", return_value=True),
            patch("ollama_queue.eval_engine.update_eval_run"),
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: sleep_calls.append(s))

        # One sleep per item (2 items)
        assert len(sleep_calls) == 2
        assert all(s == 30 for s in sleep_calls)

    def test_no_sleep_when_not_throttled(self):
        """opportunistic mode does not sleep when resources are low."""
        run = _make_run_record(run_mode="opportunistic")
        items = _make_items(3)
        sleep_calls: list = []

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._generate_one", return_value=True),
            patch("ollama_queue.eval_engine._should_throttle", return_value=False),
            patch("ollama_queue.eval_engine.update_eval_run"),
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: sleep_calls.append(s))

        assert len(sleep_calls) == 0


class TestRunEvalGenerateScheduled:
    """Tests for scheduled mode logging."""

    def test_scheduled_mode_sets_status_to_judging(self):
        """scheduled mode is behaviourally identical to batch — ends with judging."""
        run = _make_run_record(run_mode="scheduled")
        items = _make_items(2)

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._generate_one", return_value=True),
            patch("ollama_queue.eval_engine.update_eval_run") as mock_update,
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: None)

        status_calls = [c for c in mock_update.call_args_list if c.kwargs.get("status") == "judging"]
        assert len(status_calls) >= 1

    def test_scheduled_mode_does_not_sleep(self):
        """scheduled mode never throttles — no sleep calls."""
        run = _make_run_record(run_mode="scheduled")
        items = _make_items(2)
        sleep_calls: list = []

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._generate_one", return_value=True),
            patch("ollama_queue.eval_engine.update_eval_run"),
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: sleep_calls.append(s))

        assert len(sleep_calls) == 0


class TestRunEvalGenerateBatchDefault:
    """Verify batch (default) mode is unaffected by the mode dispatch code."""

    def test_batch_mode_processes_all_items(self):
        """batch mode submits every item regardless of resources."""
        run = _make_run_record(run_mode="batch")
        items = _make_items(4)
        submitted_count = []

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._generate_one", side_effect=lambda **kw: submitted_count.append(1) or True),
            patch("ollama_queue.eval_engine.update_eval_run"),
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: None)

        assert len(submitted_count) == 4

    def test_default_mode_when_run_mode_missing(self):
        """run_mode absent in run record defaults to batch behaviour."""
        run = _make_run_record()
        del run["run_mode"]  # simulate missing column
        items = _make_items(2)
        submitted_count = []

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._generate_one", side_effect=lambda **kw: submitted_count.append(1) or True),
            patch("ollama_queue.eval_engine.update_eval_run"),
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: None)

        assert len(submitted_count) == 2


# ---------------------------------------------------------------------------
# _ProxyDownError — service-restart abort (circuit breaker must NOT trigger)
# ---------------------------------------------------------------------------


class TestProxyDown:
    """_ProxyDownError is raised on ConnectError and aborts generate/judge loops cleanly.

    The circuit breaker exists to detect flaky Ollama quality. It must not fire
    when the proxy itself is unreachable (i.e. service is restarting). These tests
    verify that ConnectError → _ProxyDownError → clean abort path, distinct from the
    normal failure → failed++ → circuit breaker path.
    """

    def test_call_proxy_raises_proxy_down_on_connect_error(self):
        """httpx.ConnectError in _call_proxy must surface as _ProxyDownError, not (None, None)."""
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client_cls.return_value = mock_client

            with pytest.raises(_ProxyDownError):
                _call_proxy(
                    http_base="http://localhost:7683",
                    model="test-model",
                    prompt="test",
                    temperature=0.7,
                    num_ctx=4096,
                    timeout=30,
                    source="test",
                    priority=0,
                )

    def test_generate_loop_aborts_on_proxy_down_without_circuit_breaker(self):
        """When _generate_one raises _ProxyDownError, run is marked failed=proxy_unavailable
        and the loop exits immediately — the circuit breaker is never reached."""
        run = _make_run_record(run_mode="batch")
        items = _make_items(5)  # 5 items — would need 10+ failures to trip breaker

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.get_eval_variant", return_value=_make_variant()),
            patch("ollama_queue.eval_engine.get_eval_template", return_value=_make_template()),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            # First call raises _ProxyDownError — simulates service restart mid-run
            patch("ollama_queue.eval_engine._generate_one", side_effect=_ProxyDownError("conn refused")),
            patch("ollama_queue.eval_engine.update_eval_run") as mock_update,
            patch("ollama_queue.eval_engine.insert_eval_result"),
        ):
            run_eval_generate(1, MagicMock(), _sleep=lambda s: None)

        # Must have set status=failed with error=proxy_unavailable
        failed_calls = [
            c
            for c in mock_update.call_args_list
            if c.kwargs.get("status") == "failed" and c.kwargs.get("error") == "proxy_unavailable"
        ]
        assert len(failed_calls) == 1, "Expected exactly one proxy_unavailable failure update"

        # Must NOT have set status=failed with circuit_breaker error
        cb_calls = [c for c in mock_update.call_args_list if "circuit_breaker" in str(c.kwargs.get("error", ""))]
        assert len(cb_calls) == 0, "Circuit breaker must not fire on proxy down"

    def test_judge_loop_aborts_on_proxy_down(self):
        """When _judge_one_target raises _ProxyDownError, run is marked failed=proxy_unavailable."""
        run = {
            "id": 1,
            "data_source_url": "http://test-host/",
            "judge_model": "judge-model",
            "judge_temperature": 0.0,
            "same_cluster_targets": 1,
            "diff_cluster_targets": 1,
            "item_ids": None,
            "seed": 42,
        }
        # Simulate one generated result in the DB for the judge to process
        gen_row = {"source_item_id": "0", "principle": "test principle", "variant": "A"}
        items = _make_items(3)

        # Build a mock DB that returns gen_row from the SQL query inside run_eval_judge
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [gen_row]
        mock_db = MagicMock()
        mock_db._lock = MagicMock()
        mock_db._lock.__enter__ = MagicMock(return_value=None)
        mock_db._lock.__exit__ = MagicMock(return_value=False)
        mock_db._connect.return_value = mock_conn

        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine._fetch_items", return_value=items),
            patch("ollama_queue.eval_engine._get_eval_setting", side_effect=lambda db, key, default="": str(default)),
            patch("ollama_queue.eval_engine._judge_one_target", side_effect=_ProxyDownError("conn refused")),
            patch("ollama_queue.eval_engine.update_eval_run") as mock_update,
        ):
            run_eval_judge(1, mock_db)

        failed_calls = [
            c
            for c in mock_update.call_args_list
            if c.kwargs.get("status") == "failed" and c.kwargs.get("error") == "proxy_unavailable"
        ]
        assert len(failed_calls) == 1


# ---------------------------------------------------------------------------
# build_analysis_prompt
# ---------------------------------------------------------------------------


class TestBuildAnalysisPrompt:
    def _metrics(self):
        return {
            "D": {"f1": 0.71, "recall": 0.82, "precision": 0.62, "actionability": 3.9},
            "E": {"f1": 0.79, "recall": 0.88, "precision": 0.71, "actionability": 4.1},
        }

    def _top(self):
        return [{"variant": "E", "principle": "Always validate inputs", "score_transfer": 5}]

    def _bottom(self):
        return [{"variant": "D", "principle": "Be careful", "score_transfer": 1}]

    def test_contains_run_id(self):
        prompt = build_analysis_prompt(
            42, ["D", "E"], 20, "deepseek-r1:8b", self._metrics(), "E", self._top(), self._bottom()
        )
        assert "42" in prompt

    def test_contains_variant_ids(self):
        prompt = build_analysis_prompt(
            1, ["D", "E"], 20, "deepseek-r1:8b", self._metrics(), "E", self._top(), self._bottom()
        )
        assert "D" in prompt and "E" in prompt

    def test_contains_winner_label(self):
        prompt = build_analysis_prompt(
            1, ["D", "E"], 20, "deepseek-r1:8b", self._metrics(), "E", self._top(), self._bottom()
        )
        assert "winner" in prompt.lower()

    def test_contains_f1_values(self):
        prompt = build_analysis_prompt(
            1, ["D", "E"], 20, "deepseek-r1:8b", self._metrics(), "E", self._top(), self._bottom()
        )
        assert "0.79" in prompt
        assert "0.71" in prompt

    def test_contains_top_pair_principle(self):
        prompt = build_analysis_prompt(
            1, ["D", "E"], 20, "deepseek-r1:8b", self._metrics(), "E", self._top(), self._bottom()
        )
        assert "Always validate inputs" in prompt

    def test_contains_bottom_pair_principle(self):
        prompt = build_analysis_prompt(
            1, ["D", "E"], 20, "deepseek-r1:8b", self._metrics(), "E", self._top(), self._bottom()
        )
        assert "Be careful" in prompt

    def test_empty_pairs_still_produces_prompt(self):
        prompt = build_analysis_prompt(1, ["D"], 10, "judge", self._metrics(), None, [], [])
        assert len(prompt) > 100

    def test_contains_summary_section_request(self):
        prompt = build_analysis_prompt(1, ["D"], 10, "judge", self._metrics(), "D", [], [])
        assert "SUMMARY" in prompt

    def test_contains_recommendations_section_request(self):
        prompt = build_analysis_prompt(1, ["D"], 10, "judge", self._metrics(), "D", [], [])
        assert "RECOMMENDATIONS" in prompt

    def test_judge_model_in_prompt(self):
        prompt = build_analysis_prompt(1, ["D"], 10, "my-judge-model", self._metrics(), "D", [], [])
        assert "my-judge-model" in prompt

    def test_item_count_in_prompt(self):
        prompt = build_analysis_prompt(1, ["D"], 99, "judge", self._metrics(), "D", [], [])
        assert "99" in prompt

    def test_principle_truncated_at_180_chars(self):
        long_principle = "x" * 300
        pairs = [{"variant": "D", "principle": long_principle, "score_transfer": 3}]
        prompt = build_analysis_prompt(1, ["D"], 10, "judge", self._metrics(), "D", pairs, [])
        # 180-char truncation means the 181st char is never in the prompt
        assert "x" * 181 not in prompt
        assert "x" * 180 in prompt


# ---------------------------------------------------------------------------
# generate_eval_analysis
# ---------------------------------------------------------------------------


class TestGenerateEvalAnalysis:
    def _complete_run(self, **overrides):
        base = {
            "id": 1,
            "status": "complete",
            "variants": '["D", "E"]',
            "judge_model": "deepseek-r1:8b",
            "metrics": json.dumps(
                {
                    "D": {"f1": 0.71, "recall": 0.82, "precision": 0.62, "actionability": 3.9},
                    "E": {"f1": 0.79, "recall": 0.88, "precision": 0.71, "actionability": 4.1},
                }
            ),
            "winner_variant": "E",
            "item_count": 20,
        }
        base.update(overrides)
        return base

    def _mock_db_with_run(self, run):
        mock_db = MagicMock()
        mock_db._lock = MagicMock()
        mock_db._lock.__enter__ = MagicMock(return_value=None)
        mock_db._lock.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db._connect.return_value = mock_conn
        return mock_db

    def test_stores_analysis_md_on_success(self):
        run = self._complete_run()
        mock_db = self._mock_db_with_run(run)
        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine._fetch_analysis_samples", return_value=([], [])),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch(
                "ollama_queue.eval_engine._call_proxy",
                return_value=("SUMMARY: Good.\nWHY: High F1.\nRECOMMENDATIONS:\n1. Use E.", None),
            ),
            patch("ollama_queue.eval_engine.update_eval_run") as mock_update,
        ):
            generate_eval_analysis(mock_db, 1)
        stored_calls = [c for c in mock_update.call_args_list if "analysis_md" in c.kwargs]
        assert len(stored_calls) == 1
        assert "SUMMARY" in stored_calls[0].kwargs["analysis_md"]

    def test_skips_non_complete_run(self):
        run = self._complete_run(status="generating")
        mock_db = self._mock_db_with_run(run)
        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.update_eval_run") as mock_update,
        ):
            generate_eval_analysis(mock_db, 1)
        assert all("analysis_md" not in c.kwargs for c in mock_update.call_args_list)

    def test_skips_run_with_no_metrics(self):
        run = self._complete_run(metrics=None)
        mock_db = self._mock_db_with_run(run)
        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine.update_eval_run") as mock_update,
        ):
            generate_eval_analysis(mock_db, 1)
        assert all("analysis_md" not in c.kwargs for c in mock_update.call_args_list)

    def test_graceful_on_empty_proxy_response(self):
        run = self._complete_run()
        mock_db = self._mock_db_with_run(run)
        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine._fetch_analysis_samples", return_value=([], [])),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._call_proxy", return_value=(None, None)),
            patch("ollama_queue.eval_engine.update_eval_run") as mock_update,
        ):
            generate_eval_analysis(mock_db, 1)  # must not raise
        assert all("analysis_md" not in c.kwargs for c in mock_update.call_args_list)

    def test_graceful_on_proxy_down(self):
        run = self._complete_run()
        mock_db = self._mock_db_with_run(run)
        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine._fetch_analysis_samples", return_value=([], [])),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._call_proxy", side_effect=_ProxyDownError("down")),
            patch("ollama_queue.eval_engine.update_eval_run") as mock_update,
        ):
            generate_eval_analysis(mock_db, 1)  # must not raise
        assert all("analysis_md" not in c.kwargs for c in mock_update.call_args_list)

    def test_uses_analysis_model_setting_when_set(self):
        run = self._complete_run()
        mock_db = self._mock_db_with_run(run)
        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine._fetch_analysis_samples", return_value=([], [])),
            patch(
                "ollama_queue.eval_engine._get_eval_setting",
                side_effect=lambda db, key, default="": "custom-model" if key == "eval.analysis_model" else default,
            ),
            patch("ollama_queue.eval_engine._call_proxy", return_value=("analysis text", None)) as mock_proxy,
            patch("ollama_queue.eval_engine.update_eval_run"),
        ):
            generate_eval_analysis(mock_db, 1)
        called_model = mock_proxy.call_args.kwargs["model"]
        assert called_model == "custom-model"

    def test_falls_back_to_judge_model_when_analysis_model_empty(self):
        run = self._complete_run(judge_model="run-judge-model")
        mock_db = self._mock_db_with_run(run)
        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine._fetch_analysis_samples", return_value=([], [])),
            patch("ollama_queue.eval_engine._get_eval_setting", return_value=""),
            patch("ollama_queue.eval_engine._call_proxy", return_value=("analysis text", None)) as mock_proxy,
            patch("ollama_queue.eval_engine.update_eval_run"),
        ):
            generate_eval_analysis(mock_db, 1)
        called_model = mock_proxy.call_args.kwargs["model"]
        assert called_model == "run-judge-model"

    def test_falls_back_to_global_judge_model_when_run_judge_model_absent(self):
        # Rung 3: analysis_model="" AND run.judge_model=None → global eval.judge_model
        run = self._complete_run(judge_model=None)
        mock_db = self._mock_db_with_run(run)
        with (
            patch("ollama_queue.eval_engine.get_eval_run", return_value=run),
            patch("ollama_queue.eval_engine._fetch_analysis_samples", return_value=([], [])),
            patch(
                "ollama_queue.eval_engine._get_eval_setting",
                side_effect=lambda db, key, default="": "global-judge" if key == "eval.judge_model" else "",
            ),
            patch("ollama_queue.eval_engine._call_proxy", return_value=("analysis text", None)) as mock_proxy,
            patch("ollama_queue.eval_engine.update_eval_run"),
        ):
            generate_eval_analysis(mock_db, 1)
        called_model = mock_proxy.call_args.kwargs["model"]
        assert called_model == "global-judge"

    def test_not_found_run_returns_gracefully(self):
        mock_db = MagicMock()
        with patch("ollama_queue.eval_engine.get_eval_run", return_value=None):
            generate_eval_analysis(mock_db, 999)  # must not raise


# ---------------------------------------------------------------------------
# update_eval_variant
# ---------------------------------------------------------------------------


def test_update_eval_variant_sets_fields(tmp_path):
    """update_eval_variant sets arbitrary columns on an eval_variants row."""
    from ollama_queue.db import Database

    db = Database(str(tmp_path / "test.db"))
    db.initialize()

    # Insert a variant row directly; use "zero-shot-causal" which initialize() seeds.
    with db._lock:
        conn = db._connect()
        conn.execute(
            "INSERT INTO eval_variants (id, label, prompt_template_id, model, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            ("V1", "Variant 1", "zero-shot-causal", "qwen2.5:7b"),
        )
        conn.commit()

    update_eval_variant(db, "V1", is_recommended=1, is_production=1)

    with db._lock:
        conn = db._connect()
        row = conn.execute("SELECT is_recommended, is_production FROM eval_variants WHERE id = 'V1'").fetchone()
    assert row["is_recommended"] == 1
    assert row["is_production"] == 1


# ---------------------------------------------------------------------------
# TestCheckAutoPromote
# ---------------------------------------------------------------------------


class TestCheckAutoPromote:
    """Tests for check_auto_promote three-gate logic."""

    @pytest.fixture
    def db_with_complete_run(self, tmp_path):
        from ollama_queue.db import Database

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        db.set_setting("eval.auto_promote", True)
        db.set_setting("eval.f1_threshold", 0.75)
        db.set_setting("eval.auto_promote_min_improvement", 0.05)
        db.set_setting("eval.error_budget", 0.30)
        db.set_setting("eval.stability_window", 0)  # disabled
        import json

        run_id = create_eval_run(db, variant_id="A")
        metrics = json.dumps({"A": {"f1": 0.85, "precision": 0.9, "recall": 0.8, "actionability": 0.8}})
        update_eval_run(
            db, run_id, status="complete", winner_variant="A", metrics=metrics, item_count=10, error_budget=0.30
        )
        return db, run_id

    def test_skips_if_auto_promote_disabled(self, db_with_complete_run):
        db, run_id = db_with_complete_run
        db.set_setting("eval.auto_promote", False)
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_not_called()

    def test_skips_if_f1_below_threshold(self, db_with_complete_run):
        db, run_id = db_with_complete_run
        db.set_setting("eval.f1_threshold", 0.90)  # raise bar above winner F1=0.85
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_not_called()

    def test_skips_if_improvement_below_min(self, db_with_complete_run):
        """Skips when winner F1 doesn't beat production F1 + min_improvement."""
        import json

        db, run_id = db_with_complete_run
        # Mark variant B as production (B is seeded by initialize())
        with db._lock:
            conn = db._connect()
            conn.execute("UPDATE eval_variants SET is_production = 1 WHERE id = 'B'")
            conn.commit()
        old_run_id = create_eval_run(db, variant_id="B")
        old_metrics = json.dumps({"B": {"f1": 0.82, "precision": 0.85, "recall": 0.80, "actionability": 0.75}})
        update_eval_run(db, old_run_id, status="complete", winner_variant="B", metrics=old_metrics)
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_not_called()

    def test_promotes_when_all_gates_pass(self, db_with_complete_run):
        """Auto-promotes when F1 >= threshold AND delta >= min_improvement AND error_budget ok."""
        db, run_id = db_with_complete_run
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            mock_promote.return_value = {"ok": True, "run_id": run_id, "variant_id": "A", "label": "Config A"}
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_called_once_with(db, run_id)

    def test_skips_if_error_budget_exceeded(self, db_with_complete_run):
        """Skips if too many eval_results failed (score_transfer IS NULL)."""
        db, run_id = db_with_complete_run
        db.set_setting("eval.error_budget", 0.05)  # 5% tolerance
        # Insert 5 failed results out of item_count=10 → 50% failure rate > 5%
        for i in range(5):
            insert_eval_result(
                db,
                run_id=run_id,
                variant="A",
                source_item_id=f"src-{i}",
                target_item_id=f"tgt-{i}",
                is_same_cluster=0,
                row_type="judge",
            )
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_not_called()

    def test_never_raises_on_unexpected_error(self, tmp_path):
        """check_auto_promote swallows all exceptions — never propagates."""
        from ollama_queue.db import Database

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        db.set_setting("eval.auto_promote", True)
        # No run exists — should log and return without raising
        check_auto_promote(db, 9999, "http://localhost:7683")  # must not raise


class TestCheckAutoPromoteBayesian:
    """Tests for Bayesian-mode auto-promote: uses AUC + separation instead of F1."""

    @pytest.fixture
    def db_with_bayesian_run(self, tmp_path):
        """Create a DB with a complete bayesian run that has AUC=0.90, separation=0.5."""
        from ollama_queue.db import Database

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        db.set_setting("eval.auto_promote", True)
        db.set_setting("eval.auc_threshold", 0.85)
        db.set_setting("eval.min_posterior_separation", 0.4)
        db.set_setting("eval.auto_promote_min_improvement", 0.05)
        db.set_setting("eval.error_budget", 0.30)
        db.set_setting("eval.stability_window", 0)
        import json

        run_id = create_eval_run(db, variant_id="A")
        metrics = json.dumps(
            {
                "A": {
                    "auc": 0.90,
                    "same_mean_posterior": 0.85,
                    "diff_mean_posterior": 0.35,
                    "separation": 0.50,
                    "calibration_error": 0.02,
                }
            }
        )
        update_eval_run(
            db,
            run_id,
            status="complete",
            winner_variant="A",
            metrics=metrics,
            item_count=10,
            error_budget=0.30,
            judge_mode="bayesian",
        )
        return db, run_id

    def test_bayesian_promotes_when_auc_and_separation_pass(self, db_with_bayesian_run):
        """Bayesian auto-promote succeeds when AUC >= threshold AND separation >= min."""
        db, run_id = db_with_bayesian_run
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            mock_promote.return_value = {"ok": True, "run_id": run_id, "variant_id": "A", "label": "Config A"}
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_called_once_with(db, run_id)

    def test_bayesian_skips_when_auc_below_threshold(self, db_with_bayesian_run):
        """Bayesian auto-promote fails when AUC < threshold."""
        db, run_id = db_with_bayesian_run
        db.set_setting("eval.auc_threshold", 0.95)  # raise bar above AUC=0.90
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_not_called()

    def test_bayesian_skips_when_separation_below_min(self, db_with_bayesian_run):
        """Bayesian auto-promote fails when separation < min_posterior_separation."""
        db, run_id = db_with_bayesian_run
        db.set_setting("eval.min_posterior_separation", 0.6)  # raise bar above separation=0.50
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_not_called()

    def test_legacy_still_uses_f1(self, tmp_path):
        """Legacy auto-promote (rubric/binary) still uses F1, not AUC."""
        from ollama_queue.db import Database

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        db.set_setting("eval.auto_promote", True)
        db.set_setting("eval.f1_threshold", 0.75)
        db.set_setting("eval.auto_promote_min_improvement", 0.05)
        db.set_setting("eval.error_budget", 0.30)
        db.set_setting("eval.stability_window", 0)
        import json

        run_id = create_eval_run(db, variant_id="A")
        # Legacy run: has F1 but no AUC — should use F1 path
        metrics = json.dumps({"A": {"f1": 0.85, "precision": 0.9, "recall": 0.8, "actionability": 0.8}})
        update_eval_run(
            db,
            run_id,
            status="complete",
            winner_variant="A",
            metrics=metrics,
            item_count=10,
            error_budget=0.30,
            # No judge_mode set — defaults to 'rubric'
        )
        with patch("ollama_queue.eval_engine.do_promote_eval_run") as mock_promote:
            mock_promote.return_value = {"ok": True, "run_id": run_id, "variant_id": "A", "label": "Config A"}
            check_auto_promote(db, run_id, "http://localhost:7683")
        mock_promote.assert_called_once_with(db, run_id)


# ---------------------------------------------------------------------------
# Bayesian fusion functions (Task 17: ported from lessons-db/eval.py)
# ---------------------------------------------------------------------------


class TestBuildPairedJudgePrompt:
    """Tests for paired tournament prompt construction."""

    def test_contains_both_targets(self):
        """Prompt must contain content from both same-group and diff-group targets."""
        from ollama_queue.eval_engine import build_paired_judge_prompt

        same = {"title": "Resource cleanup failure", "one_liner": "Handles not closed", "description": "..."}
        diff = {"title": "Naming convention bug", "one_liner": "Wrong label", "description": "..."}
        prompt, _ = build_paired_judge_prompt("Resources must be released symmetrically", same, diff)
        assert "Resource cleanup failure" in prompt
        assert "Naming convention bug" in prompt
        assert "PRINCIPLE:" in prompt
        assert "TARGET A:" in prompt
        assert "TARGET B:" in prompt

    def test_position_randomization(self):
        """Different position seeds produce different A/B orderings."""
        from ollama_queue.eval_engine import build_paired_judge_prompt

        same = {"title": "Same", "one_liner": "s", "description": ""}
        diff = {"title": "Diff", "one_liner": "d", "description": ""}
        _, same_is_a_0 = build_paired_judge_prompt("principle", same, diff, position_seed=0)
        _, same_is_a_1 = build_paired_judge_prompt("principle", same, diff, position_seed=1)
        # Seeds 0 (even->swap) and 1 (odd->no swap) produce opposite orderings
        assert same_is_a_0 != same_is_a_1

    def test_returns_tuple(self):
        """Returns (str, bool) tuple."""
        from ollama_queue.eval_engine import build_paired_judge_prompt

        same = {"title": "T", "one_liner": "O", "description": "D"}
        diff = {"title": "T2", "one_liner": "O2", "description": "D2"}
        result = build_paired_judge_prompt("principle", same, diff, position_seed=1)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], bool)


class TestParsePairedJudge:
    """Tests for paired judge response parsing."""

    def test_valid_answers(self):
        """Parses A, B, NEITHER correctly."""
        from ollama_queue.eval_engine import parse_paired_judge

        assert parse_paired_judge("A") == "A"
        assert parse_paired_judge("B") == "B"
        assert parse_paired_judge("NEITHER") == "NEITHER"

    def test_strips_think_tags(self):
        """Think tags are removed before parsing."""
        from ollama_queue.eval_engine import parse_paired_judge

        assert parse_paired_judge("<THINK>reasoning here</THINK>A") == "A"

    def test_case_insensitive(self):
        """Handles lowercase and mixed case."""
        from ollama_queue.eval_engine import parse_paired_judge

        assert parse_paired_judge("a") == "A"
        assert parse_paired_judge("b - because it matches") == "B"
        assert parse_paired_judge("Neither applies well") == "NEITHER"

    def test_none_on_empty(self):
        """Returns None on empty or unparseable input."""
        from ollama_queue.eval_engine import parse_paired_judge

        assert parse_paired_judge("") is None
        assert parse_paired_judge(None) is None


class TestMechanismExtraction:
    """Tests for mechanism extraction prompt + parser."""

    def test_prompt_contains_both_lessons(self):
        """Mechanism prompt includes content from both lessons."""
        from ollama_queue.eval_engine import build_mechanism_extraction_prompt

        a = {"title": "Lesson A", "one_liner": "Bug A", "description": "Details A"}
        b = {"title": "Lesson B", "one_liner": "Bug B", "description": "Details B"}
        prompt = build_mechanism_extraction_prompt(a, b)
        assert "Lesson A" in prompt
        assert "Lesson B" in prompt
        assert "TRIGGER:" in prompt
        assert "TARGET:" in prompt
        assert "FIX:" in prompt

    def test_parse_valid_triplet(self):
        """Parses a valid TRIGGER/TARGET/FIX response."""
        from ollama_queue.eval_engine import parse_mechanism_triplet

        response = "TRIGGER: uncaught exception\nTARGET: cleanup handler\nFIX: symmetric teardown"
        result = parse_mechanism_triplet(response)
        assert result is not None
        assert result["trigger"] == "uncaught exception"
        assert result["target"] == "cleanup handler"
        assert result["fix"] == "symmetric teardown"

    def test_parse_none_response(self):
        """Returns None for NONE or empty responses."""
        from ollama_queue.eval_engine import parse_mechanism_triplet

        assert parse_mechanism_triplet("NONE") is None
        assert parse_mechanism_triplet("") is None
        assert parse_mechanism_triplet(None) is None


class TestSignalExtractors:
    """Tests for log-likelihood ratio signal extractors."""

    def test_paired_signal_signs(self):
        """Same -> positive, diff -> negative, neither -> zero."""
        from ollama_queue.eval_engine import compute_paired_signal

        assert compute_paired_signal("same") > 0
        assert compute_paired_signal("diff") < 0
        assert compute_paired_signal("neither") == 0.0

    def test_embedding_signal_signs(self):
        """High similarity -> positive, low -> negative."""
        from ollama_queue.eval_engine import compute_embedding_signal

        assert compute_embedding_signal(0.8) > 0
        assert compute_embedding_signal(0.1) < 0

    def test_scope_signal_signs(self):
        """High overlap -> positive, zero overlap -> negative, empty -> uninformative."""
        from ollama_queue.eval_engine import compute_scope_signal

        assert compute_scope_signal({"python", "web"}, {"python", "web"}) > 0
        assert compute_scope_signal({"python"}, {"java"}) < 0
        assert compute_scope_signal(set(), {"python"}) == 0.0

    def test_mechanism_signal_signs(self):
        """Match -> positive, no match -> negative, None -> uninformative."""
        from ollama_queue.eval_engine import compute_mechanism_signal

        assert compute_mechanism_signal(True) > 0
        assert compute_mechanism_signal(False) < 0
        assert compute_mechanism_signal(None) == 0.0


class TestComputeTransferPosterior:
    """Tests for Bayesian fusion posterior computation."""

    def test_prior_with_no_evidence(self):
        """All-zero signals should produce the prior probability (0.25)."""
        from ollama_queue.eval_engine import compute_transfer_posterior

        posterior = compute_transfer_posterior(0.0, 0.0, 0.0, 0.0)
        assert abs(posterior - 0.25) < 0.01

    def test_strong_positive_evidence(self):
        """Strong same-group paired signal should push posterior above 0.5."""
        from ollama_queue.eval_engine import compute_transfer_posterior

        posterior = compute_transfer_posterior(2.5, 0.0, 0.0, 0.0)
        assert posterior > 0.5

    def test_strong_negative_evidence(self):
        """Strong diff-group paired signal should push posterior well below 0.25."""
        from ollama_queue.eval_engine import compute_transfer_posterior

        posterior = compute_transfer_posterior(-2.5, 0.0, 0.0, 0.0)
        assert posterior < 0.1

    def test_bounded_zero_to_one(self):
        """Posterior is always in [0, 1]."""
        from ollama_queue.eval_engine import compute_transfer_posterior

        # Maximum positive evidence
        p = compute_transfer_posterior(2.5, 1.5, 1.0, 2.0)
        assert 0.0 <= p <= 1.0
        # Maximum negative evidence
        p = compute_transfer_posterior(-2.5, -1.5, -0.5, -1.5)
        assert 0.0 <= p <= 1.0


class TestComputeBayesianMetrics:
    """Tests for Bayesian AUC and separation metrics."""

    def test_separation_positive_with_discriminating_posteriors(self):
        """Same-group posteriors higher than diff-group -> positive separation."""
        from ollama_queue.eval_engine import compute_bayesian_metrics

        scored = [
            {"variant": "A", "is_same_group": True, "posterior": 0.8},
            {"variant": "A", "is_same_group": True, "posterior": 0.9},
            {"variant": "A", "is_same_group": False, "posterior": 0.2},
            {"variant": "A", "is_same_group": False, "posterior": 0.1},
        ]
        metrics = compute_bayesian_metrics(scored)
        assert "A" in metrics
        assert metrics["A"]["separation"] > 0
        assert metrics["A"]["auc"] > 0.5

    def test_indistinguishable_posteriors(self):
        """Equal posteriors for same/diff -> no separation, AUC near 0.5."""
        from ollama_queue.eval_engine import compute_bayesian_metrics

        scored = [
            {"variant": "B", "is_same_group": True, "posterior": 0.5},
            {"variant": "B", "is_same_group": False, "posterior": 0.5},
        ]
        metrics = compute_bayesian_metrics(scored)
        assert abs(metrics["B"]["separation"]) < 0.01
        assert abs(metrics["B"]["auc"] - 0.5) < 0.01

    def test_per_variant_grouping(self):
        """Metrics computed independently per variant."""
        from ollama_queue.eval_engine import compute_bayesian_metrics

        scored = [
            {"variant": "A", "is_same_group": True, "posterior": 0.9},
            {"variant": "A", "is_same_group": True, "posterior": 0.85},
            {"variant": "A", "is_same_group": False, "posterior": 0.1},
            {"variant": "A", "is_same_group": False, "posterior": 0.15},
            {"variant": "B", "is_same_group": True, "posterior": 0.55},
            {"variant": "B", "is_same_group": True, "posterior": 0.45},
            {"variant": "B", "is_same_group": False, "posterior": 0.4},
            {"variant": "B", "is_same_group": False, "posterior": 0.5},
        ]
        metrics = compute_bayesian_metrics(scored)
        assert "A" in metrics
        assert "B" in metrics
        # A has clear separation, B has overlapping posteriors -> A has higher AUC
        assert metrics["A"]["auc"] > metrics["B"]["auc"]


class TestJudgeModeParameter:
    """Tests for judge_mode parameter acceptance in _judge_one_target."""

    def test_rubric_mode_accepted(self, tmp_path):
        """_judge_one_target accepts judge_mode='rubric' without error."""
        from ollama_queue.db import Database
        from ollama_queue.eval_engine import _judge_one_target, create_eval_run

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        run_id = create_eval_run(db, variant_id="A")

        target = {"id": "42", "title": "Test", "one_liner": "Bug", "description": "Details"}

        with patch("ollama_queue.eval_engine._call_proxy") as mock_proxy:
            mock_proxy.return_value = (
                '{"transfer": 4, "precision": 3, "actionability": 3, "reasoning": "ok"}',
                0.5,
            )
            _judge_one_target(
                db=db,
                run_id=run_id,
                variant="A",
                source_item_id="1",
                principle="Test principle",
                target=target,
                is_same=True,
                judge_model="test-model",
                judge_temperature=0.1,
                source_tag="test",
                http_base="http://localhost:7683",
                judge_mode="rubric",
            )
        # Verify the result was stored
        with db._lock:
            conn = db._connect()
            row = conn.execute("SELECT score_transfer FROM eval_results WHERE run_id = ?", (run_id,)).fetchone()
        assert row is not None
        assert row[0] == 4

    def test_bayesian_mode_stores_posterior(self, tmp_path):
        """_judge_one_target with judge_mode='bayesian' stores score_posterior."""
        from ollama_queue.db import Database
        from ollama_queue.eval_engine import _judge_one_target, create_eval_run

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        run_id = create_eval_run(db, variant_id="A")

        same = {"id": "42", "title": "Same", "one_liner": "S", "description": ""}
        diff = {"id": "99", "title": "Diff", "one_liner": "D", "description": ""}

        with patch("ollama_queue.eval_engine._call_proxy") as mock_proxy:
            mock_proxy.return_value = ("A", 0.3)
            _judge_one_target(
                db=db,
                run_id=run_id,
                variant="A",
                source_item_id="1",
                principle="Test principle",
                target=same,
                is_same=True,
                judge_model="test-model",
                judge_temperature=0.1,
                source_tag="test",
                http_base="http://localhost:7683",
                judge_mode="bayesian",
                diff_target=diff,
            )
        with db._lock:
            conn = db._connect()
            row = conn.execute(
                "SELECT score_paired_winner, score_posterior FROM eval_results WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        assert row is not None
        assert row[0] in ("same", "diff", "neither")
        assert row[1] is not None  # posterior was computed and stored
        assert 0.0 <= row[1] <= 1.0

    def test_tournament_mode_stores_winner(self, tmp_path):
        """_judge_one_target with judge_mode='tournament' stores paired winner but no posterior."""
        from ollama_queue.db import Database
        from ollama_queue.eval_engine import _judge_one_target, create_eval_run

        db = Database(str(tmp_path / "test.db"))
        db.initialize()
        run_id = create_eval_run(db, variant_id="A")

        same = {"id": "42", "title": "Same", "one_liner": "S", "description": ""}
        diff = {"id": "99", "title": "Diff", "one_liner": "D", "description": ""}

        with patch("ollama_queue.eval_engine._call_proxy") as mock_proxy:
            mock_proxy.return_value = ("B", 0.3)
            _judge_one_target(
                db=db,
                run_id=run_id,
                variant="A",
                source_item_id="1",
                principle="Test principle",
                target=same,
                is_same=True,
                judge_model="test-model",
                judge_temperature=0.1,
                source_tag="test",
                http_base="http://localhost:7683",
                judge_mode="tournament",
                diff_target=diff,
            )
        with db._lock:
            conn = db._connect()
            row = conn.execute(
                "SELECT score_paired_winner, score_posterior FROM eval_results WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        assert row is not None
        assert row[0] in ("same", "diff", "neither")
        assert row[1] is None  # tournament mode does not compute posterior
