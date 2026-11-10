# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Bounded stored facts and portable dataset compatibility queries."""

from unittest.mock import patch

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import mssql, sqlite
from sqlalchemy.engine import Dialect, Engine

from pyrit.memory import SQLiteMemory
from pyrit.models import AttackSeedGroup, SeedObjective, SeedPrompt

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


@pytest.mark.parametrize("dialect", [sqlite.dialect(), mssql.dialect()])
def test_group_profile_query_uses_portable_container_projections(dialect: Dialect) -> None:
    statement = SQLiteMemory._build_seed_dataset_group_profiles_statement(metadata_keys=["policy"])
    sql = str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
    assert "GROUP BY" in sql
    assert "prompt_metadata" in sql
    assert ".value" not in sql
    if dialect.name == "mssql":
        assert "JSON_QUERY" in sql
        assert "json_extract" not in sql
        assert "JSON_VALUE" not in sql
    else:
        assert "json_extract" in sql
