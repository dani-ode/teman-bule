import pytest

from temanbule.modules.assessments.rubric import practice_score
from temanbule.platform.errors import ValidationError


def test_writing_rounds_half_up():
    assert practice_score(section="writing", dimensions={
        "task_fulfillment": 3, "organization": 2, "grammar": 2, "vocabulary": 1,
    }) == 53


@pytest.mark.parametrize("invalid", [True, 2.5, -1, 5, "3"])
def test_invalid_dimension_score_rejected(invalid):
    with pytest.raises(ValidationError):
        practice_score(section="speaking", dimensions={
            "task_fulfillment": invalid, "delivery": 4, "grammar": 4, "vocabulary": 4,
        })


def test_missing_dimension_rejected():
    with pytest.raises(ValidationError):
        practice_score(section="writing", dimensions={"grammar": 4})


@pytest.mark.parametrize("score,expected", [(0, 0), (4, 100)])
def test_score_bounds(score, expected):
    assert practice_score(section="writing", dimensions={
        "task_fulfillment": score, "organization": score,
        "grammar": score, "vocabulary": score,
    }) == expected
