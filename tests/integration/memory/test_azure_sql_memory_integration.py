# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from typing import Generator, List
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy.exc import SQLAlchemyError

from pyrit.identifiers import ScorerIdentifier
from pyrit.memory import AzureSQLMemory
from pyrit.memory.memory_models import (
    AttackResultEntry,
    PromptMemoryEntry,
    ScenarioResultEntry,
)
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    MessagePiece,
    ScenarioIdentifier,
    ScenarioResult,
    SeedPrompt,
)


def generate_test_id() -> str:
    """
    Generate a unique 8-character test ID to avoid test pollution across parallel test runs.

    Returns:
        str: A unique 8-character identifier
    """
    return str(uuid4())[:8]


def get_test_scorer_identifier(**kwargs) -> ScorerIdentifier:
    """
    Returns a test ScorerIdentifier for use in integration tests.

    Args:
        **kwargs: Optional overrides for ScorerIdentifier fields.

    Returns:
        ScorerIdentifier: A test scorer identifier with all required fields.
    """
    return ScorerIdentifier(
        class_name=kwargs.get("class_name", "TestScorer"),
        class_module=kwargs.get("class_module", "tests.integration.memory.test_azure_sql_memory_integration"),
        class_description=kwargs.get("class_description", "Test scorer for integration testing"),
        identifier_type=kwargs.get("identifier_type", "instance"),
        scorer_type=kwargs.get("scorer_type", "true_false"),
        system_prompt_template=kwargs.get("system_prompt_template"),
    )


@contextmanager
def cleanup_conversation_data(memory: AzureSQLMemory, conversation_ids: List[str]) -> Generator[None, None, None]:
    """
    Context manager to ensure cleanup of test data from attack results and message pieces.

    This ensures data is cleaned up even if tests fail, preventing test pollution.

    Args:
        memory: AzureSQLMemory instance to clean up
        conversation_ids: List of conversation IDs to delete

    Yields:
        None
    """
    try:
        yield
    finally:
        with closing(memory.get_session()) as session:
            try:
                # Delete attack results first (foreign key dependencies)
                session.query(AttackResultEntry).filter(AttackResultEntry.conversation_id.in_(conversation_ids)).delete(
                    synchronize_session=False
                )

                # Delete message pieces
                session.query(PromptMemoryEntry).filter(PromptMemoryEntry.conversation_id.in_(conversation_ids)).delete(
                    synchronize_session=False
                )

                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                print(f"Cleanup failed: {e}")


@contextmanager
def cleanup_scenario_data(memory: AzureSQLMemory, test_id: str) -> Generator[None, None, None]:
    """
    Context manager to ensure cleanup of scenario result test data.

    Filters and deletes all scenario results containing the test_id in their labels.

    Args:
        memory: AzureSQLMemory instance to clean up
        test_id: Unique test identifier used in labels

    Yields:
        None
    """
    try:
        yield
    finally:
        with closing(memory.get_session()) as session:
            try:
                # Query all scenario results and filter by test_id label
                all_results = session.query(ScenarioResultEntry).filter(ScenarioResultEntry.labels.isnot(None)).all()

                for result in all_results:
                    if result.labels and result.labels.get("test_id") == test_id:
                        session.delete(result)

                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                print(f"Cleanup failed: {e}")


@contextmanager
def cleanup_scenario_data_by_field(
    memory: AzureSQLMemory, test_id: str, field_name: str
) -> Generator[None, None, None]:
    """
    Context manager to ensure cleanup of scenario result test data by objective_target_identifier field.

    Filters and deletes all scenario results containing the test_id in the specified field
    of their objective_target_identifier.

    Args:
        memory: AzureSQLMemory instance to clean up
        test_id: Unique test identifier used in the field
        field_name: Name of the field in objective_target_identifier (e.g., 'endpoint', 'model_name')

    Yields:
        None
    """
    try:
        yield
    finally:
        with closing(memory.get_session()) as session:
            try:
                all_results = (
                    session.query(ScenarioResultEntry)
                    .filter(ScenarioResultEntry.objective_target_identifier.isnot(None))
                    .all()
                )

                for result in all_results:
                    field_value = result.objective_target_identifier.get(field_name, "")
                    if test_id in field_value:
                        session.delete(result)

                session.commit()
            except SQLAlchemyError as e:
                session.rollback()
                print(f"Cleanup failed: {e}")


@pytest.mark.asyncio
async def test_get_seeds_with_metadata_filter(azuresql_instance: AzureSQLMemory):
    """
    Test SQL Azure JSON filtering on seed prompt metadata.

    Verifies that metadata filtering works correctly with both string and integer values,
    and that multiple metadata keys can be combined (AND logic).
    """
    # Use unique values to avoid deduplication with previous test runs
    test_id = str(uuid4())
    value1 = str(uuid4())
    value2 = np.random.randint(0, 10000)

    # Use unique seed values to avoid deduplication
    sp1 = SeedPrompt(value=f"sp1-{test_id}", data_type="text", metadata={"key1": value1}, added_by=test_id)
    sp2 = SeedPrompt(
        value=f"sp2-{test_id}", data_type="text", metadata={"key1": value2, "key2": value1}, added_by=test_id
    )

    # Use public async API method
    await azuresql_instance.add_seeds_to_memory_async(seeds=[sp1, sp2])

    # Verify seeds were inserted
    inserted_seeds = azuresql_instance.get_seeds(added_by=test_id)
    assert len(inserted_seeds) == 2, f"Expected 2 seeds with added_by='{test_id}', got {len(inserted_seeds)}"

    # Test single metadata filter (combining with added_by to avoid old test data)
    result = azuresql_instance.get_seeds(metadata={"key1": value1}, added_by=test_id)
    assert len(result) == 1, f"Expected 1 seed with metadata {{'key1': '{value1}'}}, got {len(result)}"
    assert result[0].metadata == {"key1": value1}

    # Test multiple metadata filters (ALL must be present)
    result2 = azuresql_instance.get_seeds(metadata={"key1": value2, "key2": value1}, added_by=test_id)
    assert len(result2) == 1
    assert result2[0].metadata == {"key1": value2, "key2": value1}

    # Clean up using public API
    with closing(azuresql_instance.get_session()) as session:
        try:
            # Delete seeds by their IDs
            from pyrit.memory.memory_models import SeedEntry

            session.query(SeedEntry).filter(SeedEntry.id.in_([sp1.id, sp2.id])).delete(synchronize_session=False)
            session.commit()
        except SQLAlchemyError as e:
            session.rollback()
            print(f"Cleanup failed: {e}")

    # Ensure that entries are removed (filter by added_by to check only our test data)
    assert azuresql_instance.get_seeds(metadata={"key1": value1}, added_by=test_id) == []
    assert azuresql_instance.get_seeds(metadata={"key2": value1}, added_by=test_id) == []


@pytest.mark.asyncio
async def test_get_attack_results_by_harm_categories(azuresql_instance: AzureSQLMemory):
    """
    Integration test for SQL Azure JSON filtering on targeted harm categories.

    Tests that harm category filtering requires ALL specified categories to be present
    (AND logic, not OR). Verifies both single and multiple category filters work correctly.
    """
    # Use unique conversation IDs to avoid test pollution
    test_id = generate_test_id()

    conversation_ids = [
        f"conv_harm_1_{test_id}",
        f"conv_harm_2_{test_id}",
        f"conv_harm_3_{test_id}",
    ]

    with cleanup_conversation_data(azuresql_instance, conversation_ids):
        # Create message pieces with harm categories
        piece1 = MessagePiece(
            conversation_id=conversation_ids[0],
            role="user",
            original_value="Test 1",
            converted_value="Test 1",
            targeted_harm_categories=["hate", "violence"],
        )
        piece2 = MessagePiece(
            conversation_id=conversation_ids[1],
            role="user",
            original_value="Test 2",
            converted_value="Test 2",
            targeted_harm_categories=["hate"],
        )
        piece3 = MessagePiece(
            conversation_id=conversation_ids[2],
            role="user",
            original_value="Test 3",
            converted_value="Test 3",
            targeted_harm_categories=["violence"],
        )

        azuresql_instance.add_message_pieces_to_memory(message_pieces=[piece1, piece2, piece3])

        # Create attack results
        result1 = AttackResult(
            conversation_id=conversation_ids[0],
            objective="Test objective 1",
            attack_identifier={"name": "test_attack"},
            outcome=AttackOutcome.SUCCESS,
        )
        result2 = AttackResult(
            conversation_id=conversation_ids[1],
            objective="Test objective 2",
            attack_identifier={"name": "test_attack"},
            outcome=AttackOutcome.SUCCESS,
        )
        result3 = AttackResult(
            conversation_id=conversation_ids[2],
            objective="Test objective 3",
            attack_identifier={"name": "test_attack"},
            outcome=AttackOutcome.FAILURE,
        )

        azuresql_instance.add_attack_results_to_memory(attack_results=[result1, result2, result3])

        # Test filtering by single harm category
        results = azuresql_instance.get_attack_results(targeted_harm_categories=["hate"])
        # Filter to only results from this test
        results = [r for r in results if test_id in r.conversation_id]
        assert len(results) == 2
        conv_ids = {r.conversation_id for r in results}
        assert conversation_ids[0] in conv_ids
        assert conversation_ids[1] in conv_ids

        # Test filtering by multiple harm categories (ALL must be present)
        results = azuresql_instance.get_attack_results(targeted_harm_categories=["hate", "violence"])
        results = [r for r in results if test_id in r.conversation_id]
        assert len(results) == 1
        assert results[0].conversation_id == conversation_ids[0]

        # Test filtering with no matches
        results = azuresql_instance.get_attack_results(targeted_harm_categories=["hate", "self-harm"])
        results = [r for r in results if test_id in r.conversation_id]
        assert len(results) == 0


@pytest.mark.asyncio
async def test_get_attack_results_by_labels(azuresql_instance: AzureSQLMemory):
    """
    Integration test for SQL Azure JSON filtering on labels.

    Tests that label filtering requires ALL specified labels to be present
    (AND logic, not OR). Verifies single and multiple label filters work correctly.
    """
    # Use unique conversation IDs to avoid test pollution
    test_id = generate_test_id()

    conversation_ids = [
        f"conv_label_1_{test_id}",
        f"conv_label_2_{test_id}",
        f"conv_label_3_{test_id}",
    ]

    with cleanup_conversation_data(azuresql_instance, conversation_ids):
        # Create message pieces with labels
        piece1 = MessagePiece(
            conversation_id=conversation_ids[0],
            role="user",
            original_value="Test 1",
            converted_value="Test 1",
            labels={"op_id": f"op123_{test_id}", "category": "test", "priority": "high"},
        )
        piece2 = MessagePiece(
            conversation_id=conversation_ids[1],
            role="user",
            original_value="Test 2",
            converted_value="Test 2",
            labels={"op_id": f"op123_{test_id}", "category": "test"},
        )
        piece3 = MessagePiece(
            conversation_id=conversation_ids[2],
            role="user",
            original_value="Test 3",
            converted_value="Test 3",
            labels={"op_id": f"op456_{test_id}"},
        )

        azuresql_instance.add_message_pieces_to_memory(message_pieces=[piece1, piece2, piece3])

        # Create attack results
        result1 = AttackResult(
            conversation_id=conversation_ids[0],
            objective="Test objective 1",
            attack_identifier={"name": "test_attack"},
            outcome=AttackOutcome.SUCCESS,
        )
        result2 = AttackResult(
            conversation_id=conversation_ids[1],
            objective="Test objective 2",
            attack_identifier={"name": "test_attack"},
            outcome=AttackOutcome.SUCCESS,
        )
        result3 = AttackResult(
            conversation_id=conversation_ids[2],
            objective="Test objective 3",
            attack_identifier={"name": "test_attack"},
            outcome=AttackOutcome.FAILURE,
        )

        azuresql_instance.add_attack_results_to_memory(attack_results=[result1, result2, result3])

        # Test filtering by single label
        results = azuresql_instance.get_attack_results(labels={"op_id": f"op123_{test_id}"})
        assert len(results) == 2
        conv_ids = {r.conversation_id for r in results}
        assert conversation_ids[0] in conv_ids
        assert conversation_ids[1] in conv_ids

        # Test filtering by multiple labels (ALL must be present)
        results = azuresql_instance.get_attack_results(labels={"op_id": f"op123_{test_id}", "category": "test"})
        assert len(results) == 2

        results = azuresql_instance.get_attack_results(labels={"op_id": f"op123_{test_id}", "priority": "high"})
        assert len(results) == 1
        assert results[0].conversation_id == conversation_ids[0]

        # Test filtering with no matches
        results = azuresql_instance.get_attack_results(labels={"op_id": "nonexistent"})
        results = [r for r in results if test_id in r.conversation_id]
        assert len(results) == 0


@pytest.mark.asyncio
async def test_scenario_result_scorer_identifier_roundtrip(azuresql_instance: AzureSQLMemory):
    """
    Integration test for storing and retrieving objective_scorer_identifier in ScenarioResult.

    Verifies that ScorerIdentifier is correctly serialized to JSON when stored
    and deserialized back to ScorerIdentifier when retrieved from Azure SQL.
    """
    test_id = generate_test_id()

    with cleanup_scenario_data(azuresql_instance, test_id):
        # Create a ScorerIdentifier with various fields
        scorer_identifier = get_test_scorer_identifier(
            scorer_type="true_false",
            system_prompt_template="Test prompt template for {objective}",
        )

        # Create scenario with scorer identifier
        scenario = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(
                name=f"Scorer Test Scenario {test_id}",
                scenario_version=1,
            ),
            objective_target_identifier={"endpoint": f"https://test-{test_id}.example.com"},
            attack_results={},
            objective_scorer_identifier=scorer_identifier,
            labels={"test_id": test_id},
        )

        azuresql_instance.add_scenario_results_to_memory(scenario_results=[scenario])

        # Retrieve and verify
        results = azuresql_instance.get_scenario_results(labels={"test_id": test_id})
        assert len(results) == 1

        retrieved = results[0]
        assert retrieved.objective_scorer_identifier is not None
        assert isinstance(retrieved.objective_scorer_identifier, ScorerIdentifier)
        assert retrieved.objective_scorer_identifier.scorer_type == "true_false"
        assert retrieved.objective_scorer_identifier.system_prompt_template == "Test prompt template for {objective}"
        assert retrieved.objective_scorer_identifier.class_name == scorer_identifier.class_name
        assert retrieved.objective_scorer_identifier.hash == scorer_identifier.hash


@pytest.mark.asyncio
async def test_get_scenario_results_by_labels(azuresql_instance: AzureSQLMemory):
    """
    Integration test for SQL Azure JSON filtering on scenario result labels.

    Tests that label filtering requires ALL specified labels to be present
    (AND logic, not OR). Verifies single and multiple label filters work correctly.
    """
    # Use unique names to avoid test pollution
    test_id = generate_test_id()

    with cleanup_scenario_data(azuresql_instance, test_id):
        # Create scenario results with labels
        scorer_id = get_test_scorer_identifier()
        scenario1 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"Test Scenario 1 {test_id}", scenario_version=1),
            objective_target_identifier={"endpoint": "https://api.openai.com"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
            labels={"environment": "test", "priority": "high", "team": "red", "test_id": test_id},
        )
        scenario2 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"Test Scenario 2 {test_id}", scenario_version=1),
            objective_target_identifier={"endpoint": "https://api.azure.com"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
            labels={"environment": "test", "priority": "high", "test_id": test_id},
        )
        scenario3 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"Test Scenario 3 {test_id}", scenario_version=1),
            objective_target_identifier={"endpoint": "https://api.anthropic.com"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
            labels={"environment": "prod", "test_id": test_id},
        )

        azuresql_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2, scenario3])

        # Test filtering by single label
        results = azuresql_instance.get_scenario_results(labels={"environment": "test", "test_id": test_id})
        assert len(results) == 2
        names = {r.scenario_identifier.name for r in results}
        assert f"Test Scenario 1 {test_id}" in names
        assert f"Test Scenario 2 {test_id}" in names

        # Test filtering by multiple labels (ALL must be present)
        results = azuresql_instance.get_scenario_results(
            labels={"environment": "test", "priority": "high", "test_id": test_id}
        )
        assert len(results) == 2

        results = azuresql_instance.get_scenario_results(
            labels={"environment": "test", "team": "red", "test_id": test_id}
        )
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"Test Scenario 1 {test_id}"

        # Test filtering with no matches
        results = azuresql_instance.get_scenario_results(labels={"environment": "staging", "test_id": test_id})
        assert len(results) == 0


@pytest.mark.asyncio
async def test_get_scenario_results_by_target_endpoint(azuresql_instance: AzureSQLMemory):
    """
    Integration test for SQL Azure case-insensitive endpoint filtering.

    Tests that endpoint filtering supports case-insensitive substring matching,
    allowing flexible searches across different endpoint formats.
    """
    # Use unique names to avoid test pollution
    test_id = generate_test_id()

    with cleanup_scenario_data_by_field(azuresql_instance, test_id, "endpoint"):
        # Create scenario results with different endpoints
        scorer_id = get_test_scorer_identifier()
        scenario1 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"OpenAI Test {test_id}", scenario_version=1),
            objective_target_identifier={"endpoint": f"https://api-{test_id}.openai.com/v1/chat"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
        )
        scenario2 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"Azure OpenAI Test {test_id}", scenario_version=1),
            objective_target_identifier={"endpoint": f"https://myresource-{test_id}.openai.azure.com/openai"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
        )
        scenario3 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"Anthropic Test {test_id}", scenario_version=1),
            objective_target_identifier={"endpoint": f"https://api-{test_id}.anthropic.com/v1/messages"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
        )
        scenario4 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"Azure Other {test_id}", scenario_version=1),
            objective_target_identifier={"endpoint": f"https://myresource-{test_id}.cognitiveservices.azure.com"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
        )

        azuresql_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2, scenario3, scenario4])

        # Test case-insensitive substring matching - search for test_id specific endpoints
        results = azuresql_instance.get_scenario_results(objective_target_endpoint=test_id)
        assert len(results) == 4  # All should have test_id in their endpoint

        results = azuresql_instance.get_scenario_results(objective_target_endpoint=f"openai.com/{test_id}")
        assert len(results) == 0  # test_id comes before openai.com

        results = azuresql_instance.get_scenario_results(objective_target_endpoint=f"{test_id}.openai")
        assert len(results) == 2
        names = {r.scenario_identifier.name for r in results}
        assert f"OpenAI Test {test_id}" in names
        assert f"Azure OpenAI Test {test_id}" in names

        # Test case-insensitive with AZURE
        results = azuresql_instance.get_scenario_results(objective_target_endpoint=f"{test_id}.openai.AZURE")
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"Azure OpenAI Test {test_id}"

        # Test anthropic
        results = azuresql_instance.get_scenario_results(objective_target_endpoint=f"{test_id}.AnThRoPiC")
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"Anthropic Test {test_id}"

        # Test cognitiveservices
        results = azuresql_instance.get_scenario_results(objective_target_endpoint=f"{test_id}.cognitiveservices")
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"Azure Other {test_id}"


@pytest.mark.asyncio
async def test_get_scenario_results_by_target_model_name(azuresql_instance: AzureSQLMemory):
    """
    Integration test for SQL Azure case-insensitive model name filtering.

    Tests that model name filtering supports case-insensitive substring matching,
    allowing flexible searches across different model name formats and versions.
    """
    # Use unique model name suffixes to avoid test pollution
    test_id = generate_test_id()

    with cleanup_scenario_data_by_field(azuresql_instance, test_id, "model_name"):
        # Create scenario results with different model names
        scorer_id = get_test_scorer_identifier()
        scenario1 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"GPT-4 Test {test_id}", scenario_version=1),
            objective_target_identifier={"model_name": f"gpt-4-turbo-{test_id}"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
        )
        scenario2 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"GPT-4 Omni Test {test_id}", scenario_version=1),
            objective_target_identifier={"model_name": f"gpt-4o-{test_id}"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
        )
        scenario3 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"GPT-3.5 Test {test_id}", scenario_version=1),
            objective_target_identifier={"model_name": f"gpt-3.5-turbo-{test_id}"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
        )
        scenario4 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(name=f"Claude Test {test_id}", scenario_version=1),
            objective_target_identifier={"model_name": f"claude-3-opus-{test_id}"},
            attack_results={},
            objective_scorer_identifier=scorer_id,
        )

        azuresql_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2, scenario3, scenario4])

        # Test case-insensitive substring matching - search for test_id
        results = azuresql_instance.get_scenario_results(objective_target_model_name=test_id)
        assert len(results) == 4  # All should have test_id in their model name

        # Test case-insensitive substring matching - gpt with test_id
        results = azuresql_instance.get_scenario_results(objective_target_model_name=f"gpt-4-turbo-{test_id}")
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"GPT-4 Test {test_id}"

        # Test case-insensitive substring matching - GPT-4 (uppercase)
        results = azuresql_instance.get_scenario_results(objective_target_model_name=f"GPT-4o-{test_id}")
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"GPT-4 Omni Test {test_id}"

        # Test substring in the middle - version number
        results = azuresql_instance.get_scenario_results(objective_target_model_name=f"3.5-turbo-{test_id}")
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"GPT-3.5 Test {test_id}"

        # Test case-insensitive with different model family
        results = azuresql_instance.get_scenario_results(objective_target_model_name=f"CLAUDE-3-opus-{test_id}")
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"Claude Test {test_id}"

        # Test turbo suffix with test_id
        results = azuresql_instance.get_scenario_results(objective_target_model_name=f"turbo-{test_id}")
        assert len(results) == 2
        names = {r.scenario_identifier.name for r in results}
        assert f"GPT-4 Test {test_id}" in names
        assert f"GPT-3.5 Test {test_id}" in names


@pytest.mark.asyncio
async def test_get_scenario_results_combined_filters(azuresql_instance: AzureSQLMemory):
    """
    Integration test for combining multiple SQL Azure JSON filters.

    Tests that multiple filter conditions can be combined simultaneously,
    verifying AND logic across different filter types (labels, endpoint, model name, version, time).
    """
    # Use unique identifiers to avoid test pollution
    test_id = generate_test_id()
    now = datetime.utcnow()
    yesterday = now - timedelta(days=1)

    with cleanup_scenario_data(azuresql_instance, test_id):
        # Create scenario results with various attributes
        scorer_id = get_test_scorer_identifier()
        scenario1 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(
                name=f"Production Test {test_id}", scenario_version=1, pyrit_version="0.4.0"
            ),
            objective_target_identifier={
                "endpoint": f"https://api-{test_id}.openai.com",
                "model_name": f"gpt-4-turbo-{test_id}",
            },
            attack_results={},
            objective_scorer_identifier=scorer_id,
            labels={"environment": "prod", "priority": "high", "test_id": test_id},
            completion_time=now,
        )
        scenario2 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(
                name=f"Test Environment {test_id}", scenario_version=1, pyrit_version="0.4.0"
            ),
            objective_target_identifier={
                "endpoint": f"https://test-{test_id}.openai.com",
                "model_name": f"gpt-4-turbo-{test_id}",
            },
            attack_results={},
            objective_scorer_identifier=scorer_id,
            labels={"environment": "test", "priority": "low", "test_id": test_id},
            completion_time=yesterday,
        )
        scenario3 = ScenarioResult(
            scenario_identifier=ScenarioIdentifier(
                name=f"Old Version Test {test_id}", scenario_version=1, pyrit_version="0.3.0"
            ),
            objective_target_identifier={
                "endpoint": f"https://api-{test_id}.openai.com",
                "model_name": f"gpt-3.5-turbo-{test_id}",
            },
            attack_results={},
            objective_scorer_identifier=scorer_id,
            labels={"environment": "prod", "test_id": test_id},
            completion_time=yesterday,
        )

        azuresql_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2, scenario3])

        # Test combining name filter with labels
        results = azuresql_instance.get_scenario_results(
            scenario_name=f"Test Environment {test_id}", labels={"environment": "test", "test_id": test_id}
        )
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"Test Environment {test_id}"

        # Test combining endpoint, model name, and labels
        results = azuresql_instance.get_scenario_results(
            objective_target_endpoint=f"api-{test_id}.openai",
            objective_target_model_name=f"gpt-4-turbo-{test_id}",
            labels={"priority": "high", "test_id": test_id},
        )
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"Production Test {test_id}"

        # Test combining version and time filters with test_id
        # Add 1 second buffer to account for SQL Server datetime precision differences
        results = azuresql_instance.get_scenario_results(
            pyrit_version="0.4.0", added_before=now + timedelta(seconds=1), labels={"test_id": test_id}
        )
        assert len(results) == 2

        # Test combining all filters - should match only one
        results = azuresql_instance.get_scenario_results(
            scenario_name=f"Production Test {test_id}",
            pyrit_version="0.4.0",
            objective_target_endpoint=f"api-{test_id}",
            objective_target_model_name=f"turbo-{test_id}",
            labels={"environment": "prod", "priority": "high", "test_id": test_id},
        )
        assert len(results) == 1
        assert results[0].scenario_identifier.name == f"Production Test {test_id}"

        # Test combining filters with no matches
        results = azuresql_instance.get_scenario_results(
            objective_target_endpoint=f"api-{test_id}", labels={"environment": "staging", "test_id": test_id}
        )
        assert len(results) == 0
