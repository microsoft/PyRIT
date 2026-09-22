# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import hashlib
from unittest.mock import patch

import pytest
from sqlalchemy import String, select, update

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import SeedEntry, SeedIdentifierEntry
from pyrit.models import AnswerMatches, MatchesObjective, SeedGroup, SeedIdentifier, SeedObjective, SeedPrompt
from pyrit.models.identifiers.seed_identifier import compute_seed_group_hash


def _objective(*, answer: str = "Paris", dataset: str | None = "questions") -> SeedObjective:
    return SeedObjective(
        value="What is the capital?",
        dataset_name=dataset,
        conditions=(AnswerMatches(correct_answer=answer, correct_answer_index="A"),),
    )


@pytest.mark.usefixtures("patch_central_database")
class TestSeedConditions:
    async def test_seed_entry_roundtrip_async(self, sqlite_instance: MemoryInterface) -> None:
        objective = _objective()
        await sqlite_instance.add_seeds_to_memory_async(seeds=[objective], added_by="tester")

        with sqlite_instance.get_session() as session:
            entry = session.scalars(select(SeedEntry)).one()
            assert entry.conditions == [
                {"condition_type": "answer_matches", "correct_answer": "Paris", "correct_answer_index": "A"}
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

        reloaded = sqlite_instance.get_seed_groups()
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
            conditions = (AnswerMatches(correct_answer=answer, correct_answer_index="A"),) if answer else ()
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

        restored = sqlite_instance.get_seed_groups()
        assert len(restored) == 2
        assert all(len(group.seeds) == 2 for group in restored)
        assert {
            compute_seed_group_hash([SeedIdentifier.from_seed(seed) for seed in group.seeds]) for group in restored
        } == {
            compute_seed_group_hash([SeedIdentifier.from_seed(seed) for seed in group.seeds])
            for group in original_groups
        }
        await sqlite_instance.add_seed_groups_to_memory_async(prompt_groups=original_groups, added_by="tester")
        assert len(sqlite_instance.get_seeds()) == 4

    async def test_legacy_null_and_nonobjective_conditions_async(self, sqlite_instance: MemoryInterface) -> None:
        seeds = [SeedObjective(value="legacy"), SeedPrompt(value="prompt", data_type="text")]
        await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="tester")
        with sqlite_instance.get_session() as session:
            session.execute(update(SeedEntry).values(conditions=None))
            session.commit()
            assert all(entry.conditions is None for entry in session.scalars(select(SeedEntry)))
        restored = sqlite_instance.get_seeds()
        objective = next(seed for seed in restored if isinstance(seed, SeedObjective))
        assert objective.conditions == ()
        prompt = next(seed for seed in restored if isinstance(seed, SeedPrompt))
        assert "conditions" not in prompt.model_dump()

    @pytest.mark.parametrize(
        "payload",
        [
            [{"condition_type": "unknown_persisted_seed_condition"}],
            [{"condition_type": "answer_matches", "correct_answer_index": "A"}],
            [{"condition_type": "answer_matches", "correct_answer": "", "correct_answer_index": "A"}],
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
        with pytest.raises(ValueError, match="condition|correct_answer_index|at least 1 character"):
            sqlite_instance.get_seeds()

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
        stored = sqlite_instance.get_seeds()
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
        assert len(sqlite_instance.get_seeds()) == 2

    async def test_duplicates_within_input_are_not_collapsed_async(self, sqlite_instance: MemoryInterface) -> None:
        await sqlite_instance.add_seeds_to_memory_async(
            seeds=[_objective(), _objective(), _objective(answer="Rome")], added_by="tester"
        )
        assert len(sqlite_instance.get_seeds()) == 3

    async def test_condition_order_is_identity_but_object_key_order_is_not_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        answer = AnswerMatches(correct_answer="Paris", correct_answer_index="A")
        first = SeedObjective(value="question", conditions=(answer, MatchesObjective()))
        reordered = SeedObjective(value="question", conditions=(MatchesObjective(), answer))
        equivalent = SeedObjective.model_validate(
            {
                "value": "question",
                "conditions": [
                    {"correct_answer_index": "A", "correct_answer": "Paris", "condition_type": "answer_matches"},
                    {"condition_type": "matches_objective"},
                ],
            }
        )
        await sqlite_instance.add_seeds_to_memory_async(seeds=[first], added_by="tester")
        await sqlite_instance.add_seeds_to_memory_async(seeds=[reordered, equivalent], added_by="tester")
        assert len(sqlite_instance.get_seeds()) == 2

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
            assert len(sqlite_instance.get_seeds()) == 2
        finally:
            table.c.dataset_name.type = original_type

    async def test_condition_lookup_remains_bounded_async(self, sqlite_instance: MemoryInterface) -> None:
        for answer in ("Paris", "Rome"):
            seeds = [
                SeedObjective(
                    value=f"question {index}",
                    dataset_name="questions",
                    conditions=(AnswerMatches(correct_answer=answer, correct_answer_index="A"),),
                )
                for index in range(12)
            ]
            with (
                patch.object(type(sqlite_instance), "_MAX_BIND_VARS", 5),
                patch.object(sqlite_instance, "get_seeds", wraps=sqlite_instance.get_seeds) as lookup,
            ):
                await sqlite_instance.add_seeds_to_memory_async(seeds=seeds, added_by="tester")
            assert [len(call.kwargs["value_sha256"]) for call in lookup.call_args_list] == [4, 4, 4]
        assert len(sqlite_instance.get_seeds()) == 24

    def test_identifier_json_roundtrip(self, sqlite_instance: MemoryInterface) -> None:
        identifier = SeedIdentifier.from_seed(_objective())
        with sqlite_instance.get_session() as session:
            session.add(SeedIdentifierEntry.from_domain_model(identifier))
            session.commit()
        restored = sqlite_instance.get_seed_identifiers()[0]
        assert restored.params["conditions"] == identifier.params["conditions"]
        assert restored.hash == identifier.hash
