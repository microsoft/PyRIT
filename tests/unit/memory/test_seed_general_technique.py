# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from uuid import uuid4

import pytest

from pyrit.memory import MemoryInterface
from pyrit.models import (
    AttackTechniqueSeedGroup,
    SeedIdentifier,
    SeedPrompt,
    SeedRecord,
    SeedSimulatedConversation,
)


@pytest.mark.usefixtures("patch_central_database")
class TestSeedGeneralTechnique:
    async def test_attack_technique_seed_group_roundtrip_async(self, sqlite_instance: MemoryInterface) -> None:
        group = AttackTechniqueSeedGroup.from_system_prompt("System attack prompt.")
        seed = group.seeds[0]
        assert seed.is_general_technique is True

        # Prepare and verify identity
        await sqlite_instance.add_seeds_to_memory_async(seeds=group.seeds, added_by="operator")
        identifier_before = SeedIdentifier.from_seed(seed)

        [retrieved_seed] = await sqlite_instance.get_seeds_async(prompt_group_ids=[seed.prompt_group_id])
        assert isinstance(retrieved_seed, SeedPrompt)
        assert retrieved_seed.is_general_technique is True
        identifier_after = SeedIdentifier.from_seed(retrieved_seed)

        assert identifier_before.hash == identifier_after.hash

        # Reconstructing group from retrieved seeds must succeed without validation error
        rebuilt_group = AttackTechniqueSeedGroup(seeds=[retrieved_seed])
        assert len(rebuilt_group.seeds) == 1
        assert rebuilt_group.seeds[0].is_general_technique is True

    async def test_seed_simulated_conversation_roundtrip_async(self, sqlite_instance: MemoryInterface) -> None:
        seed = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="Generate a conversation."),
            dataset_name="techniques",
        )
        assert seed.is_general_technique is True

        await sqlite_instance.add_seeds_to_memory_async(seeds=[seed], added_by="operator")
        identifier_before = SeedIdentifier.from_seed(seed)

        [retrieved] = await sqlite_instance.get_seeds_async(dataset_name="techniques")
        assert isinstance(retrieved, SeedSimulatedConversation)
        assert retrieved.is_general_technique is True

        identifier_after = SeedIdentifier.from_seed(retrieved)
        assert identifier_before.hash == identifier_after.hash

    async def test_filter_by_is_general_technique_async(self, sqlite_instance: MemoryInterface) -> None:
        general_seed = SeedPrompt(value="General technique seed", is_general_technique=True, dataset_name="mixed")
        specific_seed = SeedPrompt(value="Specific objective prompt", is_general_technique=False, dataset_name="mixed")

        await sqlite_instance.add_seeds_to_memory_async(seeds=[general_seed, specific_seed], added_by="operator")

        all_seeds = await sqlite_instance.get_seeds_async(dataset_name="mixed")
        assert len(all_seeds) == 2

        general_only = await sqlite_instance.get_seeds_async(dataset_name="mixed", is_general_technique=True)
        assert len(general_only) == 1
        assert general_only[0].value == "General technique seed"
        assert general_only[0].is_general_technique is True

        specific_only = await sqlite_instance.get_seeds_async(dataset_name="mixed", is_general_technique=False)
        assert len(specific_only) == 1
        assert specific_only[0].value == "Specific objective prompt"
        assert specific_only[0].is_general_technique is False

    async def test_remove_by_is_general_technique_async(self, sqlite_instance: MemoryInterface) -> None:
        general_seed = SeedPrompt(value="General prompt", is_general_technique=True, dataset_name="removal")
        specific_seed = SeedPrompt(value="Specific prompt", is_general_technique=False, dataset_name="removal")

        await sqlite_instance.add_seeds_to_memory_async(seeds=[general_seed, specific_seed], added_by="operator")

        removed_count = await sqlite_instance.remove_seeds_from_memory_async(
            dataset_name="removal", is_general_technique=True
        )
        assert removed_count == 1

        remaining = await sqlite_instance.get_seeds_async(dataset_name="removal")
        assert len(remaining) == 1
        assert remaining[0].value == "Specific prompt"
        assert remaining[0].is_general_technique is False

    async def test_seed_groups_filter_by_is_general_technique_async(self, sqlite_instance: MemoryInterface) -> None:
        group_id_1 = uuid4()
        group_id_2 = uuid4()

        group1_seeds = [
            SeedPrompt(
                value="General turn 1",
                is_general_technique=True,
                prompt_group_id=group_id_1,
                sequence=0,
                role="user",
            ),
            SeedPrompt(
                value="General turn 2",
                is_general_technique=True,
                prompt_group_id=group_id_1,
                sequence=1,
                role="user",
            ),
        ]
        group2_seeds = [
            SeedPrompt(
                value="Regular turn 1",
                is_general_technique=False,
                prompt_group_id=group_id_2,
                sequence=0,
                role="user",
            ),
            SeedPrompt(
                value="Regular turn 2",
                is_general_technique=False,
                prompt_group_id=group_id_2,
                sequence=1,
                role="user",
            ),
        ]

        await sqlite_instance.add_seeds_to_memory_async(seeds=group1_seeds + group2_seeds, added_by="operator")

        general_groups = await sqlite_instance.get_seed_groups_async(is_general_technique=True)
        assert len(general_groups) == 1
        assert general_groups[0].seeds[0].prompt_group_id == group_id_1

        non_general_groups = await sqlite_instance.get_seed_groups_async(is_general_technique=False)
        assert len(non_general_groups) == 1
        assert non_general_groups[0].seeds[0].prompt_group_id == group_id_2

    async def test_remove_seed_groups_by_is_general_technique_async(self, sqlite_instance: MemoryInterface) -> None:
        group_id_1 = uuid4()
        group_id_2 = uuid4()

        group1_seeds = [
            SeedPrompt(
                value="Delete me general",
                is_general_technique=True,
                prompt_group_id=group_id_1,
                sequence=0,
                role="user",
            ),
            SeedPrompt(
                value="Delete me companion",
                is_general_technique=True,
                prompt_group_id=group_id_1,
                sequence=1,
                role="user",
            ),
        ]
        group2_seeds = [
            SeedPrompt(
                value="Keep me regular",
                is_general_technique=False,
                prompt_group_id=group_id_2,
                sequence=0,
                role="user",
            ),
            SeedPrompt(
                value="Keep me companion",
                is_general_technique=False,
                prompt_group_id=group_id_2,
                sequence=1,
                role="user",
            ),
        ]

        await sqlite_instance.add_seeds_to_memory_async(seeds=group1_seeds + group2_seeds, added_by="operator")

        removed = await sqlite_instance.remove_seed_groups_from_memory_async(is_general_technique=True)
        assert removed == 2

        remaining_groups = await sqlite_instance.get_seed_groups_async()
        assert len(remaining_groups) == 1
        assert remaining_groups[0].seeds[0].prompt_group_id == group_id_2

    async def test_seed_record_projection(self, sqlite_instance: MemoryInterface) -> None:
        seed = SeedPrompt(value="Record projection test", is_general_technique=True, dataset_name="projection")
        await sqlite_instance.add_seeds_to_memory_async(seeds=[seed], added_by="operator")

        examples = await sqlite_instance.get_seed_example_async(dataset_name="projection", example_id=seed.id)
        assert len(examples) == 1
        record = examples[0]
        assert isinstance(record, SeedRecord)
        assert record.is_general_technique is True
