# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from typing import Any

import pytest

from pyrit.memory import MemoryInterface, SeedEntry
from pyrit.models import (
    DecompositionPath,
    SeedDataset,
    SeedObjective,
    SeedPrompt,
    build_objective_decomposition_tree,
)

DATASET = "decomposition"
ROOT_VALUE = "Exfiltrate customer data"


def _decomposed_dataset(*, exfil_value: str = "Pull customer records", dataset: str = DATASET) -> SeedDataset:
    data: dict[str, Any] = {
        "dataset_name": dataset,
        "seeds": [
            {
                "seed_type": "objective",
                "value": ROOT_VALUE,
                "sub_objectives": [
                    {"key": "recon", "value": "Enumerate internal services"},
                    {"key": "creds", "value": "Obtain admin credentials"},
                    {
                        "key": "phish",
                        "value": "Phish an admin",
                        "prompt_group_alias": "phishing",
                        "sub_objectives": [{"key": "lure", "value": "Write a lure"}],
                        "paths": [["lure"]],
                    },
                    {"key": "exfil", "value": exfil_value},
                ],
                "paths": [
                    {"name": "Credential theft", "steps": ["recon", "creds", "exfil"]},
                    {"name": "Phishing", "steps": ["recon", "phish", "exfil"]},
                ],
            },
            {"value": "Reset your password here.", "prompt_group_alias": "phishing"},
        ],
    }
    return SeedDataset.from_dict(data)


def _root(dataset: SeedDataset) -> SeedObjective:
    return next(objective for objective in dataset.objectives if objective.value == ROOT_VALUE)


async def _add(memory: MemoryInterface, dataset: SeedDataset) -> None:
    await memory.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="tester")


@pytest.mark.usefixtures("patch_central_database")
class TestSeedDecompositionStorage:
    async def test_round_trip_preserves_parents_and_paths_async(self, sqlite_instance: MemoryInterface) -> None:
        dataset = _decomposed_dataset()
        await _add(sqlite_instance, dataset)

        stored = await sqlite_instance.get_seeds_async(dataset_name=DATASET, seed_type="objective")

        expected = {(seed.id, seed.parent_objective_id, seed.paths) for seed in dataset.objectives}
        assert {(seed.id, seed.parent_objective_id, seed.paths) for seed in stored} == expected

    async def test_get_seed_decomposition_returns_subtree_async(self, sqlite_instance: MemoryInterface) -> None:
        dataset = _decomposed_dataset()
        await _add(sqlite_instance, dataset)
        root = _root(dataset)

        subtree = await sqlite_instance.get_seed_decomposition_async(objective_id=root.id)

        tree = build_objective_decomposition_tree(subtree)
        assert subtree[0].id == root.id
        assert set(tree.get_subtree(root.id)) == {objective.id for objective in dataset.objectives}

    async def test_get_seed_decomposition_starts_at_any_level_async(self, sqlite_instance: MemoryInterface) -> None:
        dataset = _decomposed_dataset()
        await _add(sqlite_instance, dataset)
        phish = next(objective for objective in dataset.objectives if objective.value == "Phish an admin")

        subtree = await sqlite_instance.get_seed_decomposition_async(objective_id=phish.id)

        assert [objective.value for objective in subtree] == ["Phish an admin", "Write a lure"]

    async def test_get_seed_decomposition_unknown_id_is_empty_async(self, sqlite_instance: MemoryInterface) -> None:
        assert await sqlite_instance.get_seed_decomposition_async(objective_id=uuid.uuid4()) == []

    async def test_reloading_same_tree_does_not_duplicate_and_remaps_ids_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        first = _decomposed_dataset()
        await _add(sqlite_instance, first)
        second = _decomposed_dataset()

        await _add(sqlite_instance, second)

        stored = await sqlite_instance.get_seeds_async(dataset_name=DATASET)
        assert len(stored) == len(first.seeds)
        assert [seed.id for seed in second.seeds] == [seed.id for seed in first.seeds]
        assert [seed.prompt_group_id for seed in second.seeds] == [seed.prompt_group_id for seed in first.seeds]
        assert _root(second).paths == _root(first).paths

    @pytest.mark.parametrize("verify_order", [("x1", "x2"), ("x2", "x1")])
    async def test_identical_siblings_deduplicate_in_any_order_async(
        self, sqlite_instance: MemoryInterface, verify_order: tuple[str, str]
    ) -> None:
        def dataset(order: tuple[str, str]) -> SeedDataset:
            children = {"x1": {"key": "x1", "value": "Verify"}, "x2": {"key": "x2", "value": "Verify"}}
            return SeedDataset.from_dict(
                {
                    "dataset_name": DATASET,
                    "seeds": [
                        {
                            "seed_type": "objective",
                            "value": ROOT_VALUE,
                            "sub_objectives": [
                                {"key": "a", "value": "Recon"},
                                children[order[0]],
                                {"key": "b", "value": "Phish"},
                                children[order[1]],
                            ],
                            "paths": [["a", "x1"], ["b", "x2"]],
                        }
                    ],
                }
            )

        await _add(sqlite_instance, dataset(("x1", "x2")))

        await _add(sqlite_instance, dataset(verify_order))

        assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == 5

    async def test_stored_tree_read_back_in_any_order_is_not_copied_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        await _add(sqlite_instance, _decomposed_dataset())
        stored = await sqlite_instance.get_seeds_async(dataset_name=DATASET)

        await sqlite_instance.add_seeds_to_memory_async(seeds=list(reversed(stored)), added_by="tester")

        assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == len(stored)

    async def test_changed_tree_is_stored_separately_async(self, sqlite_instance: MemoryInterface) -> None:
        await _add(sqlite_instance, _decomposed_dataset())

        await _add(sqlite_instance, _decomposed_dataset(exfil_value="Download the customer table"))

        roots = [
            seed
            for seed in await sqlite_instance.get_seeds_async(dataset_name=DATASET, seed_type="objective")
            if seed.value == ROOT_VALUE
        ]
        assert len(roots) == 2

    async def test_same_tree_in_another_dataset_is_stored_separately_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        await _add(sqlite_instance, _decomposed_dataset())

        await _add(sqlite_instance, _decomposed_dataset(dataset="other"))

        assert len(await sqlite_instance.get_seeds_async(dataset_name="other")) == 7

    async def test_standalone_objective_is_not_merged_into_tree_member_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        await _add(sqlite_instance, _decomposed_dataset())

        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value="Enumerate internal services", dataset_name=DATASET)], added_by="tester"
        )

        stored = await sqlite_instance.get_seeds_async(value="Enumerate internal services", dataset_name=DATASET)
        assert sorted(seed.parent_objective_id is None for seed in stored) == [False, True]

    async def test_tree_member_is_not_merged_into_standalone_objective_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value="Enumerate internal services", dataset_name=DATASET)], added_by="tester"
        )

        await _add(sqlite_instance, _decomposed_dataset())

        stored = await sqlite_instance.get_seeds_async(value="Enumerate internal services", dataset_name=DATASET)
        assert len(stored) == 2

    async def test_standalone_prompt_is_not_merged_into_tree_prompt_group_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        await _add(sqlite_instance, _decomposed_dataset())

        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedPrompt(value="Reset your password here.", dataset_name=DATASET)], added_by="tester"
        )

        assert len(await sqlite_instance.get_seeds_async(value="Reset your password here.")) == 2

    async def test_independent_seeds_still_deduplicate_alongside_trees_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        await _add(sqlite_instance, _decomposed_dataset())
        standalone = SeedObjective(value="Standalone", dataset_name=DATASET)

        await sqlite_instance.add_seeds_to_memory_async(seeds=[standalone], added_by="tester")
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value="Standalone", dataset_name=DATASET)], added_by="tester"
        )

        assert len(await sqlite_instance.get_seeds_async(value="Standalone")) == 1

    async def test_tree_reusing_stored_ids_is_copied_with_new_ids_async(self, sqlite_instance: MemoryInterface) -> None:
        dataset = _decomposed_dataset()
        await _add(sqlite_instance, dataset)
        stored = await sqlite_instance.get_seeds_async(dataset_name=DATASET)
        stored_ids = {seed.id for seed in stored}
        changed = [
            seed.model_copy(update={"value": "Changed goal", "value_sha256": None})
            if isinstance(seed, SeedObjective) and seed.value == ROOT_VALUE
            else seed
            for seed in stored
        ]

        await sqlite_instance.add_seeds_to_memory_async(seeds=changed, added_by="tester")

        all_seeds = await sqlite_instance.get_seeds_async(dataset_name=DATASET)
        assert len(all_seeds) == 2 * len(stored)
        copied = [seed for seed in changed if isinstance(seed, SeedObjective)]
        assert not {seed.id for seed in changed} & stored_ids
        assert build_objective_decomposition_tree(copied).roots == (
            next(seed.id for seed in copied if seed.value == "Changed goal"),
        )
        original_root = _root(dataset)
        assert len(await sqlite_instance.get_seed_decomposition_async(objective_id=original_root.id)) == 6

    async def test_incomplete_tree_is_rejected_async(self, sqlite_instance: MemoryInterface) -> None:
        child = SeedObjective(value="child", dataset_name=DATASET, parent_objective_id=uuid.uuid4())

        with pytest.raises(ValueError, match="is missing"):
            await sqlite_instance.add_seeds_to_memory_async(seeds=[child], added_by="tester")

    async def test_replace_rejects_incomplete_tree_async(self, sqlite_instance: MemoryInterface) -> None:
        parent = SeedObjective(value="parent", dataset_name=DATASET)
        child = SeedObjective(value="child", dataset_name=DATASET, parent_objective_id=parent.id)
        parent.paths = (DecompositionPath(steps=(child.id, uuid.uuid4())),)

        with pytest.raises(ValueError, match="not its direct children"):
            await sqlite_instance.replace_seeds_for_dataset_async(
                dataset_name=DATASET, seeds=[parent, child], added_by="tester"
            )

    def test_seed_record_exposes_decomposition(self) -> None:
        parent = SeedObjective(value="parent", added_by="tester")
        child = SeedObjective(value="child", added_by="tester", parent_objective_id=parent.id)
        parent.paths = (DecompositionPath(name="only", steps=(child.id,)),)

        assert SeedEntry(entry=parent).get_seed_record().paths == parent.paths
        assert SeedEntry(entry=child).get_seed_record().parent_objective_id == parent.id

    def test_prompt_entry_with_decomposition_fields_is_rejected(self) -> None:
        entry = SeedEntry(entry=SeedPrompt(value="prompt", added_by="tester"))
        entry.parent_objective_id = uuid.uuid4()

        with pytest.raises(ValueError, match="Only objective seeds"):
            entry.get_seed()
