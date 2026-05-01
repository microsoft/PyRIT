# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

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
