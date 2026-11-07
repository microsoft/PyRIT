# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The shared live search matrix for SQLite unit tests and opt-in Azure SQL integration."""

from typing import Any
from uuid import uuid4

from sqlalchemy import delete

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import AttackResultEntry, FindingEntry, OperationEntry, ScenarioResultEntry, SeedEntry
from pyrit.models import (
    AttackResult,
    Finding,
    IdentifierFilter,
    IdentifierType,
    Operation,
    ScenarioIdentifier,
    ScenarioResult,
    SeedPrompt,
    TargetIdentifier,
)

TEXTS = ("100%", "100X", "a_b", "a/b", "a\\b", "axb", "ab", "[a]", "*", "x%_/[a]*\\Y", "\u00c9THIQUE", "text")
LITERAL_CASES = (
    ("%", {"100%", "x%_/[a]*\\Y"}),
    ("_", {"a_b", "x%_/[a]*\\Y"}),
    ("/", {"a/b", "x%_/[a]*\\Y"}),
    ("\\", {"a\\b", "x%_/[a]*\\Y"}),
    ("[a]", {"[a]", "x%_/[a]*\\Y"}),
    ("*", {"*", "x%_/[a]*\\Y"}),
    ("%_/[a]*\\", {"x%_/[a]*\\Y"}),
    ("\u00e9thique", {"\u00c9THIQUE"}),
    ("TEXT", {"text"}),
    ("a_b", {"a_b"}),
    ("", set(TEXTS)),
    (None, set(TEXTS)),
)
PATTERN_CASES = (
    ("100%", {"100%", "100X"}),
    ("%b", {"a_b", "a/b", "a\\b", "axb", "ab"}),
    ("%TEXT%", {"text"}),
    ("a_b", {"a_b", "a/b", "a\\b", "axb"}),
    ("a/_b", {"a_b"}),
    ("a//b", {"a/b"}),
    ("a\\b", {"a\\b"}),
    ("[a]", {"[a]"}),
    ("*", {"*"}),
    ("%/%%", {"100%", "x%_/[a]*\\Y"}),
    ("\u00e9%", {"\u00c9THIQUE"}),
    ("", set()),
    ("%", set(TEXTS)),
)


async def assert_search_contract_async(
    *, memory: MemoryInterface, expected: set[str], value: str | None = None, pattern: str | None = None
) -> None:
    """Exercise public scalar, decoded-list, JSON and example queries on one backend."""
    token = str(uuid4())
    scenarios = [
        ScenarioResult(
            scenario_identifier=ScenarioIdentifier(
                class_name=text,
                class_module="tests.text_search",
                version=1,
                objective_target=TargetIdentifier(
                    class_name="SearchTarget", class_module="tests.text_search", endpoint=text, model_name=text
                ),
            ),
            labels={"search-contract": token},
            attack_results={},
        )
        for text in TEXTS
    ]
    operation = await memory.add_operation_async(Operation(name=f"search-contract-{token}"))
    try:
        await memory.add_seeds_to_memory_async(
            seeds=[
                SeedPrompt(
                    value=text,
                    dataset_name=token,
                    authors=[text],
                    harm_categories=[text],
                    groups=[text],
                    parameters=[text],
                )
                for text in TEXTS
            ],
            added_by=token,
        )
        await memory.add_scenario_results_to_memory_async(scenario_results=scenarios)
        await memory.add_attack_results_to_memory_async(
            attack_results=[
                AttackResult(conversation_id=str(uuid4()), objective=text, operation_id=operation.id) for text in TEXTS
            ]
        )
        for text in TEXTS:
            await memory.add_finding_async(finding=Finding(operation_id=operation.id, title=text, severity="low"))

        seeds = await memory.get_seeds_async(dataset_name=token, value=value, value_pattern=pattern)
        assert {seed.value for seed in seeds} == expected
        for field in ("authors", "harm_categories", "groups", "parameters"):
            filters: dict[str, Any] = (
                {f"{field}_patterns": [pattern]}
                if pattern is not None
                else {field: [value]}
                if value is not None
                else {}
            )
            seeds = await memory.get_seeds_async(dataset_name=token, **filters)
            assert {seed.value for seed in seeds} == expected
        examples, total, _ = await memory.get_seed_examples_async(
            dataset_name=token, limit=100, value_search=value, value_pattern=pattern
        )
        assert {members[0].value for members in examples.values()} == expected
        assert total == len(expected)
        findings = await memory.get_findings_async(
            operation_id=operation.id, limit=100, title_query=value, title_pattern=pattern
        )
        assert {finding.title for finding in findings} == expected
        attacks = await memory.get_attack_results_async(
            operation_ids=[operation.id], objective=value, objective_pattern=pattern
        )
        assert {attack.objective for attack in attacks} == expected
        results = await memory.get_scenario_results_async(
            labels={"search-contract": token}, scenario_name=value, scenario_name_pattern=pattern
        )
        assert {result.scenario_name for result in results} == expected
        for field in ("objective_target_endpoint", "objective_target_model_name"):
            json_filters: dict[str, Any] = {field: value, f"{field}_pattern": pattern}
            results = await memory.get_scenario_results_async(labels={"search-contract": token}, **json_filters)
            assert {result.scenario_name for result in results} == expected
        if value or pattern is not None:
            identifier_filter = IdentifierFilter(
                identifier_type=IdentifierType.TARGET,
                property_path="$.endpoint",
                value=value,
                value_pattern=pattern,
                partial_match=pattern is None,
            )
            results = await memory.get_scenario_results_async(
                labels={"search-contract": token}, identifier_filters=[identifier_filter]
            )
            assert {result.scenario_name for result in results} == expected
    finally:
        async with await memory.get_session_async() as session:
            await session.execute(delete(FindingEntry).where(FindingEntry.operation_id == operation.id))
            await session.execute(delete(AttackResultEntry).where(AttackResultEntry.operation_id == operation.id))
            await session.execute(
                delete(ScenarioResultEntry).where(ScenarioResultEntry.id.in_([scenario.id for scenario in scenarios]))
            )
            await session.execute(delete(SeedEntry).where(SeedEntry.dataset_name == token))
            await session.execute(delete(OperationEntry).where(OperationEntry.id == operation.id))
            await session.commit()
