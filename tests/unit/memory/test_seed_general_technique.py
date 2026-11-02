# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import SeedEntry
from pyrit.models import (
    AttackTechniqueSeedGroup,
    SeedIdentifier,
    SeedObjective,
    SeedPrompt,
    SeedRecord,
    SeedSimulatedConversation,
)


@pytest.mark.usefixtures("patch_central_database")
class TestSeedGeneralTechnique:
    """Tests for symmetric persistence of the is_general_technique flag."""

    async def test_seed_entry_persists_is_general_technique(self) -> None:
        general_prompt = SeedPrompt(
            value="General technique", data_type="text", is_general_technique=True, added_by="operator"
        )
        entry_general = SeedEntry(entry=general_prompt)
        assert entry_general.is_general_technique is True
        restored_general = entry_general.get_seed()
        assert isinstance(restored_general, SeedPrompt)
        assert restored_general.is_general_technique is True

        regular_prompt = SeedPrompt(
            value="Regular prompt", data_type="text", is_general_technique=False, added_by="operator"
        )
        entry_regular = SeedEntry(entry=regular_prompt)
        assert entry_regular.is_general_technique is False
        restored_regular = entry_regular.get_seed()
        assert isinstance(restored_regular, SeedPrompt)
        assert restored_regular.is_general_technique is False

        objective = SeedObjective(value="Target objective", added_by="operator")
        entry_obj = SeedEntry(entry=objective)
        assert entry_obj.is_general_technique is False
        restored_obj = entry_obj.get_seed()
        assert isinstance(restored_obj, SeedObjective)
        assert restored_obj.is_general_technique is False

        conversation = SeedSimulatedConversation(
            adversarial_chat_system_prompt=SeedPrompt(value="Adversary system", added_by="operator"),
            added_by="operator",
        )
        entry_conv = SeedEntry(entry=conversation)
        assert entry_conv.is_general_technique is True
        restored_conv = entry_conv.get_seed()
        assert isinstance(restored_conv, SeedSimulatedConversation)
        assert restored_conv.is_general_technique is True

    def test_seed_record_projects_is_general_technique(self) -> None:
        general_prompt = SeedPrompt(
            value="Technique prompt", data_type="text", is_general_technique=True, added_by="operator"
        )
        entry = SeedEntry(entry=general_prompt)
        record = entry.get_seed_record()
        assert isinstance(record, SeedRecord)
        assert record.is_general_technique is True
        assert "is_jinja_template" not in record.model_dump()

        regular_prompt = SeedPrompt(
            value="Regular prompt", data_type="text", is_general_technique=False, added_by="operator"
        )
        record_reg = SeedEntry(entry=regular_prompt).get_seed_record()
        assert record_reg.is_general_technique is False

    async def test_attack_technique_seed_group_sqlite_roundtrip_rebuilds_and_matches_identifier(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        technique_seed = SeedPrompt(value="Framing strategy", data_type="text", is_general_technique=True)
        group = AttackTechniqueSeedGroup(seeds=[technique_seed])

        await sqlite_instance.add_seeds_to_memory_async(seeds=group.seeds, added_by="operator")
        persisted_seed = group.seeds[0]
        original_identifier = SeedIdentifier.from_seed(persisted_seed)

        retrieved_seeds = await sqlite_instance.get_seeds_async()
        assert len(retrieved_seeds) == 1
        retrieved_seed = retrieved_seeds[0]
        assert isinstance(retrieved_seed, SeedPrompt)
        assert retrieved_seed.is_general_technique is True

        retrieved_identifier = SeedIdentifier.from_seed(retrieved_seed)
        assert retrieved_identifier.hash == original_identifier.hash
        assert retrieved_identifier.is_general_technique is True

        rebuilt_group = AttackTechniqueSeedGroup(seeds=retrieved_seeds)
        assert len(rebuilt_group.seeds) == 1
        assert rebuilt_group.seeds[0].is_general_technique is True

    def test_legacy_entry_defaults_to_false_for_prompt(self) -> None:
        entry = SeedEntry(entry=SeedPrompt(value="Legacy prompt", added_by="legacy"))
        entry.is_general_technique = False

        seed = entry.get_seed()
        assert isinstance(seed, SeedPrompt)
        assert seed.is_general_technique is False

        record = entry.get_seed_record()
        assert record.is_general_technique is False

    async def test_sequential_inserts_with_opposite_flags_both_stored_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        regular = SeedPrompt(value="Same value", dataset_name="flags", is_general_technique=False)
        technique = SeedPrompt(value="Same value", dataset_name="flags", is_general_technique=True)

        await sqlite_instance.add_seeds_to_memory_async(seeds=[regular], added_by="tester")
        await sqlite_instance.add_seeds_to_memory_async(seeds=[technique], added_by="tester")

        stored = await sqlite_instance.get_seeds_async(dataset_name="flags")
        assert len(stored) == 2
        flags = {s.is_general_technique for s in stored}
        assert flags == {False, True}

    async def test_sequential_inserts_technique_first_opposite_flags_both_stored_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        technique = SeedPrompt(value="Same value 2", dataset_name="flags2", is_general_technique=True)
        regular = SeedPrompt(value="Same value 2", dataset_name="flags2", is_general_technique=False)

        await sqlite_instance.add_seeds_to_memory_async(seeds=[technique], added_by="tester")
        await sqlite_instance.add_seeds_to_memory_async(seeds=[regular], added_by="tester")

        stored = await sqlite_instance.get_seeds_async(dataset_name="flags2")
        assert len(stored) == 2
        flags = {s.is_general_technique for s in stored}
        assert flags == {False, True}

    def test_seed_behavior_and_group_member_keys_include_general_technique(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        regular = SeedPrompt(value="Key test", is_general_technique=False)
        technique = SeedPrompt(value="Key test", is_general_technique=True)

        assert sqlite_instance._seed_behavior_key(regular) != sqlite_instance._seed_behavior_key(technique)
        assert sqlite_instance._seed_group_member_key(regular) != sqlite_instance._seed_group_member_key(technique)
