# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import datetime

import pytest

from pyrit.identifiers import ScorerIdentifier
from pyrit.models import Score


@pytest.mark.asyncio
async def test_score_to_dict():
    scorer_identifier = ScorerIdentifier(
        class_name="TestScorer",
        class_module="pyrit.score",
        class_description="",
        identifier_type="instance",
    )
    sample_score = Score(
        id=str(uuid.uuid4()),
        score_value="false",
        score_value_description="true false score",
        score_type="true_false",
        score_category=["Category1"],
        score_rationale="Rationale text",
        score_metadata={"key": "value"},
        scorer_class_identifier=scorer_identifier,
        message_piece_id=str(uuid.uuid4()),
        timestamp=datetime.now(),
        objective="Task1",
    )
    result = sample_score.to_dict()

    # Check that all keys are present
    expected_keys = [
        "id",
        "score_value",
        "score_value_description",
        "score_type",
        "score_category",
        "score_rationale",
        "score_metadata",
        "scorer_class_identifier",
        "message_piece_id",
        "timestamp",
        "objective",
    ]

    for key in expected_keys:
        assert key in result, f"Missing key: {key}"

    # Check the key values
    assert result["id"] == str(sample_score.id)
    assert result["score_value"] == sample_score.score_value
    assert result["score_value_description"] == sample_score.score_value_description
    assert result["score_type"] == sample_score.score_type
    assert result["score_category"] == sample_score.score_category
    assert result["score_rationale"] == sample_score.score_rationale
    assert result["score_metadata"] == sample_score.score_metadata
    assert result["scorer_class_identifier"] == sample_score.scorer_class_identifier.to_dict()
    assert result["message_piece_id"] == str(sample_score.message_piece_id)
    assert result["timestamp"] == sample_score.timestamp.isoformat()
    assert result["objective"] == sample_score.objective
