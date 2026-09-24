# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import String, select, update
from sqlalchemy.exc import SQLAlchemyError

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import SeedEntry, SeedIdentifierEntry
from pyrit.models import AnswerMatches, MatchesObjective, SeedGroup, SeedIdentifier, SeedObjective, SeedPrompt
from pyrit.models.identifiers.seed_identifier import compute_seed_group_hash


def _objective(*, answer: str = "Paris", dataset: str | None = "questions") -> SeedObjective:
    return SeedObjective(
        value="What is the capital?",
        dataset_name=dataset,
        conditions=(AnswerMatches(correct_answer=answer, correct_answer_label="A"),),
    )


@pytest.mark.usefixtures("patch_central_database")
class TestSeedConditions:
    @pytest.mark.parametrize("copy_to_new_group", [False, True])
    async def test_reloaded_companion_is_retained_without_duplicate_id_async(
        self, sqlite_instance: MemoryInterface, copy_to_new_group: bool
    ) -> None:
        original = SeedGroup(seeds=[SeedPrompt(value="Choose an answer.")])
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[original], added_by="tester")
        [prompt] = await sqlite_instance.get_seeds_async()
        original_id = prompt.id
        original_group_id = prompt.prompt_group_id
        group_id = uuid.uuid4() if copy_to_new_group else original_group_id
        prompt.prompt_group_id = group_id
        objective = _objective()
        objective.prompt_group_id = group_id
        missing = SeedPrompt(value="Additional context.", prompt_group_id=group_id)
        group = SeedGroup(seeds=[prompt, objective, missing])

        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")

        [restored] = await sqlite_instance.get_seed_groups_async(prompt_group_ids=[group_id])
        assert len(restored.seeds) == 3
        assert restored.scoring_expectation.conditions == objective.conditions
        companion = next(seed for seed in restored.seeds if seed.value == prompt.value)
        assert (companion.id != original_id) is copy_to_new_group
        assert prompt.id == companion.id
        assert {seed.id for seed in group.seeds} == {seed.id for seed in restored.seeds}
        [original_prompt] = [
            seed
            for seed in (await sqlite_instance.get_seeds_async(prompt_group_ids=[original_group_id]))
            if seed.id == original_id
        ]
        assert original_prompt.prompt_group_id == original_group_id
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")
        assert len(await sqlite_instance.get_seeds_async()) == (4 if copy_to_new_group else 3)

    async def test_copied_seed_id_changes_only_after_successful_insert_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        original = SeedGroup(seeds=[SeedPrompt(value="Choose an answer.")])
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[original], added_by="tester")
        [prompt] = await sqlite_instance.get_seeds_async()
        original_id = prompt.id
        prompt.prompt_group_id = uuid.uuid4()
        group = SeedGroup(seeds=[prompt, _objective()])
        with (
            patch.object(sqlite_instance, "_insert_entries", side_effect=SQLAlchemyError("insertion failed")),
            pytest.raises(SQLAlchemyError, match="insertion failed"),
        ):
            await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")

        assert prompt.id == original_id
        assert len(await sqlite_instance.get_seeds_async()) == 1
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")
        [restored] = await sqlite_instance.get_seed_groups_async(prompt_group_ids=[prompt.prompt_group_id])
        assert prompt.id != original_id
        assert {seed.id for seed in group.seeds} == {seed.id for seed in restored.seeds}
        assert len(await sqlite_instance.get_seeds_async()) == 3

    async def test_seed_entry_roundtrip_async(self, sqlite_instance: MemoryInterface) -> None:
        objective = _objective()
        await sqlite_instance.add_seeds_to_memory_async(seeds=[objective], added_by="tester")

        with sqlite_instance.get_session() as session:
            entry = session.scalars(select(SeedEntry)).one()
            assert entry.conditions == [
                {"condition_type": "answer_matches", "correct_answer": "Paris", "correct_answer_label": "A"}
            ]
            restored = entry.get_seed()

        assert isinstance(restored, SeedObjective)
        assert isinstance(restored.conditions[0], AnswerMatches)
        assert restored.conditions == objective.conditions
        assert restored.value_sha256 == hashlib.sha256(objective.value.encode()).hexdigest()

    async def test_group_reload_keeps_distinct_criteria_async(self, sqlite_instance: MemoryInterface) -> None:
        groups = [
            SeedGroup(seeds=[_objective(answer=answer), SeedPrompt(value="Choose an answer.", data_type="text")])
            for answer in ("Paris", "Rome")
        ]
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=groups, added_by="tester")

        reloaded = await sqlite_instance.get_seed_groups_async()
        assert len(reloaded) == 2
        assert all(len(group.seeds) == 2 for group in reloaded)
        assert {group.scoring_expectation.conditions[0].correct_answer for group in reloaded} == {"Paris", "Rome"}
        expected_hashes = {
            compute_seed_group_hash([SeedIdentifier.from_seed(seed) for seed in group.seeds]) for group in groups
        }
        assert len(expected_hashes) == 2
        assert {
            compute_seed_group_hash([SeedIdentifier.from_seed(seed) for seed in group.seeds]) for group in reloaded
        } == expected_hashes

    @pytest.mark.parametrize(
        ("first_answer", "second_answer"),
        [("Paris", "Rome"), ("Paris", None), (None, "Paris")],
    )
    @pytest.mark.parametrize("load_as_groups", [True, False])
    async def test_new_criteria_keep_complete_groups_across_calls_async(
        self,
        *,
        sqlite_instance: MemoryInterface,
        first_answer: str | None,
        second_answer: str | None,
        load_as_groups: bool,
    ) -> None:
        original_groups = []
        for answer in (first_answer, second_answer):
            conditions = (AnswerMatches(correct_answer=answer, correct_answer_label="A"),) if answer else ()
            group = SeedGroup(
                seeds=[
                    SeedObjective(value="question", dataset_name="questions", conditions=conditions),
                    SeedPrompt(value="shared prompt", dataset_name="questions", data_type="text"),
                ]
            )
            original_groups.append(group)
            if load_as_groups:
                await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=[group], added_by="tester")
            else:
                await sqlite_instance.add_seeds_to_memory_async(seeds=group.seeds, added_by="tester")

        restored = await sqlite_instance.get_seed_groups_async()
        assert len(restored) == 2
        assert all(len(group.seeds) == 2 for group in restored)
        assert {
            compute_seed_group_hash([SeedIdentifier.from_seed(seed) for seed in group.seeds]) for group in restored
        } == {
            compute_seed_group_hash([SeedIdentifier.from_seed(seed) for seed in group.seeds])
            for group in original_groups
        }
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=original_groups, added_by="tester")
        assert len(await sqlite_instance.get_seeds_async()) == 4

    async def test_legacy_null_and_nonobjective_conditions_async(self, sqlite_instance: MemoryInterface) -> None:
        seeds = [SeedObjective(value="legacy"), SeedPrompt(value="prompt", data_type="text")]
        await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="tester")
        with sqlite_instance.get_session() as session:
            session.execute(update(SeedEntry).values(conditions=None))
            session.commit()
            assert all(entry.conditions is None for entry in session.scalars(select(SeedEntry)))
        restored = await sqlite_instance.get_seeds_async()
        objective = next(seed for seed in restored if isinstance(seed, SeedObjective))
        assert objective.conditions == ()
        prompt = next(seed for seed in restored if isinstance(seed, SeedPrompt))
        assert "conditions" not in prompt.model_dump()

    @pytest.mark.parametrize(
        "payload",
        [
            [{"condition_type": "unknown_persisted_seed_condition"}],
            [
                {"condition_type": "answer_matches", "correct_answer": "Paris"},
                {"condition_type": "unknown_persisted_seed_condition"},
            ],
            [{"condition_type": "answer_matches", "correct_answer": "Paris", "future_field": "criterion"}],
            [{"condition_type": "answer_matches", "correct_answer_label": "A"}],
            [{"condition_type": "answer_matches", "correct_answer": "", "correct_answer_label": "A"}],
            {},
            "",
            [None],
        ],
    )
    async def test_invalid_persisted_conditions_raise_async(
        self, *, sqlite_instance: MemoryInterface, payload: object
    ) -> None:
        await sqlite_instance.add_seeds_to_memory_async(seeds=[_objective()], added_by="tester")
        with sqlite_instance.get_session() as session:
            session.execute(update(SeedEntry).values(conditions=payload))
            session.commit()
        with pytest.raises(ValueError, match="condition|correct_answer_label|at least 1 character"):
            (await sqlite_instance.get_seeds_async())

    def test_nonobjective_persisted_conditions_raise(self) -> None:
        entry = SeedEntry(entry=SeedPrompt(value="prompt", data_type="text"))
        entry.conditions = [{"condition_type": "matches_objective"}]
        with pytest.raises(ValueError, match="Only objective seeds"):
            entry.get_seed()

    @pytest.mark.parametrize("dataset", ["questions", None, ""])
    async def test_dedup_preserves_different_conditions_across_calls_async(
        self, *, sqlite_instance: MemoryInterface, dataset: str | None
    ) -> None:
        await sqlite_instance.add_seeds_to_memory_async(seeds=[_objective()], added_by="tester")
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[_objective(dataset=dataset), _objective(answer="Rome", dataset=dataset)], added_by="tester"
        )
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[_objective(answer="Rome", dataset=dataset)], added_by="tester"
        )
        stored = await sqlite_instance.get_seeds_async()
        assert len(stored) == 2
        assert len({seed.value_sha256 for seed in stored}) == 1
        assert {seed.conditions[0].correct_answer for seed in stored} == {"Paris", "Rome"}

    async def test_dedup_keeps_condition_free_and_condition_bearing_seeds_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        objective = _objective()
        plain = SeedObjective(value=objective.value, dataset_name=objective.dataset_name)
        await sqlite_instance.add_seeds_to_memory_async(seeds=[plain], added_by="tester")
        await sqlite_instance.add_seeds_to_memory_async(seeds=[objective], added_by="tester")
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[SeedObjective(value=plain.value, dataset_name=plain.dataset_name)], added_by="tester"
        )
        assert len(await sqlite_instance.get_seeds_async()) == 2

    async def test_duplicates_within_input_are_not_collapsed_async(self, sqlite_instance: MemoryInterface) -> None:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[_objective(), _objective(), _objective(answer="Rome")], added_by="tester"
        )
        assert len(await sqlite_instance.get_seeds_async()) == 3

    async def test_condition_order_is_identity_but_object_key_order_is_not_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        answer = AnswerMatches(correct_answer="Paris", correct_answer_label="A")
        first = SeedObjective(value="question", conditions=(answer, MatchesObjective()))
        reordered = SeedObjective(value="question", conditions=(MatchesObjective(), answer))
        equivalent = SeedObjective.model_validate(
            {
                "value": "question",
                "conditions": [
                    {"correct_answer_label": "A", "correct_answer": "Paris", "condition_type": "answer_matches"},
                    {"condition_type": "matches_objective"},
                ],
            }
        )
        await sqlite_instance.add_seeds_to_memory_async(seeds=[first], added_by="tester")
        await sqlite_instance.add_seeds_to_memory_async(seeds=[reordered, equivalent], added_by="tester")
        assert len(await sqlite_instance.get_seeds_async()) == 2

    @pytest.mark.parametrize(
        ("collation", "stored_dataset", "incoming_dataset"),
        [("NOCASE", "Questions", "questions"), ("RTRIM", "questions", "questions   ")],
    )
    async def test_condition_dedup_respects_database_collation_async(
        self, *, sqlite_instance: MemoryInterface, collation: str, stored_dataset: str, incoming_dataset: str
    ) -> None:
        table = SeedEntry.__table__
        original_type = table.c.dataset_name.type
        table.drop(sqlite_instance.engine)
        table.c.dataset_name.type = String(collation=collation)
        try:
            table.create(sqlite_instance.engine)
            await sqlite_instance.add_seeds_to_memory_async(
                seeds=[_objective(dataset=stored_dataset)], added_by="tester"
            )
            await sqlite_instance.add_seeds_to_memory_async(
                seeds=[_objective(dataset=incoming_dataset), _objective(answer="Rome", dataset=incoming_dataset)],
                added_by="tester",
            )
            assert len(await sqlite_instance.get_seeds_async()) == 2
        finally:
            table.c.dataset_name.type = original_type

    async def test_condition_lookup_remains_bounded_async(self, sqlite_instance: MemoryInterface) -> None:
        for answer in ("Paris", "Rome"):
            seeds = [
                SeedObjective(
                    value=f"question {index}",
                    dataset_name="questions",
                    conditions=(AnswerMatches(correct_answer=answer, correct_answer_label="A"),),
                )
                for index in range(12)
            ]
            with (
                patch.object(type(sqlite_instance), "_MAX_BIND_VARS", 5),
                patch.object(sqlite_instance, "_execute_get_seeds", wraps=sqlite_instance._execute_get_seeds) as lookup,
            ):
                await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="tester")
            assert [len(call.kwargs["value_sha256"]) for call in lookup.call_args_list] == [4, 4, 4]
        assert len(await sqlite_instance.get_seeds_async()) == 24

    async def test_identifier_json_roundtrip(self, sqlite_instance: MemoryInterface) -> None:
        identifier = SeedIdentifier.from_seed(_objective())
        with sqlite_instance.get_session() as session:
            session.add(SeedIdentifierEntry.from_domain_model(identifier))
            session.commit()
        restored = (await sqlite_instance.get_seed_identifiers_async())[0]
        assert restored.params["conditions"] == identifier.params["conditions"]
        assert restored.hash == identifier.hash
