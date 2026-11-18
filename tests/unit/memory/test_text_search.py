# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import UnicodeText, literal, select
from sqlalchemy.dialects import mssql, sqlite

from pyrit.common.text_search import compile_like_pattern, contains_pattern
from pyrit.memory import AzureSQLMemory, MemoryInterface, SeedEntry, SQLiteMemory
from pyrit.memory.analytics_sql import text_match
from pyrit.memory.memory_interface import _AttackResultQuery
from pyrit.memory.memory_models import ScenarioResultEntry
from pyrit.models import (
    AttackAnalyticsDimension,
    AttackAnalyticsFacetQuery,
    Conversation,
    IdentifierFilter,
    IdentifierType,
    MessagePiece,
    ScenarioResult,
    SeedPrompt,
    TargetIdentifier,
)
from test_support.text_search import LITERAL_CASES, PATTERN_CASES, assert_search_contract_async
from unit.mocks import get_mock_target_identifier, make_scenario_result


@pytest.mark.parametrize("value, expected", LITERAL_CASES)
async def test_literal_contract_async(
    *, sqlite_instance: MemoryInterface, value: str | None, expected: set[str]
) -> None:
    await assert_search_contract_async(memory=sqlite_instance, value=value, expected=expected)


@pytest.mark.parametrize("pattern, expected", PATTERN_CASES)
async def test_pattern_contract_async(*, sqlite_instance: MemoryInterface, pattern: str, expected: set[str]) -> None:
    await assert_search_contract_async(memory=sqlite_instance, pattern=pattern, expected=expected)


@pytest.mark.parametrize("memory_type, dialect", [(SQLiteMemory, sqlite.dialect()), (AzureSQLMemory, mssql.dialect())])
@pytest.mark.parametrize("value", ["%", "_", "/", "\\", "[a]", "*", "%_/[a]*\\", "\u00c9THIQUE", ""])
def test_scalar_json_and_list_search_compilation(
    *, memory_type: type[MemoryInterface], dialect: Any, value: str
) -> None:
    memory = object.__new__(memory_type)
    conditions = memory._build_seed_filter_conditions(value=value, authors=[value])
    conditions.append(
        memory._get_condition_json_property_match(
            json_column=ScenarioResultEntry.objective_target_identifier,
            property_path="$.endpoint",
            value=value,
            partial_match=True,
        )
    )
    compiled = select(SeedEntry.id).where(*conditions).compile(dialect=dialect)
    assert contains_pattern(value) in compiled.params.values()
    assert "ESCAPE '/'" in str(compiled)
    assert ("LOWER(" if dialect.name == "mssql" else "pyrit_analytics_lower") in str(compiled)


@pytest.mark.parametrize("pattern, expected", PATTERN_CASES)
@pytest.mark.parametrize("dialect", [sqlite.dialect(), mssql.dialect()])
def test_patterns_are_parameterized(*, dialect: Any, pattern: str, expected: set[str]) -> None:
    compiled = select(literal(1)).where(text_match(column=SeedEntry.value, pattern=pattern)).compile(dialect=dialect)
    assert compile_like_pattern(pattern) in compiled.params.values()
    assert "ESCAPE '/'" in str(compiled)


@pytest.mark.parametrize("pattern", ["/", "text/", "//text/", "%/_/"])
def test_dangling_escapes_are_rejected(pattern: str) -> None:
    with pytest.raises(ValueError, match="unpaired"):
        compile_like_pattern(pattern)
    with pytest.raises(ValueError, match="unpaired"):
        IdentifierFilter(identifier_type=IdentifierType.TARGET, property_path="$.endpoint", value_pattern=pattern)
    with pytest.raises(ValueError, match="unpaired"):
        AttackAnalyticsFacetQuery(dimension=AttackAnalyticsDimension(name="operation"), search_pattern=pattern)


@pytest.mark.parametrize(
    "flags", [{}, {"partial_match": True}, {"case_sensitive": True}, {"array_element_path": "$.x"}]
)
def test_identifier_pattern_conflicts(flags: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        IdentifierFilter(
            identifier_type=IdentifierType.TARGET,
            property_path="$.endpoint",
            value_pattern="%",
            **(flags if flags else {"value": "x"}),
        )


@pytest.mark.parametrize("field", ["value", "authors", "harm_categories", "groups", "parameters"])
async def test_seed_literal_pattern_conflicts_async(*, sqlite_instance: MemoryInterface, field: str) -> None:
    values = {"value": "x", "value_pattern": "%"} if field == "value" else {field: ["x"], f"{field}_patterns": ["%"]}
    for method in (
        sqlite_instance.get_seeds_async,
        sqlite_instance.get_seed_groups_async,
        sqlite_instance.remove_seeds_from_memory_async,
        sqlite_instance.remove_seed_groups_from_memory_async,
    ):
        with pytest.raises(ValueError, match="either"):
            await method(**values)


async def test_scalar_pattern_conflicts_async(sqlite_instance: MemoryInterface) -> None:
    with pytest.raises(ValueError, match="either"):
        await sqlite_instance.get_findings_async(title_query="", title_pattern="")
    with pytest.raises(ValueError, match="either"):
        await sqlite_instance.get_attack_results_async(objective="", objective_pattern="")
    with pytest.raises(ValueError, match="either"):
        await sqlite_instance.get_seed_examples_async(dataset_name=None, limit=1, value_search="", value_pattern="")
    with pytest.raises(ValueError, match="either"):
        await sqlite_instance.get_scenario_results_async(scenario_name="", scenario_name_pattern="")
    with pytest.raises(ValueError, match="either"):
        await sqlite_instance.get_scenario_results_async(
            scenario_result_ids=[], scenario_name="x", scenario_name_pattern="%"
        )
    with pytest.raises(ValueError, match="either"):
        _AttackResultQuery(attack_result_ids=[], objective="x", objective_pattern="%")
    unsupported_filter = IdentifierFilter(identifier_type=IdentifierType.ATTACK, property_path="$.x", value="x")
    assert (
        await sqlite_instance.get_scenario_results_async(
            scenario_result_ids=[], identifier_filters=[unsupported_filter]
        )
        == []
    )


async def test_empty_and_null_json_and_list_values_async(sqlite_instance: MemoryInterface) -> None:
    async with await sqlite_instance.get_session_async() as session:
        for document, expected in [
            ({}, False),
            ({"value": None}, False),
            ({"value": ""}, True),
            ({"value": "x"}, False),
        ]:
            condition = sqlite_instance._get_condition_json_property_match(
                json_column=literal(json.dumps(document), UnicodeText()),
                property_path="$.value",
                value_pattern="",
            )
            assert bool(await session.scalar(select(condition))) == expected
        assert await session.scalar(select(text_match(column=literal("", UnicodeText()), pattern="")))
        assert await session.scalar(select(text_match(column=literal(None, UnicodeText()), pattern="%"))) is None
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[SeedPrompt(value="", authors=[""]), SeedPrompt(value="null", authors=None)], added_by="test"
    )
    assert [seed.value for seed in await sqlite_instance.get_seeds_async(value_pattern="")] == [""]
    assert [seed.value for seed in await sqlite_instance.get_seeds_async(authors_patterns=[""])] == [""]
    assert len(await sqlite_instance.get_seeds_async(value="")) == 2
    examples, total, _ = await sqlite_instance.get_seed_examples_async(dataset_name=None, limit=10, value_pattern="")
    assert total == 1
    assert next(iter(examples.values()))[0].value == ""


async def test_exact_removal_and_dataset_precedence_async(sqlite_instance: MemoryInterface) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[
            SeedPrompt(value="100%", dataset_name="A_B", authors=["a_b"]),
            SeedPrompt(value="100X", dataset_name="axb", authors=["axb"]),
        ],
        added_by="test",
    )
    assert len(await sqlite_instance.get_seeds_async(dataset_name="A_B", dataset_name_pattern="invalid/")) == 1
    assert len(await sqlite_instance.get_seeds_async(dataset_name_pattern="a/_b")) == 1
    assert await sqlite_instance.remove_seeds_from_memory_async(authors=["a_b"]) == 1
    assert await sqlite_instance.remove_seeds_from_memory_async(value="100%") == 0
    assert await sqlite_instance.remove_seeds_from_memory_async(value_pattern="100%") == 1
    with pytest.raises(ValueError, match="At least one"):
        await sqlite_instance.remove_seeds_from_memory_async(value="")


async def test_scenario_alias_normalization_is_explicit_async(sqlite_instance: MemoryInterface) -> None:
    await sqlite_instance.add_scenario_results_to_memory_async(
        scenario_results=[
            make_scenario_result(
                scenario_name="ContentHarms",
                attack_results={},
                objective_target_identifier=get_mock_target_identifier(),
            ),
            make_scenario_result(
                scenario_name="content_harms",
                attack_results={},
                objective_target_identifier=get_mock_target_identifier(),
            ),
        ]
    )
    literal_results = await sqlite_instance.get_scenario_results_async(scenario_name="content_harms")
    assert [result.scenario_name for result in literal_results] == ["content_harms"]
    alias_results = await sqlite_instance.get_scenario_results_async(
        scenario_name=ScenarioResult.normalize_scenario_name("content_harms")
    )
    assert [result.scenario_name for result in alias_results] == ["ContentHarms"]


async def test_group_pattern_filter_and_removal_preserve_members_async(sqlite_instance: MemoryInterface) -> None:
    group_id = uuid4()
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[
            SeedPrompt(value="100%", prompt_group_id=group_id, sequence=0, role="user"),
            SeedPrompt(value="companion", prompt_group_id=group_id, sequence=1, role="assistant"),
            SeedPrompt(value="100X"),
        ],
        added_by="test",
    )
    groups = await sqlite_instance.get_seed_groups_async(value_pattern="100/%")
    assert len(groups) == 1
    assert {seed.value for seed in groups[0].seeds} == {"100%", "companion"}
    assert await sqlite_instance.remove_seed_groups_from_memory_async(value_pattern="100/%") == 2
    assert [seed.value for seed in await sqlite_instance.get_seeds_async()] == ["100X"]


async def test_list_patterns_keep_all_and_example_exact_lists_keep_any_async(sqlite_instance: MemoryInterface) -> None:
    await sqlite_instance.add_seeds_to_memory_async(
        seeds=[
            SeedPrompt(value="both", authors=["ALPHA", "WEB"], harm_categories=["alpha", "web"]),
            SeedPrompt(value="first", authors=["alpha"], harm_categories=["alpha"]),
            SeedPrompt(value="last", authors=["web"], harm_categories=["web"]),
        ],
        added_by="test",
    )
    seeds = await sqlite_instance.get_seeds_async(authors_patterns=["a%", "%b"])
    assert [seed.value for seed in seeds] == ["both"]
    examples, total, _ = await sqlite_instance.get_seed_examples_async(
        dataset_name=None, limit=10, harm_categories=["unknown", "WEB"]
    )
    assert total == 2
    assert {members[0].value for members in examples.values()} == {"both", "last"}


async def test_candidate_filter_uses_the_same_contract_async(sqlite_instance: MemoryInterface) -> None:
    batch = await sqlite_instance.prepare_seed_batch_async(
        seeds=[SeedPrompt(value="100%"), SeedPrompt(value="100X")], added_by="test"
    )
    literal_seeds = await sqlite_instance.filter_seed_candidates_async(seeds=batch.seeds, filters={"value": "100%"})
    pattern_seeds = await sqlite_instance.filter_seed_candidates_async(
        seeds=batch.seeds, filters={"value_pattern": "100%"}
    )
    assert [seed.value for seed in literal_seeds] == ["100%"]
    assert [seed.value for seed in pattern_seeds] == ["100%", "100X"]


@pytest.mark.parametrize(
    "filters, error",
    [
        ({"value_pattern": "text/"}, "unpaired"),
        ({"value": "", "value_pattern": ""}, "either"),
        ({"dataset_name_pattern": "text/"}, "unpaired"),
        *[
            ({f"{field}_patterns": ["text/"]}, "unpaired")
            for field in ("authors", "harm_categories", "groups", "parameters")
        ],
        *[
            ({field: [], f"{field}_patterns": []}, "either")
            for field in ("authors", "harm_categories", "groups", "parameters")
        ],
    ],
)
async def test_empty_candidate_filters_validate_without_database_async(
    *, sqlite_instance: MemoryInterface, filters: dict[str, Any], error: str
) -> None:
    with patch.object(sqlite_instance, "_run_database_operation_async", new_callable=AsyncMock) as operation:
        with pytest.raises(ValueError, match=error):
            await sqlite_instance.filter_seed_candidates_async(seeds=[], filters=filters)
        operation.assert_not_called()


@pytest.mark.parametrize(
    "filters",
    [
        {},
        {"value": ""},
        {"value_pattern": ""},
        {"value_pattern": "text%"},
        {"authors_patterns": []},
        {"authors_patterns": ["text%"]},
        {"dataset_name": "exact", "dataset_name_pattern": "text/"},
    ],
)
async def test_valid_empty_candidate_filters_preserve_shortcut_async(
    *, sqlite_instance: MemoryInterface, filters: dict[str, Any]
) -> None:
    with patch.object(sqlite_instance, "_run_database_operation_async", new_callable=AsyncMock) as operation:
        assert await sqlite_instance.filter_seed_candidates_async(seeds=[], filters=filters) == []
        operation.assert_not_called()


def test_analytics_pattern_query_round_trips_and_conflicts_are_rejected() -> None:
    query = AttackAnalyticsFacetQuery(dimension=AttackAnalyticsDimension(name="operation"), search_pattern="%/_%")
    assert AttackAnalyticsFacetQuery.model_validate(query.model_dump()) == query
    with pytest.raises(ValueError, match="either"):
        AttackAnalyticsFacetQuery(dimension=query.dimension, search="", search_pattern="%")


async def test_identifier_pattern_is_forwarded_by_prompt_queries_async(sqlite_instance: MemoryInterface) -> None:
    for text in ("100%_Path", "100XXPath"):
        conversation_id = str(uuid4())
        await sqlite_instance.add_conversation_to_memory_async(
            conversation=Conversation(
                conversation_id=conversation_id,
                target_identifier=TargetIdentifier(class_name="SearchTarget", class_module="test", endpoint=text),
            )
        )
        await sqlite_instance.add_message_pieces_to_memory_async(
            message_pieces=[MessagePiece(role="user", original_value=text, conversation_id=conversation_id)]
        )
    pattern = IdentifierFilter(
        identifier_type=IdentifierType.TARGET, property_path="$.endpoint", value_pattern="100%path"
    )
    messages = await sqlite_instance.get_message_pieces_async(identifier_filters=[pattern])
    assert {message.original_value for message in messages} == {"100%_Path", "100XXPath"}
    literal = IdentifierFilter(
        identifier_type=IdentifierType.TARGET, property_path="$.endpoint", value="%_path", partial_match=True
    )
    messages = await sqlite_instance.get_message_pieces_async(identifier_filters=[literal])
    assert [message.original_value for message in messages] == ["100%_Path"]
