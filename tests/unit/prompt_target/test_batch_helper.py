# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.prompt_target.batch_helper import (
    _get_chunks,
    _validate_rate_limit_parameters,
    batch_task_async,
)


def test_get_chunks_single_list():
    items = [1, 2, 3, 4, 5]
    chunks = list(_get_chunks(items, batch_size=2))
    assert chunks == [[[1, 2]], [[3, 4]], [[5]]]


def test_get_chunks_multiple_lists():
    a = [1, 2, 3, 4]
    b = ["a", "b", "c", "d"]
    chunks = list(_get_chunks(a, b, batch_size=2))
    assert chunks == [[[1, 2], ["a", "b"]], [[3, 4], ["c", "d"]]]


def test_get_chunks_no_args_raises():
    with pytest.raises(ValueError, match="No arguments provided"):
        list(_get_chunks(batch_size=2))


def test_get_chunks_mismatched_lengths_raises():
    with pytest.raises(ValueError, match="same length"):
        list(_get_chunks([1, 2], [1], batch_size=2))


def test_get_chunks_batch_size_larger_than_list():
    items = [1, 2]
    chunks = list(_get_chunks(items, batch_size=10))
    assert chunks == [[[1, 2]]]


def test_validate_rate_limit_no_target():
    # Should not raise when no target is provided
    _validate_rate_limit_parameters(prompt_target=None, batch_size=5)


def test_validate_rate_limit_no_rpm():
    target = MagicMock()
    target._max_requests_per_minute = None
    # Should not raise when target has no RPM limit
    _validate_rate_limit_parameters(prompt_target=target, batch_size=5)


def test_validate_rate_limit_rpm_with_batch_1():
    target = MagicMock()
    target._max_requests_per_minute = 10
    # Should not raise when batch_size is 1 (compatible with RPM limiting)
    _validate_rate_limit_parameters(prompt_target=target, batch_size=1)


def test_validate_rate_limit_rpm_with_batch_gt_1_raises():
    target = MagicMock()
    target._max_requests_per_minute = 10
    with pytest.raises(ValueError, match="Batch size must be configured to 1"):
        _validate_rate_limit_parameters(prompt_target=target, batch_size=5)


async def test_batch_task_async_empty_items_raises():
    with pytest.raises(ValueError, match="No items to batch"):
        await batch_task_async(
            batch_size=2,
            items_to_batch=[],
            task_func=AsyncMock(),
            task_arguments=["arg"],
        )


async def test_batch_task_async_empty_inner_list_raises():
    with pytest.raises(ValueError, match="No items to batch"):
        await batch_task_async(
            batch_size=2,
            items_to_batch=[[]],
            task_func=AsyncMock(),
            task_arguments=["arg"],
        )


async def test_batch_task_async_mismatched_args_raises():
    with pytest.raises(ValueError, match="Number of lists of items to batch must match"):
        await batch_task_async(
            batch_size=2,
            items_to_batch=[[1, 2]],
            task_func=AsyncMock(),
            task_arguments=["arg1", "arg2"],
        )


async def test_batch_task_async_calls_func():
    mock_func = AsyncMock(return_value="result")
    results = await batch_task_async(
        batch_size=2,
        items_to_batch=[[1, 2, 3]],
        task_func=mock_func,
        task_arguments=["item"],
    )
    assert len(results) == 3
    assert mock_func.call_count == 3


async def test_batch_task_async_multiple_item_lists():
    mock_func = AsyncMock(return_value="ok")
    results = await batch_task_async(
        batch_size=2,
        items_to_batch=[[1, 2], ["a", "b"]],
        task_func=mock_func,
        task_arguments=["num", "letter"],
    )
    assert len(results) == 2
    assert mock_func.call_count == 2


async def test_batch_task_async_passes_kwargs():
    mock_func = AsyncMock(return_value="done")
    await batch_task_async(
        batch_size=1,
        items_to_batch=[[10]],
        task_func=mock_func,
        task_arguments=["x"],
        extra_param="extra_value",
    )
    call_kwargs = mock_func.call_args[1]
    assert call_kwargs["x"] == 10
    assert call_kwargs["extra_param"] == "extra_value"


async def test_batch_task_async_validates_rate_limit():
    target = MagicMock()
    target._max_requests_per_minute = 10
    with pytest.raises(ValueError, match="Batch size must be configured to 1"):
        await batch_task_async(
            prompt_target=target,
            batch_size=2,
            items_to_batch=[[1, 2]],
            task_func=AsyncMock(),
            task_arguments=["item"],
        )


@pytest.mark.parametrize("failure_type", [RuntimeError, asyncio.CancelledError])
async def test_batch_task_failure_finishes_cleanup_and_never_starts_later_batches(failure_type):
    slow_started = asyncio.Event()
    finalized = asyncio.Event()
    calls: list[int] = []
    slow_tasks: list[asyncio.Task] = []

    async def send_async(*, item):
        calls.append(item)
        if item == 1:
            slow_task = asyncio.current_task()
            assert slow_task is not None
            slow_tasks.append(slow_task)
            slow_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                finalized.set()
        await slow_started.wait()
        raise failure_type("send failed")

    try:
        with pytest.raises(failure_type, match="send failed"):
            await asyncio.wait_for(
                batch_task_async(
                    batch_size=2,
                    items_to_batch=[[1, 2, 3]],
                    task_func=send_async,
                    task_arguments=["item"],
                ),
                timeout=5,
            )
        assert finalized.is_set()
        assert len(slow_tasks) == 1 and slow_tasks[0].done()
        assert calls == [1, 2]
    finally:
        for task in slow_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*slow_tasks, return_exceptions=True)


@pytest.mark.parametrize("cancel_again", [False, True], ids=["single-cancel", "repeated-cancel"])
@pytest.mark.parametrize("cancel_children", [False, True], ids=["caller-cancelled", "children-cancelled"])
async def test_caller_cancellation_preserves_slow_child_cleanup(cancel_again, cancel_children):
    all_started = asyncio.Event()
    fast_finished = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    children: dict[int, asyncio.Task] = {}
    calls: list[int] = []

    async def send_async(*, item):
        task = asyncio.current_task()
        assert task is not None
        children[item] = task
        calls.append(item)
        if item == 2:
            # Observe completion after gather's child callback, not merely entry into finally.
            task.add_done_callback(lambda _: fast_finished.set())
        if len(children) == 2:
            all_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            if item == 1:
                cleanup_started.set()
                await release_cleanup.wait()
                cleanup_finished.set()

    parent = asyncio.create_task(
        batch_task_async(batch_size=2, items_to_batch=[[1, 2, 3]], task_func=send_async, task_arguments=["item"])
    )
    try:
        await asyncio.wait_for(all_started.wait(), timeout=5)
        if cancel_children:
            children[1].cancel("stop slow item")
            await asyncio.wait_for(cleanup_started.wait(), timeout=5)
            children[2].cancel("stop fast item")
        else:
            parent.cancel("stop batch")
        await asyncio.wait_for(cleanup_started.wait(), timeout=5)
        await asyncio.wait_for(fast_finished.wait(), timeout=5)

        assert children[1].cancelling() == 1
        assert not cleanup_finished.is_set()
        assert not parent.done()
        if cancel_again:
            parent.cancel("stop batch again")
            cancellation_delivered = asyncio.Event()
            asyncio.get_running_loop().call_soon(cancellation_delivered.set)
            await cancellation_delivered.wait()
            assert children[1].cancelling() == 1
            assert not parent.done()

        release_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(parent, timeout=5)
        assert cleanup_finished.is_set()
        assert calls == [1, 2]
        assert all(task.done() for task in children.values())
    finally:
        release_cleanup.set()
        if not parent.done():
            parent.cancel()
        await asyncio.gather(parent, *children.values(), return_exceptions=True)
