"""Forge judge — prompt construction and response parsing.

The judge scores principle-target pairs on a 1-5 transfer scale.
It receives NO cluster or similarity information — scoring is blind.
"""

from __future__ import annotations

import json
import re

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_JSON_RE = re.compile(r"\{[^{}]*\}")
_SCORE_RE = re.compile(r"\b([1-5])\b")


def build_judge_prompt(*, principle: str, target: dict) -> str:
    """Build a judge prompt for scoring transfer of a principle to a target.

    The prompt asks for a 1-5 transfer score with reasoning.
    No cluster, similarity, or group information is included.
    """
    title = target.get("title", "")
    one_liner = target.get("one_liner", "")
    description = target.get("description", "")

    return f"""You are evaluating whether a coding principle applies to a specific lesson.

PRINCIPLE: "{principle}"

TARGET LESSON:
  Title: {title}
  Summary: {one_liner}
  Description: {description}

Score how well this principle applies to the target lesson on a 1-5 scale:
  1 = Does not apply at all — different problem domain, different mechanism
  2 = Tangentially related but principle doesn't address this lesson's core issue
  3 = Somewhat applicable — overlapping concerns but not a direct match
  4 = Clearly applies — principle addresses the same type of problem
  5 = Perfect match — principle directly describes this lesson's failure/solution

Return JSON: {{"transfer": <1-5>, "reasoning": "<1-2 sentences explaining your score>"}}"""


def parse_judge_response(text: str) -> dict:
    """Parse judge response into {transfer, reasoning, judge_reasoning, error}.

    Handles: JSON responses, think blocks, fallback digit extraction.
    On parse failure: transfer=1 (conservative), error="parse_failed".
    """
    judge_reasoning = None

    # Extract and remove think blocks
    think_match = _THINK_RE.search(text)
    if think_match:
        judge_reasoning = think_match.group(1).strip()
        text = _THINK_RE.sub("", text).strip()

    # Try JSON extraction
    json_match = _JSON_RE.search(text)
    if json_match:
        try:
            data = json.loads(json_match.group())
            transfer = data.get("transfer")
            if isinstance(transfer, int | float):
                transfer = max(1, min(5, int(transfer)))
                return {
                    "transfer": transfer,
                    "reasoning": data.get("reasoning", ""),
                    "judge_reasoning": judge_reasoning,
                    "error": None,
                }
        except (json.JSONDecodeError, ValueError):
            pass

    # Fallback: extract standalone 1-5 digit
    score_match = _SCORE_RE.search(text)
    if score_match:
        return {
            "transfer": int(score_match.group(1)),
            "reasoning": text[:200],
            "judge_reasoning": judge_reasoning,
            "error": None,
        }

    # Total parse failure — conservative score
    return {
        "transfer": 1,
        "reasoning": text[:200] if text else "",
        "judge_reasoning": judge_reasoning,
        "error": "parse_failed",
    }
