"""Tests for Forge judge — prompt building and response parsing."""

from ollama_queue.forge.judge import build_judge_prompt, parse_judge_response


def test_build_judge_prompt_contains_principle():
    prompt = build_judge_prompt(
        principle="Always log before returning a fallback",
        target={"title": "Silent catch", "one_liner": "Bare except hides errors", "description": "When..."},
    )
    assert "Always log before returning a fallback" in prompt
    assert "Silent catch" in prompt


def test_build_judge_prompt_no_cluster_info():
    """Prompt must not contain any cluster or similarity information."""
    prompt = build_judge_prompt(
        principle="Test principle",
        target={"title": "T", "one_liner": "O", "description": "D"},
    )
    lower = prompt.lower()
    assert "cluster" not in lower
    assert "similarity" not in lower
    assert "quartile" not in lower


def test_parse_judge_response_valid_json():
    text = '{"transfer": 4, "reasoning": "Good match because..."}'
    result = parse_judge_response(text)
    assert result["transfer"] == 4
    assert "reasoning" in result
    assert result["error"] is None


def test_parse_judge_response_with_think_block():
    text = '<think>Let me analyze...</think>{"transfer": 3, "reasoning": "Partial match"}'
    result = parse_judge_response(text)
    assert result["transfer"] == 3
    assert result["judge_reasoning"] == "Let me analyze..."


def test_parse_judge_response_clamps_score():
    text = '{"transfer": 7, "reasoning": "x"}'
    result = parse_judge_response(text)
    assert result["transfer"] == 5  # clamped to max


def test_parse_judge_response_parse_failure():
    text = "I think this is a good match overall."
    result = parse_judge_response(text)
    assert result["transfer"] == 1  # conservative default
    assert result["error"] == "parse_failed"


def test_parse_judge_response_extract_score_from_text():
    """Fallback: extract standalone digit 1-5 if JSON fails."""
    text = "The transfer score is 4 out of 5."
    result = parse_judge_response(text)
    assert result["transfer"] == 4
