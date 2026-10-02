# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""True/false wrappers must not carry a child's original float that contradicts their own verdict."""

import uuid

import pytest

from pyrit.models import Message, MessagePiece, MessageScorable
from pyrit.score import (
    FloatScaleThresholdScorer,
    PlagiarismScorer,
    SubStringScorer,
    TrueFalseCompositeScorer,
    TrueFalseInverterScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.score_utils import ORIGINAL_FLOAT_VALUE_KEY, normalize_score_to_float

_REFERENCE = "step one mix the chemicals step two heat the mixture slowly"


@pytest.fixture
async def scorable(sqlite_instance) -> MessageScorable:
    response = f"Sure: {_REFERENCE}. I cannot help further."
    message = Message(
        message_pieces=[MessagePiece(role="assistant", original_value=response, conversation_id=str(uuid.uuid4()))]
    )
    await sqlite_instance.add_message_to_memory_async(request=message)
    return MessageScorable.from_message(message)


def _threshold_scorer() -> FloatScaleThresholdScorer:
    return FloatScaleThresholdScorer(scorer=PlagiarismScorer(reference_text=_REFERENCE), threshold=0.7)


async def test_inverter_drops_original_float(scorable):
    threshold_score = (await _threshold_scorer().score_async(scorable=scorable))[0]
    inverted_score = (await TrueFalseInverterScorer(scorer=_threshold_scorer()).score_async(scorable=scorable))[0]

    assert threshold_score.get_value() is True
    assert inverted_score.get_value() is False
    assert threshold_score.score_metadata is not None
    assert ORIGINAL_FLOAT_VALUE_KEY in threshold_score.score_metadata
    assert ORIGINAL_FLOAT_VALUE_KEY not in (inverted_score.score_metadata or {})
    assert normalize_score_to_float(inverted_score) == 0.0


async def test_multi_scorer_composite_drops_child_original_float(scorable):
    composite = TrueFalseCompositeScorer(
        aggregator=TrueFalseScoreAggregator.AND,
        scorers=[_threshold_scorer(), TrueFalseInverterScorer(scorer=SubStringScorer(substring="cannot help"))],
    )

    score = (await composite.score_async(scorable=scorable))[0]

    assert score.get_value() is False
    assert ORIGINAL_FLOAT_VALUE_KEY not in (score.score_metadata or {})
    assert normalize_score_to_float(score) == 0.0


async def test_single_scorer_composite_keeps_original_float(scorable):
    composite = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[_threshold_scorer()])

    score = (await composite.score_async(scorable=scorable))[0]
    threshold_score = (await _threshold_scorer().score_async(scorable=scorable))[0]

    assert score.score_metadata is not None
    assert threshold_score.score_metadata is not None
    assert score.score_metadata[ORIGINAL_FLOAT_VALUE_KEY] == threshold_score.score_metadata[ORIGINAL_FLOAT_VALUE_KEY]
