"""Deterministic practice rubric; provider totals and weights are never trusted."""

from collections.abc import Mapping

from temanbule.platform.errors import ValidationError

RUBRIC_VERSION = "toefl-practice.v1"
WEIGHTS = {
    "writing": {"task_fulfillment": 30, "organization": 25, "grammar": 25, "vocabulary": 20},
    "speaking": {"task_fulfillment": 30, "delivery": 30, "grammar": 20, "vocabulary": 20},
}


def practice_score(*, section: str, dimensions: Mapping[str, int]) -> int:
    """Return 0..100, half-up rounded once; exact dimensions and integer 0..4."""
    weights = WEIGHTS.get(section)
    if weights is None or set(dimensions) != set(weights):
        raise ValidationError("Section atau dimensi rubric tidak valid.")
    if any(type(score) is not int or not 0 <= score <= 4 for score in dimensions.values()):
        raise ValidationError("Skor dimensi wajib integer 0..4.")
    numerator = sum(weights[code] * score for code, score in dimensions.items())
    return (numerator + 2) // 4
