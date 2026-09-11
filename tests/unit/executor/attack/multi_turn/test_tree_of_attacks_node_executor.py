# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import logging
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from pyrit.executor.attack.multi_turn.tree_of_attacks import _TreeOfAttacksNodeExecutor


@dataclass
class _NodeState:
    node_id: str
    executions: int = 0
    objective: str | None = None


def _make_node(*, node_id: str, call_order: list[str]) -> tuple[_NodeState, AsyncMock]:
    state = _NodeState(node_id=node_id)

    async def execute_async(objective: str) -> None:
        call_order.append(node_id)
        state.executions += 1
        state.objective = objective

    return state, AsyncMock(side_effect=execute_async)


async def test_execute_nodes_async_preserves_batch_order_and_node_state() -> None:
    call_order: list[str] = []
    states_and_methods = [_make_node(node_id=f"node-{index}", call_order=call_order) for index in range(5)]
    nodes = []
    for state, method in states_and_methods:
        node = AsyncMock()
        node.state = state
        node.send_prompt_async = method
        nodes.append(node)

    executor = _TreeOfAttacksNodeExecutor(batch_size=2, logger=logging.getLogger(__name__))

    completed_batches = [
        (batch_start, [node.state.node_id for node in batch])
        async for batch_start, batch in executor.execute_nodes_async(nodes=nodes, objective="objective")
    ]

    assert completed_batches == [(0, ["node-0", "node-1"]), (2, ["node-2", "node-3"]), (4, ["node-4"])]
    assert call_order == ["node-0", "node-1", "node-2", "node-3", "node-4"]
    assert [state.executions for state, _ in states_and_methods] == [1, 1, 1, 1, 1]
    assert [state.objective for state, _ in states_and_methods] == ["objective"] * 5


async def test_execute_nodes_async_stops_before_next_batch_on_failure() -> None:
    first = AsyncMock()
    first.send_prompt_async = AsyncMock(return_value=None)
    failing = AsyncMock()
    failing.send_prompt_async = AsyncMock(side_effect=RuntimeError("node failed"))
    not_started = AsyncMock()
    not_started.send_prompt_async = AsyncMock(return_value=None)
    executor = _TreeOfAttacksNodeExecutor(batch_size=2, logger=logging.getLogger(__name__))

    with pytest.raises(RuntimeError, match="node failed"):
        async for _ in executor.execute_nodes_async(
            nodes=[first, failing, not_started],
            objective="objective",
        ):
            pass

    first.send_prompt_async.assert_awaited_once_with(objective="objective")
    failing.send_prompt_async.assert_awaited_once_with(objective="objective")
    not_started.send_prompt_async.assert_not_awaited()


async def test_execute_nodes_async_propagates_cancellation_to_active_nodes() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def wait_for_cancellation_async(objective: str) -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    node = AsyncMock()
    node.send_prompt_async = AsyncMock(side_effect=wait_for_cancellation_async)
    executor = _TreeOfAttacksNodeExecutor(batch_size=1, logger=logging.getLogger(__name__))

    async def consume_async() -> None:
        async for _ in executor.execute_nodes_async(nodes=[node], objective="objective"):
            pass

    task = asyncio.create_task(consume_async())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancelled.is_set()
    node.send_prompt_async.assert_awaited_once_with(objective="objective")
