# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import patch
from uuid import uuid4

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import Message, MessagePiece, MessageScorable
from pyrit.score import Scorer, SubStringScorer, TrueFalseCompositeScorer, TrueFalseScoreAggregator


@pytest.mark.parametrize("entry_point", ["roots", "composite", "pieces", "batch"])
@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
async def test_scoring_failure_drains_nested_work_before_returning(
    sqlite_instance: MemoryInterface, entry_point, failure_type
):
    slow_started = asyncio.Event()
    allow_completion = asyncio.Event()
    finalized = asyncio.Event()
    started_tasks: set[asyncio.Task] = set()
    late_completions: list[str] = []
    slow = SubStringScorer(substring="slow")
    failing = SubStringScorer(substring="fail")
    original_piece_async = slow._score_piece_async
    original_scorable_async = slow._score_scorable_async

    def store_message(values: list[str]) -> MessageScorable:
        conversation_id = str(uuid4())
        message = Message(
            message_pieces=[
                MessagePiece(role="assistant", original_value=value, conversation_id=conversation_id)
                for value in values
            ]
        )
        sqlite_instance.add_message_to_memory(request=message)
        return MessageScorable.from_message(message)

    async def fail_async(*_args, **_kwargs):
        await slow_started.wait()
        raise failure_type("judge failed")

    async def score_piece_async(message_piece, *, objective=None):
        if message_piece.original_value == "fail":
            return await fail_async()
        task = asyncio.current_task()
        assert task is not None
        started_tasks.add(task)
        slow_started.set()
        try:
            await allow_completion.wait()
            late_completions.append(message_piece.original_value)
            return await original_piece_async(message_piece, objective=objective)
        finally:
            await asyncio.sleep(0)
            finalized.set()

    async def track_scorable_async(**kwargs):
        task = asyncio.current_task()
        assert task is not None
        started_tasks.add(task)
        return await original_scorable_async(**kwargs)

    async def invoke_async():
        if entry_point == "roots":
            return await Scorer.score_with_scorers_async(scorable=store_message(["slow"]), scorers=[slow, failing])
        if entry_point == "composite":
            composite = TrueFalseCompositeScorer(aggregator=TrueFalseScoreAggregator.AND, scorers=[slow, failing])
            return await composite.score_async(scorable=store_message(["slow"]))
        if entry_point == "pieces":
            return await slow.score_async(scorable=store_message(["slow", "fail"]))
        return await slow.score_batch_async(scorables=[store_message(["slow"]), store_message(["fail"])], batch_size=2)

    with (
        patch.object(slow, "_score_piece_async", new=score_piece_async),
        patch.object(slow, "_score_scorable_async", new=track_scorable_async),
        patch.object(failing, "_score_piece_async", new=fail_async),
    ):
        scoring_task = asyncio.create_task(invoke_async())
        try:
            with pytest.raises(failure_type, match="judge failed"):
                await asyncio.wait_for(scoring_task, timeout=5)
            cleaned_up_at_return = finalized.is_set() and all(task.done() for task in started_tasks)
        finally:
            # Also drain the broken implementation so the regression leaves no orphan tasks.
            allow_completion.set()
            await asyncio.gather(*started_tasks, return_exceptions=True)

    assert cleaned_up_at_return
    assert late_completions == []
    assert sqlite_instance.get_scores(score_type="true_false") == []
