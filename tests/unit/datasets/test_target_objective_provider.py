# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from unit.mocks import MockPromptTarget

from pyrit.datasets import SeedDatasetProvider, TargetObjectiveProvider
from pyrit.executor.promptgen.target_objective_generator import TargetObjectiveGeneratorResult
from pyrit.memory import MemoryInterface
from pyrit.models import JsonResponseConfig, Message, SeedObjective, SeedOrigin, SeedPrompt


@pytest.mark.usefixtures("patch_central_database")
class TestTargetObjectiveProvider:
    async def test_seed_system_prompt_reaches_generator_async(self) -> None:
        target = MockPromptTarget()
        prompt = SeedPrompt(
            value="Generate astronomy objectives.",
            response_json_schema={
                "type": "object",
                "properties": {"objectives": {"type": "array", "items": {"type": "string"}}},
                "required": ["objectives"],
                "additionalProperties": False,
            },
        )
        provider = TargetObjectiveProvider(
            dataset_name="astronomy", target=target, instructions="Test", count=1, system_prompt=prompt
        )
        response = Message.from_prompt(prompt='{"objectives": ["Describe an orbit."]}', role="assistant")
        with patch.object(
            provider._generator._normalizer, "send_prompt_async", new_callable=AsyncMock, return_value=response
        ) as send:
            dataset = await provider.fetch_dataset_async()

        assert target.system_prompt == prompt.value
        config = JsonResponseConfig.from_metadata(
            metadata=send.call_args.kwargs["message"].message_pieces[0].prompt_metadata
        )
        assert config.json_schema == prompt.response_json_schema
        assert dataset.seeds[0].value == "Describe an orbit."

    async def test_provider_and_metadata_roundtrip_async(self, sqlite_instance: MemoryInterface) -> None:
        provider = TargetObjectiveProvider(
            dataset_name="generated",
            target=MockPromptTarget(),
            instructions="Test",
            count=10,
            harm_categories=["test"],
        )
        result = TargetObjectiveGeneratorResult(
            objectives=[f"Objective {index}" for index in range(10)],
            conversation_id=str(uuid4()),
        )
        with patch.object(provider._generator, "execute_async", new_callable=AsyncMock, return_value=result) as execute:
            dataset = await provider.fetch_dataset_async()
        execute.assert_awaited_once_with(instructions="Test", count=10, harm_categories=["test"])
        assert not await sqlite_instance.get_seeds_async()
        assert len(dataset.seeds) == 10
        assert all(isinstance(seed, SeedObjective) for seed in dataset.seeds)
        assert all(seed.dataset_name == "generated" and seed.origin is SeedOrigin.GENERATED for seed in dataset.seeds)
        assert all(not seed.is_jinja_template and seed.added_by is None for seed in dataset.seeds)
        await sqlite_instance.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="operator")
        stored = await sqlite_instance.get_seeds_async(dataset_name="generated", origin=SeedOrigin.GENERATED)
        assert len(stored) == 10
        assert all(seed.metadata == {"generation_conversation_id": result.conversation_id} for seed in stored)
        by_conversation = await sqlite_instance.get_seeds_async(
            metadata={"generation_conversation_id": result.conversation_id}
        )
        assert len(by_conversation) == 10
        assert stored[0].added_by == "operator"

    async def test_cache_flag_does_not_reuse_output_async(self) -> None:
        provider = TargetObjectiveProvider(
            dataset_name="generated", target=MockPromptTarget(), instructions="Test", count=1
        )
        response = Message.from_prompt(prompt='{"objectives": ["literal {{ untouched }}"]}', role="assistant")
        with patch.object(
            provider._generator._normalizer, "send_prompt_async", new_callable=AsyncMock, return_value=response
        ) as send:
            first = await provider.fetch_dataset_async(cache=True)
            second = await provider.fetch_dataset_async(cache=False)
        assert send.call_count == 2
        assert first.seeds[0].id != second.seeds[0].id
        assert first.seeds[0].value == "literal {{ untouched }}"
        assert first.seeds[0].metadata is not None
        assert second.seeds[0].metadata is not None
        assert (
            first.seeds[0].metadata["generation_conversation_id"]
            != second.seeds[0].metadata["generation_conversation_id"]
        )

    async def test_discovery_excludes_generator_async(self) -> None:
        with patch.object(TargetObjectiveProvider, "fetch_dataset_async", new_callable=AsyncMock) as fetch:
            providers = SeedDatasetProvider.get_all_providers()
            assert TargetObjectiveProvider not in providers.values()
            assert "TargetObjectiveProvider" not in providers
            names = await SeedDatasetProvider.get_all_dataset_names_async()
        assert names
        fetch.assert_not_called()

    async def test_failure_does_not_store_seed_rows_async(self, sqlite_instance: MemoryInterface) -> None:
        provider = TargetObjectiveProvider(
            dataset_name="generated", target=MockPromptTarget(), instructions="Test", count=1
        )
        with (
            patch.object(provider._generator, "execute_async", side_effect=RuntimeError("Failed")),
            pytest.raises(RuntimeError, match="Failed"),
        ):
            await provider.fetch_dataset_async()
        assert not await sqlite_instance.get_seeds_async()

    @pytest.mark.parametrize("name", ["", "  "])
    def test_rejects_blank_name(self, name: str) -> None:
        with pytest.raises(ValueError, match="dataset_name"):
            TargetObjectiveProvider(dataset_name=name, target=MockPromptTarget(), instructions="Test", count=1)
