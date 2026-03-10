"""Tests for ollama_queue.eval_analysis — pure analysis functions."""

from __future__ import annotations

from ollama_queue.eval_analysis import (
    _is_positive,
    bootstrap_f1_ci,
    compute_per_item_breakdown,
    compute_variant_stability,
    describe_config_diff,
    extract_failure_cases,
)


def _make_row(
    *,
    variant: str = "A",
    source_item_id: str = "src1",
    target_item_id: str = "tgt1",
    is_same_cluster: int | None = 1,
    score_transfer: int | None = 4,
    source_item_title: str = "Source Title",
    target_item_title: str = "Target Title",
    source_cluster: str = "cluster_a",
    target_cluster: str = "cluster_a",
    principle: str = "test principle",
) -> dict:
    """Factory for scored row dicts with sensible defaults."""
    return {
        "variant": variant,
        "source_item_id": source_item_id,
        "target_item_id": target_item_id,
        "is_same_cluster": is_same_cluster,
        "score_transfer": score_transfer,
        "source_item_title": source_item_title,
        "target_item_title": target_item_title,
        "source_cluster": source_cluster,
        "target_cluster": target_cluster,
        "principle": principle,
    }


# ---------------------------------------------------------------------------
# TestComputePerItemBreakdown
# ---------------------------------------------------------------------------


class TestComputePerItemBreakdown:
    def test_basic_breakdown(self):
        """Verify TP/FN/FP counts from 4 rows."""
        rows = [
            # TP: same cluster, high score
            _make_row(is_same_cluster=1, score_transfer=4),
            # FN: same cluster, low score
            _make_row(is_same_cluster=1, score_transfer=1),
            # FP: diff cluster, high score
            _make_row(is_same_cluster=0, score_transfer=5),
            # TN: diff cluster, low score
            _make_row(is_same_cluster=0, score_transfer=1),
        ]
        result = compute_per_item_breakdown(rows)
        assert len(result) == 1
        item = result[0]
        assert item["tp"] == 1
        assert item["fn"] == 1
        assert item["fp"] == 1

    def test_empty_input(self):
        assert compute_per_item_breakdown([]) == []

    def test_sorted_worst_first(self):
        """'bad' item (all FN) should sort before 'good' item (all TP)."""
        rows = [
            # Good item: all TP
            _make_row(source_item_id="good", is_same_cluster=1, score_transfer=5),
            _make_row(source_item_id="good", is_same_cluster=1, score_transfer=4),
            # Bad item: all FN (same cluster but low scores)
            _make_row(source_item_id="bad", is_same_cluster=1, score_transfer=1),
            _make_row(source_item_id="bad", is_same_cluster=1, score_transfer=2),
        ]
        result = compute_per_item_breakdown(rows)
        assert len(result) == 2
        assert result[0]["source_item_id"] == "bad"
        assert result[0]["f1"] == 0.0
        assert result[1]["source_item_id"] == "good"
        assert result[1]["f1"] == 1.0

    def test_no_cluster_data(self):
        """Returns sentinel when is_same_cluster is None for all rows."""
        rows = [
            _make_row(is_same_cluster=None, score_transfer=3),
            _make_row(is_same_cluster=None, score_transfer=5),
        ]
        result = compute_per_item_breakdown(rows)
        assert result == [{"status": "no_cluster_data"}]

    def test_custom_threshold(self):
        """threshold=3 vs threshold=4 changes TP/FN classification."""
        rows = [
            _make_row(is_same_cluster=1, score_transfer=3),
        ]
        # At threshold=3, score 3 is positive -> TP
        result_t3 = compute_per_item_breakdown(rows, positive_threshold=3)
        assert result_t3[0]["tp"] == 1
        assert result_t3[0]["fn"] == 0

        # At threshold=4, score 3 is negative -> FN
        result_t4 = compute_per_item_breakdown(rows, positive_threshold=4)
        assert result_t4[0]["tp"] == 0
        assert result_t4[0]["fn"] == 1


# ---------------------------------------------------------------------------
# TestExtractFailureCases
# ---------------------------------------------------------------------------


class TestExtractFailureCases:
    def test_extracts_fp_and_fn(self):
        """Verify 2 failures from 4 rows (TP, FN, TN, FP)."""
        rows = [
            _make_row(is_same_cluster=1, score_transfer=4),  # TP
            _make_row(is_same_cluster=1, score_transfer=1),  # FN
            _make_row(is_same_cluster=0, score_transfer=1),  # TN
            _make_row(is_same_cluster=0, score_transfer=5),  # FP
        ]
        failures = extract_failure_cases(rows)
        assert len(failures) == 2
        types = {f["type"] for f in failures}
        assert types == {"fn", "fp"}

    def test_empty_input(self):
        assert extract_failure_cases([]) == []

    def test_no_failures(self):
        """Only TP + TN returns empty list."""
        rows = [
            _make_row(is_same_cluster=1, score_transfer=5),  # TP
            _make_row(is_same_cluster=0, score_transfer=1),  # TN
        ]
        assert extract_failure_cases(rows) == []

    def test_includes_context_fields(self):
        """Verify all context fields present in FP result."""
        rows = [
            _make_row(
                is_same_cluster=0,
                score_transfer=5,
                variant="B",
                source_item_id="s42",
                target_item_id="t99",
                source_item_title="Source Lesson",
                target_item_title="Target Lesson",
                source_cluster="alpha",
                target_cluster="beta",
                principle="always log errors",
            ),
        ]
        failures = extract_failure_cases(rows)
        assert len(failures) == 1
        fp = failures[0]
        assert fp["type"] == "fp"
        assert fp["variant"] == "B"
        assert fp["source_item_id"] == "s42"
        assert fp["source_item_title"] == "Source Lesson"
        assert fp["target_item_id"] == "t99"
        assert fp["target_item_title"] == "Target Lesson"
        assert fp["source_cluster"] == "alpha"
        assert fp["target_cluster"] == "beta"
        assert fp["score_transfer"] == 5
        assert fp["principle"] == "always log errors"

    def test_zero_score_treated_as_score_not_missing(self):
        """score_transfer=0 should be treated as a real (negative) score, not as missing."""
        rows = [
            _make_row(is_same_cluster=1, score_transfer=0),
        ]
        failures = extract_failure_cases(rows)
        assert len(failures) == 1
        assert failures[0]["type"] == "fn"
        assert failures[0]["score_transfer"] == 0

    def test_no_cluster_data_shows_low_scoring(self):
        """Returns low_confidence entries when is_same_cluster=None."""
        rows = [
            _make_row(is_same_cluster=None, score_transfer=1, variant="A"),
            _make_row(is_same_cluster=None, score_transfer=5, variant="A"),
            _make_row(is_same_cluster=None, score_transfer=2, variant="A"),
        ]
        result = extract_failure_cases(rows)
        assert len(result) == 2  # scores 1 and 2 are below threshold=3
        assert all(r["type"] == "low_confidence" for r in result)
        # Sorted by score ascending
        assert result[0]["score_transfer"] == 1
        assert result[1]["score_transfer"] == 2


# ---------------------------------------------------------------------------
# TestBootstrapF1CI
# ---------------------------------------------------------------------------


class TestBootstrapF1CI:
    def _make_bootstrap_rows(self, n: int = 20, variant: str = "A") -> list[dict]:
        """Create n rows with mixed TP/FP/FN/TN for bootstrap testing."""
        rows = []
        for i in range(n):
            if i % 4 == 0:
                rows.append(_make_row(variant=variant, is_same_cluster=1, score_transfer=5))
            elif i % 4 == 1:
                rows.append(_make_row(variant=variant, is_same_cluster=1, score_transfer=1))
            elif i % 4 == 2:
                rows.append(_make_row(variant=variant, is_same_cluster=0, score_transfer=5))
            else:
                rows.append(_make_row(variant=variant, is_same_cluster=0, score_transfer=1))
        return rows

    def test_basic_ci(self):
        """Verify low <= mid <= high from 20 rows."""
        rows = self._make_bootstrap_rows(20)
        result = bootstrap_f1_ci(rows, variant="A", seed=42)
        assert result is not None
        assert result["low"] <= result["mid"] <= result["high"]

    def test_too_few_pairs_returns_none(self):
        """1 row returns None (below _MIN_BOOTSTRAP_PAIRS=10)."""
        rows = [_make_row(variant="A")]
        assert bootstrap_f1_ci(rows, variant="A") is None

    def test_wrong_variant_returns_none(self):
        """Filtering to nonexistent variant returns None."""
        rows = self._make_bootstrap_rows(20, variant="A")
        assert bootstrap_f1_ci(rows, variant="NONEXISTENT") is None

    def test_seed_determinism(self):
        """Same seed produces same result."""
        rows = self._make_bootstrap_rows(20)
        r1 = bootstrap_f1_ci(rows, variant="A", seed=123)
        r2 = bootstrap_f1_ci(rows, variant="A", seed=123)
        assert r1 == r2

    def test_no_cluster_data_returns_none(self):
        """All is_same_cluster=None returns None."""
        rows = [_make_row(variant="A", is_same_cluster=None) for _ in range(15)]
        assert bootstrap_f1_ci(rows, variant="A") is None

    def test_n_pairs_included(self):
        """Verify n_pairs field in result."""
        rows = self._make_bootstrap_rows(20)
        result = bootstrap_f1_ci(rows, variant="A", seed=42)
        assert result is not None
        assert result["n_pairs"] == 20


# ---------------------------------------------------------------------------
# TestComputeVariantStability
# ---------------------------------------------------------------------------


class TestComputeVariantStability:
    def test_basic_stability(self):
        """A (low stdev) stable, B (high stdev) unstable."""
        metrics = [
            {"variant": "A", "f1": 0.80},
            {"variant": "A", "f1": 0.81},
            {"variant": "A", "f1": 0.79},
            {"variant": "B", "f1": 0.90},
            {"variant": "B", "f1": 0.40},
            {"variant": "B", "f1": 0.60},
        ]
        result = compute_variant_stability(metrics)
        assert result["A"]["stable"] is True
        assert result["A"]["stdev"] < 0.10
        assert result["B"]["stable"] is False
        assert result["B"]["stdev"] > 0.10

    def test_single_run_stable(self):
        """Single run: stdev=0.0, stable=True."""
        metrics = [{"variant": "X", "f1": 0.75}]
        result = compute_variant_stability(metrics)
        assert result["X"]["stdev"] == 0.0
        assert result["X"]["stable"] is True
        assert result["X"]["n_runs"] == 1

    def test_empty_input(self):
        assert compute_variant_stability([]) == {}

    def test_custom_threshold(self):
        """stdev 0.035 is stable at 0.10 threshold, unstable at 0.03."""
        # Create data with stdev ~0.035
        metrics = [
            {"variant": "C", "f1": 0.80},
            {"variant": "C", "f1": 0.87},
        ]
        result_loose = compute_variant_stability(metrics, threshold=0.10)
        result_tight = compute_variant_stability(metrics, threshold=0.03)
        assert result_loose["C"]["stable"] is True
        assert result_tight["C"]["stable"] is False


# ---------------------------------------------------------------------------
# TestDescribeConfigDiff
# ---------------------------------------------------------------------------


class TestDescribeConfigDiff:
    def test_model_change(self):
        """Returns 1 change about model."""
        diffs = describe_config_diff(
            {"model": "qwen2.5:7b"},
            {"model": "llama3:8b"},
        )
        assert len(diffs) == 1
        assert "qwen2.5:7b" in diffs[0]
        assert "llama3:8b" in diffs[0]

    def test_temperature_change(self):
        """Returns 1 change with direction (more creative/deterministic)."""
        diffs = describe_config_diff(
            {"temperature": 0.3},
            {"temperature": 0.9},
        )
        assert len(diffs) == 1
        assert "more creative" in diffs[0]

        diffs_det = describe_config_diff(
            {"temperature": 0.9},
            {"temperature": 0.3},
        )
        assert len(diffs_det) == 1
        assert "more deterministic" in diffs_det[0]

    def test_identical_configs(self):
        """Returns empty list for identical configs."""
        cfg = {"model": "x", "temperature": 0.7, "num_ctx": 4096, "prompt_template_id": "A"}
        assert describe_config_diff(cfg, cfg) == []

    def test_multiple_changes(self):
        """3 changes for model + temp + num_ctx."""
        diffs = describe_config_diff(
            {"model": "a", "temperature": 0.5, "num_ctx": 2048},
            {"model": "b", "temperature": 0.8, "num_ctx": 4096},
        )
        assert len(diffs) == 3

    def test_none_temperature(self):
        """Handles None -> 0.6 change."""
        diffs = describe_config_diff(
            {"temperature": None},
            {"temperature": 0.6},
        )
        assert len(diffs) == 1
        assert "None" in diffs[0]
        assert "0.6" in diffs[0]

    def test_temperature_to_none(self):
        """Handles 0.7 -> None change — no direction annotation (lines 306-307)."""
        diffs = describe_config_diff(
            {"temperature": 0.7},
            {"temperature": None},
        )
        assert len(diffs) == 1
        assert "0.7" in diffs[0]
        assert "None" in diffs[0]
        # No direction when target is None
        assert "creative" not in diffs[0]
        assert "deterministic" not in diffs[0]

    def test_temperature_non_numeric_no_crash(self):
        """Non-numeric temperature values don't crash (lines 308-309)."""
        diffs = describe_config_diff(
            {"temperature": "warm"},
            {"temperature": "cold"},
        )
        assert len(diffs) == 1
        assert "warm" in diffs[0]
        assert "cold" in diffs[0]

    def test_prompt_template_change(self):
        """Prompt template diff reported (line 322)."""
        diffs = describe_config_diff(
            {"prompt_template_id": "A"},
            {"prompt_template_id": "B"},
        )
        assert len(diffs) == 1
        assert "Prompt template" in diffs[0]
        assert "A" in diffs[0]
        assert "B" in diffs[0]


# ---------------------------------------------------------------------------
# Test _is_positive edge cases
# ---------------------------------------------------------------------------


class TestIsPositiveEdgeCases:
    def test_none_returns_false(self):
        """_is_positive(None) returns False (line 35)."""
        assert _is_positive(None) is False

    def test_non_numeric_string_returns_false(self):
        """_is_positive('abc') catches ValueError and returns False (lines 38-39)."""
        assert _is_positive("abc") is False

    def test_non_castable_type_returns_false(self):
        """_is_positive with unconstable type returns False (lines 38-39)."""
        assert _is_positive([1, 2, 3]) is False
