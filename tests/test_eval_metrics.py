"""Tests for eval/metrics.py — pure metric computation."""


def test_prior_log_odds_not_in_metrics_module():
    """_PRIOR_LOG_ODDS is dead code in metrics.py — authoritative copy is in judge.py."""
    import ast
    import pathlib

    src = pathlib.Path("ollama_queue/eval/metrics.py").read_text()
    tree = ast.parse(src)
    names = [
        node.targets[0].id for node in ast.walk(tree) if isinstance(node, ast.Assign) and hasattr(node.targets[0], "id")
    ]
    assert (
        "_PRIOR_LOG_ODDS" not in names
    ), "_PRIOR_LOG_ODDS in metrics.py is dead code (authoritative copy is in judge.py)"
