# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pyrit.memory import MemoryInterface
from pyrit.models import (
    Seed,
    SeedDataset,
    SeedIdentifier,
    SeedObjective,
    SeedOrigin,
    SeedPrompt,
    SeedSimulatedConversation,
)


@pytest.mark.usefixtures("patch_central_database")
class TestSeedOrigin:
    @pytest.mark.parametrize("origin", list(SeedOrigin))
    @pytest.mark.parametrize("seed_type", [SeedPrompt, SeedObjective, SeedSimulatedConversation])
    async def test_roundtrip_async(
        self, sqlite_instance: MemoryInterface, origin: SeedOrigin, seed_type: type[Seed]
    ) -> None:
        if seed_type is SeedSimulatedConversation:
            seed = SeedSimulatedConversation(
                adversarial_chat_system_prompt=SeedPrompt(value="Generate a conversation."),
                origin=origin,
                dataset_name="origins",
            )
        else:
            seed = seed_type(value="An objective", origin=origin, dataset_name="origins")
        await sqlite_instance.add_seeds_to_memory_async(seeds=[seed], added_by="operator")
        [stored] = sqlite_instance.get_seeds(origin=origin)
        assert stored.origin is origin
        assert stored.added_by == "operator"
        assert type(stored) is seed_type
        assert type(stored).model_validate_json(stored.model_dump_json()).origin is origin
        await sqlite_instance.add_seeds_to_memory_async(seeds=[stored], added_by="operator")
        assert len(sqlite_instance.get_seeds(origin=origin)) == 1

    async def test_edit_preserves_origin_async(self, sqlite_instance: MemoryInterface) -> None:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value="Original", origin=SeedOrigin.GENERATED, dataset_name="editable")],
            added_by="operator",
        )
        [stored] = sqlite_instance.get_seeds(dataset_name="editable")
        stored.value = "Edited"
        await sqlite_instance.replace_seeds_for_dataset_async(
            dataset_name="editable", seeds=[stored], added_by="operator"
        )
        [edited] = sqlite_instance.get_seeds(dataset_name="editable")
        assert edited.value == "Edited"
        assert edited.origin is SeedOrigin.GENERATED

    def test_defaults_and_identity(self) -> None:
        seed = SeedObjective(value="An objective")
        identifier = SeedIdentifier.from_seed(seed)
        assert seed.origin is SeedOrigin.UNKNOWN
        seed.origin = SeedOrigin.USER
        assert SeedIdentifier.from_seed(seed) == identifier
        with pytest.raises(ValidationError):
            SeedObjective(value="An objective", origin="invalid")

    async def test_duplicate_does_not_overwrite_origin_async(self, sqlite_instance: MemoryInterface) -> None:
        for origin in (SeedOrigin.LOCAL, SeedOrigin.REMOTE):
            await sqlite_instance.add_seeds_to_memory_async(
                seeds=[SeedObjective(value="Same text", dataset_name="same", origin=origin)],
                added_by="operator",
            )
        [stored] = sqlite_instance.get_seeds()
        assert stored.origin is SeedOrigin.LOCAL

    async def test_filter_and_remove_async(self, sqlite_instance: MemoryInterface) -> None:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value=origin.value, origin=origin) for origin in SeedOrigin],
            added_by="operator",
        )
        assert len(sqlite_instance.get_seeds()) == len(SeedOrigin)
        assert sqlite_instance.remove_seeds_from_memory(origin=SeedOrigin.GENERATED) == 1
        assert not sqlite_instance.get_seeds(origin=SeedOrigin.GENERATED)
        assert len(sqlite_instance.get_seeds()) == len(SeedOrigin) - 1

    async def test_group_filter_preserves_membership_async(self, sqlite_instance: MemoryInterface) -> None:
        group_id = uuid4()
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[
                SeedObjective(value="Goal", origin=SeedOrigin.GENERATED, prompt_group_id=group_id),
                SeedPrompt(value="Message", origin=SeedOrigin.USER, prompt_group_id=group_id),
            ],
            added_by="operator",
        )
        [group] = sqlite_instance.get_seed_groups(origin=SeedOrigin.GENERATED)
        assert len(group.seeds) == 2
        assert sqlite_instance.remove_seed_groups_from_memory(origin=SeedOrigin.GENERATED) == 2

    def test_yaml_import_origin(self, tmp_path: Path) -> None:
        single = tmp_path / "seed.yaml"
        single.write_text("value: literal\norigin: local\n", encoding="utf-8")
        assert SeedObjective.from_yaml_file(single).origin is SeedOrigin.LOCAL
        dataset = tmp_path / "dataset.yaml"
        dataset.write_text("seeds:\n  - value: literal\n    seed_type: objective\n", encoding="utf-8")
        [seed] = SeedDataset.from_yaml_file(dataset).seeds
        assert seed.origin is SeedOrigin.LOCAL
        assert seed.is_jinja_template
