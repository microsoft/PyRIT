# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded stored facts and portable dataset compatibility queries."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.engine import Dialect, Engine

from pyrit.memory import SeedEntry, SQLiteMemory
from pyrit.models import (
    AssertGroupMetadata,
    AttackSeedGroup,
    SeedGroupRequirements,
    SeedMetadataRequirement,
    SeedObjective,
    SeedPrompt,
    group_seeds_into_attack_groups,
)
from pyrit.models.dataset_compatibility import DatasetCompatibilityStatus
from pyrit.scenario.core.dataset_compatibility import assess_candidate_profiles, assess_scenario_groups
from unit.assert_mocks import make_assert_config, make_assert_result

pytestmark = pytest.mark.usefixtures("patch_central_database")


async def test_group_profiles_compress_counts_without_hydrating_seeds_async(sqlite_instance: SQLiteMemory) -> None:
    groups = [
        AttackSeedGroup(
            seeds=[
                SeedObjective(
                    value=f"policy case {index}",
                    dataset_name="imported",
                    metadata={"policy": {}} if index % 2 else {},
                ),
                *(
                    SeedPrompt(
                        value=f"user prompt {index}.{sequence}", dataset_name="imported", sequence=sequence, role="user"
                    )
                    for sequence in range(1 + index % 4)
                ),
            ]
        )
        for index in range(100)
    ]
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[seed for group in groups for seed in group.seeds], added_by="test"
    )
    statements: list[str] = []

    def record(*, statement: str, **_: object) -> None:
        statements.append(statement)

    event.listen(Engine, "before_cursor_execute", record, named=True)
    try:
        with patch.object(sqlite_instance, "get_seeds_async", side_effect=AssertionError("No body reads in listing")):
            profiles = await sqlite_instance.get_seed_dataset_group_profiles_async(metadata_keys=["policy"])
    finally:
        event.remove(Engine, "before_cursor_execute", record)
    assert len(statements) == 1
    assert len(profiles) == 2
    assert sum(profile.group_count for profile in profiles) == 100
    assert all(profile.single_objective and profile.has_user_prompt for profile in profiles)
    assert {profile.metadata_keys: profile.group_count for profile in profiles} == {(): 50, ("policy",): 50}


async def test_group_profiles_do_not_combine_facts_from_different_groups_async(sqlite_instance: SQLiteMemory) -> None:
    groups = [
        AttackSeedGroup(seeds=[SeedObjective(value="has policy", dataset_name="mixed", metadata={"policy": {}})]),
        AttackSeedGroup(
            seeds=[
                SeedObjective(value="no policy", dataset_name="mixed"),
                SeedPrompt(value="has user input", dataset_name="mixed"),
            ]
        ),
    ]
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[seed for group in groups for seed in group.seeds], added_by="test"
    )
    profiles = await sqlite_instance.get_seed_dataset_group_profiles_async(metadata_keys=["policy"])
    assert len(profiles) == 2
    assert not any(profile.metadata_keys and profile.has_user_prompt for profile in profiles)


@pytest.mark.parametrize("with_prompt", [False, True])
async def test_group_profiles_separate_standalone_seed_and_explicit_group_namespaces_async(
    *, sqlite_instance: SQLiteMemory, with_prompt: bool
) -> None:
    metadata = make_assert_result(config=make_assert_config(group_count=1)).seed_groups[0].objective.metadata
    shared_id = uuid4()
    standalone = SeedObjective(id=shared_id, value="Standalone case.", dataset_name="collision", metadata=metadata)
    explicit = AttackSeedGroup(
        seeds=[
            SeedObjective(
                value="Grouped case.", dataset_name="collision", prompt_group_id=shared_id, metadata=metadata
            ),
            *(
                [SeedPrompt(value="Grouped request.", dataset_name="collision", prompt_group_id=shared_id, role="user")]
                if with_prompt
                else []
            ),
        ]
    )
    await sqlite_instance.add_seeds_to_memory_async(seeds=[standalone, *explicit.seeds], added_by="test")
    stored = await sqlite_instance.get_seeds_async(dataset_name="collision")
    assert next(seed for seed in stored if seed.id == shared_id).prompt_group_id is None
    assert any(seed.prompt_group_id == shared_id for seed in stored)
    with patch.object(
        sqlite_instance, "get_seeds_async", side_effect=AssertionError("Profiles must not hydrate seeds.")
    ):
        profiles = await sqlite_instance.get_seed_dataset_group_profiles_async(
            metadata_keys=[AssertGroupMetadata.METADATA_KEY]
        )
    assert sum(profile.group_count for profile in profiles) == 2
    assert all(profile.single_objective for profile in profiles)
    assert len(profiles) == (2 if with_prompt else 1)
    requirements = SeedGroupRequirements(
        metadata=(
            SeedMetadataRequirement(
                name="assert_generation",
                key=AssertGroupMetadata.METADATA_KEY,
                description="ASSERT policy metadata",
                validator=AssertGroupMetadata.from_metadata,
            ),
        )
    )
    candidate = assess_candidate_profiles(name="collision", profiles=profiles, requirements=requirements)
    assert candidate.status is DatasetCompatibilityStatus.NOT_VERIFIED
    hydrated = group_seeds_into_attack_groups(stored)
    assert len(hydrated) == 2
    assert (
        assess_scenario_groups(name="collision", groups=hydrated, requirements=requirements).status
        is DatasetCompatibilityStatus.COMPATIBLE
    )


@pytest.mark.parametrize(
    ("roles", "valid_user"),
    [
        ((None,), True),
        ((None, None), True),
        ((None, "user"), True),
        ((None, "assistant"), False),
        (("assistant",), False),
        (("system",), False),
    ],
)
async def test_legacy_prompt_roles_keep_candidate_assessment_conservative_async(
    *, sqlite_instance: SQLiteMemory, roles: tuple[str | None, ...], valid_user: bool
) -> None:
    metadata = (
        make_assert_result(config=make_assert_config(group_count=1, include_adversarial_prompt=False))
        .seed_groups[0]
        .objective.metadata
    )
    group = AttackSeedGroup(
        seeds=[
            SeedObjective(value="Stored policy case.", dataset_name="legacy", metadata=metadata),
            *(
                SeedPrompt(value=f"Stored prompt piece {index}.", dataset_name="legacy", role="user", sequence=0)
                for index in range(len(roles))
            ),
        ]
    )
    await sqlite_instance.add_seeds_to_memory_async(seeds=group.seeds, added_by="test")
    async with await sqlite_instance.get_session_async() as session:
        for prompt, role in zip(group.prompts, roles, strict=True):
            await session.execute(update(SeedEntry).where(SeedEntry.id == prompt.id).values(role=role))
        await session.commit()
        stored_roles = (
            await session.scalars(
                select(SeedEntry.role).where(SeedEntry.dataset_name == "legacy", SeedEntry.seed_type == "prompt")
            )
        ).all()
    assert stored_roles.count(None) == roles.count(None)

    requirements = SeedGroupRequirements(
        required_user_prompt=True,
        metadata=(
            SeedMetadataRequirement(
                name="assert_generation",
                key=AssertGroupMetadata.METADATA_KEY,
                description="ASSERT policy metadata",
                validator=AssertGroupMetadata.from_metadata,
            ),
        ),
    )
    profiles = await sqlite_instance.get_seed_dataset_group_profiles_async(
        metadata_keys=[AssertGroupMetadata.METADATA_KEY]
    )
    candidate = assess_candidate_profiles(name="legacy", profiles=profiles, requirements=requirements)
    expected_hint = (
        DatasetCompatibilityStatus.NOT_VERIFIED
        if None in roles or "user" in roles
        else DatasetCompatibilityStatus.INCOMPATIBLE
    )
    assert candidate.status is expected_hint
    assert candidate.status is not DatasetCompatibilityStatus.COMPATIBLE

    (restored,) = await sqlite_instance.get_seed_groups_async(dataset_name="legacy")
    hydrated = AttackSeedGroup(seeds=restored.seeds)
    assessment = assess_scenario_groups(name="legacy", groups=[hydrated], requirements=requirements)
    assert (assessment.status is DatasetCompatibilityStatus.COMPATIBLE) is valid_user
    assert all(
        prompt.role == ("user" if valid_user else next(role for role in roles if role is not None))
        for prompt in hydrated.prompts
    )
    if "assistant" in roles or "system" in roles:
        static_users = SeedGroupRequirements(user_prompts_only=True)
        assert (
            assess_candidate_profiles(name="legacy", profiles=profiles, requirements=static_users).status
            is DatasetCompatibilityStatus.INCOMPATIBLE
        )
        assert static_users.check(seed_group=hydrated)


@pytest.mark.parametrize("value", [None, False, 0, "reference", "x" * 5001, {}, []])
async def test_metadata_key_presence_is_independent_of_payload_shape_async(
    *, sqlite_instance: SQLiteMemory, value: object
) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="objective", dataset_name="references", metadata={"answer": value})],
        added_by="test",
    )
    profiles = await sqlite_instance.get_seed_dataset_group_profiles_async(metadata_keys=["answer"])
    assert len(profiles) == 1
    assert profiles[0].metadata_keys == ("answer",)


@pytest.mark.parametrize("key", ["literal.dot", 'literal"quote', "caf\u00e9"])
async def test_metadata_presence_matches_literal_keys_async(*, sqlite_instance: SQLiteMemory, key: str) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedObjective(value="objective", dataset_name="references", metadata={key: "answer"})],
        added_by="test",
    )
    profiles = await sqlite_instance.get_seed_dataset_group_profiles_async(metadata_keys=[key])
    assert profiles[0].metadata_keys == (key,)


@pytest.mark.parametrize("dialect", [sqlite.dialect(), mssql.dialect()])
def test_group_profile_query_uses_portable_presence_projections(dialect: Dialect) -> None:
    statement = SQLiteMemory._build_seed_dataset_group_profiles_statement(metadata_keys=["policy"])
    sql = str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
    assert "GROUP BY" in sql
    assert "prompt_metadata" in sql
    assert ".value" not in sql
    if dialect.name == "mssql":
        assert "OPENJSON" in sql
        assert "JSON_QUERY" in sql
        assert "json_extract" not in sql
        assert "JSON_VALUE" not in sql
        assert "max(CASE" not in sql
    else:
        assert "json_each" in sql
