import json
from unittest.mock import AsyncMock, Mock

import pytest

from temanbule.modules.assessments.models import ToeflAttempt, ToeflSubmission, ToeflTestVersion
from temanbule.modules.assessments.services import ToeflService
from temanbule.platform.errors import ValidationError


@pytest.mark.asyncio
@pytest.mark.parametrize("quote,valid", [("My answer", True), ("fabricated", False)])
async def test_writing_persists_only_grounded_backend_score(quote, valid):
    session = Mock()
    attempt = ToeflAttempt(id="attempt", user_id="user", test_version_id="test",
                           runtime_snapshot_id="snapshot", state="submitted")
    submission = ToeflSubmission(answer="My answer explains my choice.")
    session.get = AsyncMock(return_value=ToeflTestVersion(
        rubric_version="toefl-practice.v1", definition=json.dumps({"sections": [{
            "code": "writing", "questions": [{"ref": "writing-1"}],
        }]}),
    ))
    session.execute = AsyncMock(side_effect=[
        Mock(scalar_one_or_none=Mock(return_value=submission)),
        Mock(scalar_one_or_none=Mock(return_value=None)),
    ])
    session.flush = AsyncMock()
    service = ToeflService(session)
    service._owned_attempt = AsyncMock(return_value=attempt)
    dimensions = {"task_fulfillment": 3, "organization": 2, "grammar": 2, "vocabulary": 1}
    arguments = dict(user_id="user", attempt_id="attempt", question_ref="writing-1",
                     dimensions=dimensions, evidence={key: quote for key in dimensions},
                     flow_version="verified-flow.v1")
    if valid:
        score = await service.record_writing_evaluation(**arguments)
        assert score.total_score == 53
        assert attempt.state == "evaluated"
        session.add.assert_called_once_with(score)
    else:
        with pytest.raises(ValidationError):
            await service.record_writing_evaluation(**arguments)
        session.add.assert_not_called()
        assert attempt.state == "submitted"
