"""Tests for eval variant params validation."""

import pytest

from ollama_queue.eval.validation import validate_provider, validate_variant_params


class TestValidateVariantParams:
    def test_none_returns_empty_json(self):
        assert validate_variant_params(None) == "{}"

    def test_empty_dict_returns_empty_json(self):
        assert validate_variant_params({}) == "{}"

    def test_valid_params_accepted(self):
        result = validate_variant_params({"top_k": 40, "top_p": 0.9})
        assert '"top_k": 40' in result
        assert '"top_p": 0.9' in result

    def test_string_input_parsed(self):
        result = validate_variant_params('{"top_k": 40}')
        assert '"top_k": 40' in result

    def test_invalid_param_rejected(self):
        with pytest.raises(ValueError, match="topk"):
            validate_variant_params({"topk": 40})

    def test_fuzzy_suggestion_for_typo(self):
        with pytest.raises(ValueError, match="top_k"):
            validate_variant_params({"topk": 40})

    def test_temperature_in_params_rejected(self):
        with pytest.raises(ValueError, match="flat fields"):
            validate_variant_params({"temperature": 0.5})

    def test_num_ctx_in_params_rejected(self):
        with pytest.raises(ValueError, match="flat fields"):
            validate_variant_params({"num_ctx": 4096})

    def test_non_dict_rejected(self):
        with pytest.raises(ValueError, match="JSON object"):
            validate_variant_params([1, 2, 3])

    def test_invalid_json_string_rejected(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            validate_variant_params("not valid json")

    def test_sorted_keys_in_output(self):
        result = validate_variant_params({"top_p": 0.9, "top_k": 40})
        assert result.index("top_k") < result.index("top_p")


class TestValidateProvider:
    def test_none_returns_ollama(self):
        assert validate_provider(None) == "ollama"

    def test_valid_providers_accepted(self):
        assert validate_provider("ollama") == "ollama"
        assert validate_provider("claude") == "claude"
        assert validate_provider("openai") == "openai"

    def test_case_insensitive(self):
        assert validate_provider("Ollama") == "ollama"
        assert validate_provider("CLAUDE") == "claude"

    def test_invalid_provider_rejected(self):
        with pytest.raises(ValueError, match="Invalid provider"):
            validate_provider("gemini")

    def test_whitespace_stripped(self):
        assert validate_provider("  ollama  ") == "ollama"
