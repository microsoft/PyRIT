# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.models import ConversationScorable, Message, Score
from pyrit.score import (
    ObjectiveScorerEvaluator,
    SelfAskTrueFalseScorer,
    SubStringScorer,
    TrueFalseInverterScorer,
    create_conversation_scorer,
)

pytestmark = pytest.mark.usefixtures("patch_central_database")


@pytest.mark.parametrize("wrapper", ["conversation", "inverter", "leaf", "local"])
@pytest.mark.parametrize("entry", ["public", "nested", "image", "evaluation"])
@pytest.mark.parametrize("rpm", [None, 60])
@pytest.mark.parametrize("batch_size", [1, 2])
async def test_batching_respects_discovered_target_async(
    *, wrapper: str, entry: str, rpm: int | None, batch_size: int
) -> None:
    target = MockPromptTarget(rpm=rpm)
    child = SelfAskTrueFalseScorer(chat_target=target)
    if wrapper == "conversation":
        scorer = create_conversation_scorer(scorer=child)
    elif wrapper == "inverter":
        scorer = TrueFalseInverterScorer(scorer=child)
    elif wrapper == "local":
        scorer = SubStringScorer(substring="answer")
    else:
        scorer = child
    assert scorer.get_chat_target() is (None if wrapper == "local" else target)
    method = {"nested": "_score_nested_async", "image": "score_image_async"}.get(entry, "score_async")
    result = [Score(score_type="true_false", score_value="true")]
    with patch.object(scorer, method, new_callable=AsyncMock, return_value=result) as score_task:
        if entry == "evaluation":
            task = ObjectiveScorerEvaluator(scorer)._score_responses_grouped_async(
                responses=[Message.from_prompt(prompt="answer", role="assistant")],
                objectives=["Find the answer"],
                max_concurrency=batch_size,
            )
        elif entry == "image":
            task = scorer.score_image_batch_async(image_paths=["unused.png"], batch_size=batch_size)
        else:
            batch_method = scorer.score_batch_async if entry == "public" else scorer._score_batch_nested_async
            task = batch_method(
                scorables=[ConversationScorable(conversation_id="unused")],
                batch_size=batch_size,
            )
        if rpm is not None and batch_size != 1 and wrapper != "local":
            with pytest.raises(ValueError, match="Batch size must be configured to 1"):
                await task
            score_task.assert_not_awaited()
        else:
            scores = await task
            assert scores == ([result] if entry == "evaluation" else result)
            score_task.assert_awaited_once()
