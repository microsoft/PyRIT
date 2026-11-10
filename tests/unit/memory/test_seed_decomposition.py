# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from pyrit.memory import MemoryInterface, SeedEntry
from pyrit.models import (
    AnswerMatches,
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


def _direct_tree(*, grouped: bool) -> list[SeedObjective]:
    def objective(value: str, parent: SeedObjective | None = None) -> SeedObjective:
        return SeedObjective(
            value=value,
            dataset_name=DATASET,
            parent_objective_id=parent.id if parent else None,
            prompt_group_id=uuid.uuid4() if grouped else None,
        )

    root = objective(ROOT_VALUE)
    first = objective("Recon", root)
    second = objective("Pull records", root)
    root.paths = (DecompositionPath(name="only", steps=(first.id, second.id)),)
    return [root, first, second]


async def _add(memory: MemoryInterface, dataset: SeedDataset) -> None:
    await memory.add_seed_datasets_to_memory_async(datasets=[dataset], added_by="tester")


@pytest.mark.usefixtures("patch_central_database")
class TestSeedDecompositionStorage:
    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    @pytest.mark.parametrize(
        "method",
        [
            "remove_seeds_from_memory_async",
            "remove_seed_groups_from_memory_async",
            "remove_seeds_from_memory",
            "remove_seed_groups_from_memory",
        ],
    )
    @pytest.mark.parametrize("value", [ROOT_VALUE, "Phish an admin", "Write a lure"])
    async def test_partial_tree_deletion_is_rejected_async(
        self, sqlite_instance: MemoryInterface, method: str, value: str
    ) -> None:
        dataset = _decomposed_dataset()
        await _add(sqlite_instance, dataset)
        standalone = SeedPrompt(value=value, dataset_name="independent")
        await sqlite_instance.add_seeds_to_memory_async(seeds=[standalone], added_by="tester")

        with pytest.raises(ValueError, match="whole decomposition tree"):
            remove = getattr(sqlite_instance, method)
            if method.endswith("_async"):
                await remove(value=value)
            else:
                remove(value=value)

        assert {seed.id for seed in await sqlite_instance.get_seeds_async()} == {
            seed.id for seed in [*dataset.seeds, standalone]
        }

    @pytest.mark.parametrize("groups", [False, True])
    async def test_whole_tree_deletion_and_reimport_async(self, sqlite_instance: MemoryInterface, groups: bool) -> None:
        dataset = _decomposed_dataset()
        await _add(sqlite_instance, dataset)
        remove = (
            sqlite_instance.remove_seed_groups_from_memory_async
            if groups
            else sqlite_instance.remove_seeds_from_memory_async
        )

        assert await remove(dataset_name=DATASET) == len(dataset.seeds)
        assert await sqlite_instance.get_seeds_async(dataset_name=DATASET) == []
        await _add(sqlite_instance, _decomposed_dataset())
        assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == len(dataset.seeds)

    async def test_deleting_nonroot_subtree_is_rejected_async(self, sqlite_instance: MemoryInterface) -> None:
        dataset = _decomposed_dataset()
        await _add(sqlite_instance, dataset)
        phish = next(objective for objective in dataset.objectives if objective.value == "Phish an admin")
        subtree = await sqlite_instance.get_seed_decomposition_async(objective_id=phish.id)

        with pytest.raises(ValueError, match="whole decomposition tree"):
            await sqlite_instance.remove_seeds_from_memory_async(value_sha256=[seed.value_sha256 for seed in subtree])

        assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == len(dataset.seeds)

    @pytest.mark.parametrize("dialect", [sqlite.dialect(), mssql.dialect()])
    def test_deletion_guard_binds_filter_once(self, dialect: Any) -> None:
        session = MagicMock(spec=Session)
        session.query.return_value.filter.return_value.first.return_value = None
        values = [str(index) for index in range(1200)]

        MemoryInterface._validate_decomposition_deletion(
            session=session, seed_ids=select(SeedEntry.id).where(SeedEntry.value_sha256.in_(values))
        )

        conditions = session.query.return_value.filter.call_args.args
        compiled = (
            select(SeedEntry.id)
            .where(*conditions)
            .compile(dialect=dialect, compile_kwargs={"render_postcompile": True})
        )
        assert len(compiled.params) == len(values)

    @pytest.mark.parametrize("prepared", [False, True])
    async def test_metadata_edits_update_stored_tree_without_duplicate_async(
        self, sqlite_instance: MemoryInterface, prepared: bool
    ) -> None:
        first = _decomposed_dataset()
        await _add(sqlite_instance, first)
        before = {seed.id: seed for seed in await sqlite_instance.get_seeds_async(dataset_name=DATASET)}
        incoming = _decomposed_dataset()
        for seed in incoming.seeds:
            seed.description = "Updated description"
            seed.name = "Updated name"
            seed.harm_categories = ["updated"]
            seed.authors = ["New author"]
            seed.groups = ["New group"]
            seed.source = "Updated source"
            seed.metadata = {"revision": 2}
        root = _root(incoming)
        root.paths = tuple(
            path.model_copy(update={"name": "Updated path", "description": "Updated path description"})
            for path in root.paths
        )

        if prepared:
            batch = await sqlite_instance.prepare_seed_batch_async(seeds=incoming.seeds, added_by="new importer")
            await sqlite_instance.add_prepared_seed_batch_async(batch)
        else:
            await sqlite_instance.add_seeds_to_memory_async(seeds=incoming.seeds, added_by="new importer")
            assert [seed.id for seed in incoming.seeds] == [seed.id for seed in first.seeds]
            assert [seed.prompt_group_id for seed in incoming.seeds] == [seed.prompt_group_id for seed in first.seeds]

        after = {seed.id: seed for seed in await sqlite_instance.get_seeds_async(dataset_name=DATASET)}
        assert set(after) == set(before)
        for seed_id, seed in after.items():
            assert seed.description == "Updated description"
            assert seed.name == "Updated name"
            assert seed.harm_categories == ["updated"]
            assert seed.authors == ["New author"]
            assert seed.groups == ["New group"]
            assert seed.source == "Updated source"
            assert seed.metadata == {"revision": 2}
            assert seed.date_added == before[seed_id].date_added
            assert seed.added_by == before[seed_id].added_by
            assert seed.origin == before[seed_id].origin
            assert seed.prompt_group_id == before[seed_id].prompt_group_id
        stored_root = after[_root(first).id]
        assert [path.id for path in stored_root.paths] == [path.id for path in _root(first).paths]
        assert [path.steps for path in stored_root.paths] == [path.steps for path in _root(first).paths]
        assert all(path.name == "Updated path" for path in stored_root.paths)
        assert all(path.description == "Updated path description" for path in stored_root.paths)

    async def test_metadata_update_rolls_back_without_remapping_caller_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        await _add(sqlite_instance, _decomposed_dataset())
        before = await sqlite_instance.get_seeds_async(dataset_name=DATASET)
        incoming = _decomposed_dataset()
        incoming_ids = [seed.id for seed in incoming.seeds]
        for seed in incoming.seeds:
            seed.description = "Not committed"
        with patch.object(Session, "commit", side_effect=SQLAlchemyError("update failed")):
            with pytest.raises(SQLAlchemyError, match="update failed"):
                await sqlite_instance.add_seeds_to_memory_async(seeds=incoming.seeds, added_by="tester")

        after = await sqlite_instance.get_seeds_async(dataset_name=DATASET)
        assert {seed.id: seed.model_dump() for seed in after} == {seed.id: seed.model_dump() for seed in before}
        assert [seed.id for seed in incoming.seeds] == incoming_ids

    @pytest.mark.parametrize("change", ["role", "sequence", "schema", "conditions", "path_steps"])
    async def test_execution_edits_keep_trees_separate_async(
        self, sqlite_instance: MemoryInterface, change: str
    ) -> None:
        first = _decomposed_dataset()
        await _add(sqlite_instance, first)
        incoming = _decomposed_dataset()
        prompt = next(seed for seed in incoming.seeds if isinstance(seed, SeedPrompt))
        if change == "role":
            prompt.role = "assistant"
        elif change == "sequence":
            prompt.sequence += 1
        elif change == "schema":
            prompt.response_json_schema = {"type": "object"}
        elif change == "conditions":
            incoming.objectives[-1].conditions = (AnswerMatches(correct_answer="expected"),)
        else:
            root = _root(incoming)
            root.paths = tuple(path.model_copy(update={"steps": tuple(reversed(path.steps))}) for path in root.paths)

        await sqlite_instance.add_seeds_to_memory_async(seeds=incoming.seeds, added_by="tester")

        assert len(await sqlite_instance.get_seeds_async(dataset_name=DATASET)) == 2 * len(first.seeds)

    @pytest.mark.parametrize("batch_size", [1, 1000])
    async def test_decomposition_orders_siblings_by_date_then_id_async(
        self, sqlite_instance: MemoryInterface, batch_size: int
    ) -> None:
        root = SeedObjective(value=ROOT_VALUE, dataset_name=DATASET)
        later = SeedObjective(
            id=uuid.UUID(int=3),
            value="Later",
            dataset_name=DATASET,
            parent_objective_id=root.id,
            date_added=datetime(2026, 1, 2, tzinfo=UTC),
        )
        tied = [
            SeedObjective(
                id=uuid.UUID(int=index),
                value=f"Tied {index}",
                dataset_name=DATASET,
                parent_objective_id=root.id,
                date_added=later.date_added - timedelta(days=1),
            )
            for index in (2, 1)
        ]
        root.paths = (DecompositionPath(steps=(later.id, tied[0].id, tied[1].id)),)
        await sqlite_instance.add_seeds_to_memory_async(seeds=[root, later, *tied], added_by="tester")

        with patch.object(sqlite_instance, "_MAX_BIND_VARS", batch_size):
            subtree = await sqlite_instance.get_seed_decomposition_async(objective_id=root.id)

        assert [seed.id for seed in subtree] == [root.id, tied[1].id, tied[0].id, later.id]
        assert subtree[0].paths == root.paths

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
        tree = build_objective_decomposition_tree(subtree, root_id=phish.id)
        values = {objective.id: objective.value for objective in subtree}
        assert [(values[node_id], depth) for node_id, depth in tree.walk(phish.id)] == [
            ("Phish an admin", 0),
            ("Write a lure", 1),
        ]
        assert subtree[0].parent_objective_id == _root(dataset).id

    async def test_get_seed_decomposition_unknown_id_is_empty_async(self, sqlite_instance: MemoryInterface) -> None:
        assert await sqlite_instance.get_seed_decomposition_async(objective_id=uuid.uuid4()) == []

    async def test_tree_spanning_datasets_is_rejected_async(self, sqlite_instance: MemoryInterface) -> None:
        dataset = _decomposed_dataset()
        child = next(objective for objective in dataset.objectives if objective.value == "Pull customer records")
        child.dataset_name = "other"

        with pytest.raises(ValueError, match="one dataset"):
            await _add(sqlite_instance, dataset)

        assert await sqlite_instance.get_seeds_async(dataset_name=DATASET) == []
        assert await sqlite_instance.get_seeds_async(dataset_name="other") == []

    async def test_tree_prompt_in_another_dataset_is_rejected_async(self, sqlite_instance: MemoryInterface) -> None:
        dataset = _decomposed_dataset()
        prompt = next(seed for seed in dataset.seeds if isinstance(seed, SeedPrompt))
        prompt.dataset_name = "other"

        with pytest.raises(ValueError, match="one dataset"):
            await _add(sqlite_instance, dataset)

        assert await sqlite_instance.get_seeds_async(dataset_name=DATASET) == []

    @pytest.mark.parametrize(("stored_grouped", "incoming_grouped"), [(True, False), (False, True)])
    async def test_reloaded_tree_takes_stored_group_ids_async(
        self, sqlite_instance: MemoryInterface, stored_grouped: bool, incoming_grouped: bool
    ) -> None:
        stored = _direct_tree(grouped=stored_grouped)
        await sqlite_instance.add_seeds_to_memory_async(seeds=stored, added_by="tester")
        incoming = _direct_tree(grouped=incoming_grouped)

        await sqlite_instance.add_seeds_to_memory_async(seeds=incoming, added_by="tester")

        rows = {seed.id: seed for seed in await sqlite_instance.get_seeds_async(dataset_name=DATASET)}
        assert len(rows) == len(stored)
        assert [seed.id for seed in incoming] == [seed.id for seed in stored]
        assert [seed.prompt_group_id for seed in incoming] == [rows[seed.id].prompt_group_id for seed in incoming]

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
