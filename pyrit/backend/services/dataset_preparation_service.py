# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Runtime-owned scenario provider work, independent of HTTP and page lifetimes."""

from functools import lru_cache

from pyrit.memory import CentralMemory
from pyrit.scenario.core.dataset_preparation import DatasetPreparation


@lru_cache(maxsize=1)
def get_dataset_preparation_service() -> DatasetPreparation:
    """Return the preparation owner cleared before runtime memory replacement."""
    return DatasetPreparation(CentralMemory.get_memory_instance())


def has_active_dataset_preparation() -> bool:
    """Return owned work without constructing a service during runtime inspection."""
    return bool(
        get_dataset_preparation_service.cache_info().currsize and get_dataset_preparation_service().has_active_work
    )


async def close_dataset_preparation_async() -> None:
    """Drain provider work before releasing the runtime's memory reference."""
    if get_dataset_preparation_service.cache_info().currsize:
        await get_dataset_preparation_service().close_async()
        get_dataset_preparation_service.cache_clear()
