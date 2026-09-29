# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from uuid import uuid4

import pytest

from pyrit.memory import AzureSQLMemory
from pyrit.models import SeedObjective, SeedOrigin


@pytest.mark.run_only_if_all_tests
async def test_seed_origins_roundtrip_azure_sql_async(azuresql_instance: AzureSQLMemory) -> None:
    dataset_name = f"seed_origin_test_{uuid4().hex}"
    try:
        await azuresql_instance.add_seeds_to_memory_async(
            seeds=[
                SeedObjective(value=origin.value, dataset_name=dataset_name, origin=origin) for origin in SeedOrigin
            ],
            added_by="seed_origin_integration",
        )
        for origin in SeedOrigin:
            [seed] = azuresql_instance.get_seeds(dataset_name=dataset_name, origin=origin)
            assert seed.origin is origin
    finally:
        azuresql_instance.remove_seeds_from_memory(dataset_name=dataset_name)
