# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import patch

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_session import MemorySession
from pyrit.models import SeedPrompt


async def test_get_seed_dataset_summaries_avoids_metadata_row_multiplication(
    sqlite_instance: MemoryInterface,
):
    """Metadata projection returns one row per stored seed, not a modality/harm Cartesian product."""
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[
            SeedPrompt(value="prompt one", dataset_name="dataset", data_type="text", harm_categories=["harm"]),
            SeedPrompt(value="prompt two", dataset_name="dataset", data_type="reasoning", harm_categories=["harm"]),
            SeedPrompt(
                value="https://example.com/three", dataset_name="dataset", data_type="url", harm_categories=["harm"]
            ),
        ],
        added_by="tester",
    )

    original_execute = MemorySession.execute
    captured_rows: list[object] = []

    def execute(session, statement, *args, **kwargs):
        result = original_execute(session, statement, *args, **kwargs)
        original_all = result.all

        def all_rows():
            rows = original_all()
            captured_rows.extend(rows)
            return rows

        result.all = all_rows
        return result

    with patch.object(MemorySession, "execute", execute):
        summaries = await sqlite_instance.get_seed_dataset_summaries_async()

    assert len(captured_rows) == 3
    assert len(summaries) == 1
    assert summaries[0].seed_pieces == 3
    assert summaries[0].modalities == ("reasoning", "text", "url")
    assert summaries[0].harm_categories == ("harm",)
