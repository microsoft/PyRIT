# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from pyrit.identifiers import ScorerIdentifier
from pyrit.memory import MemoryInterface
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ScenarioIdentifier,
    ScenarioResult,
)
from tests.unit.mocks import get_mock_scorer_identifier


@pytest.fixture
def sample_attack_results(sqlite_instance: MemoryInterface):
    """Fixture that creates and adds sample attack results to memory."""
    attack_results = [create_attack_result(f"conv_{i}", f"Objective {i}") for i in range(1, 4)]
    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)
    return attack_results


def create_attack_result(conversation_id: str, objective: str, outcome: AttackOutcome = AttackOutcome.SUCCESS):
    """Helper function to create AttackResult."""
    return AttackResult(
        conversation_id=conversation_id,
        objective=objective,
        attack_identifier={"name": "test_attack"},
        executed_turns=5,
        execution_time_ms=1000,
        outcome=outcome,
    )


def create_scenario_result(
    name: str = "Test Scenario",
    description: str = "Test Description",
    version: int = 1,
    attack_results: Optional[dict[str, list[AttackResult]]] = None,
):
    """Helper function to create ScenarioResult."""
    scenario_identifier = ScenarioIdentifier(
        name=name,
        description=description,
        scenario_version=version,
        init_data={"test_key": "test_value"},
    )

    if attack_results is None:
        attack_results = {}

    # Use ScorerIdentifier directly instead of deprecated dict/mock_scorer
    scorer_identifier = ScorerIdentifier(
        class_name="TestScorer",
        class_module="tests.unit.memory",
        class_description="",
        identifier_type="instance",
    )

    return ScenarioResult(
        scenario_identifier=scenario_identifier,
        objective_target_identifier={"target": "test_target"},
        attack_results=attack_results,
        objective_scorer_identifier=scorer_identifier,
    )


def test_add_and_retrieve_scenario_results(sqlite_instance: MemoryInterface, sample_attack_results):
    """Test adding scenario results to memory and retrieving them without filters."""
    # Create scenario results using the fixture's attack results
    scenario_result1 = create_scenario_result(
        name="Scenario 1",
        attack_results={
            "PromptInjection": sample_attack_results[:2],
        },
    )

    scenario_result2 = create_scenario_result(
        name="Scenario 2",
        attack_results={
            "Crescendo": [sample_attack_results[2]],
        },
    )

    # Add scenario results to memory
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result1, scenario_result2])

    # Verify they were added by querying all scenario results
    all_scenarios = sqlite_instance.get_scenario_results()
    assert len(all_scenarios) == 2

    # Verify the data was stored correctly
    scenario_names = {scenario.scenario_identifier.name for scenario in all_scenarios}
    assert scenario_names == {"Scenario 1", "Scenario 2"}


def test_filter_by_name(sqlite_instance: MemoryInterface, sample_attack_results):
    """Test retrieving scenario results filtered by name."""
    # Create and add scenario results
    scenario_result1 = create_scenario_result(
        name="Test Scenario Alpha",
        attack_results={"Attack1": [sample_attack_results[0]]},
    )
    scenario_result2 = create_scenario_result(
        name="Production Scenario",
        attack_results={"Attack2": [sample_attack_results[1]]},
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result1, scenario_result2])

    # Query by name substring
    results = sqlite_instance.get_scenario_results(scenario_name="Test")
    assert len(results) == 1
    assert results[0].scenario_identifier.name == "Test Scenario Alpha"


def test_filter_by_version(sqlite_instance: MemoryInterface, sample_attack_results):
    """Test retrieving scenario results filtered by version."""
    # Create and add scenario results with different versions
    scenario_result1 = create_scenario_result(
        name="Test Scenario",
        version=1,
        attack_results={"Attack1": [sample_attack_results[0]]},
    )
    scenario_result2 = create_scenario_result(
        name="Test Scenario",
        version=2,
        attack_results={"Attack2": [sample_attack_results[1]]},
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result1, scenario_result2])

    # Query by version
    results = sqlite_instance.get_scenario_results(scenario_version=2)
    assert len(results) == 1
    assert results[0].scenario_identifier.version == 2


def test_filter_by_ids(sqlite_instance: MemoryInterface, sample_attack_results):
    """Test retrieving scenario results by their IDs."""
    # Create and add scenario results
    scenario_result1 = create_scenario_result(
        name="Scenario 1",
        attack_results={"Attack1": [sample_attack_results[0]]},
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result1])

    # Query by ID using the scenario result's id
    results = sqlite_instance.get_scenario_results(scenario_result_ids=[str(scenario_result1.id)])
    assert len(results) == 1
    assert results[0].scenario_identifier.name == "Scenario 1"
    assert results[0].id == scenario_result1.id


def test_empty_ids_returns_empty(sqlite_instance: MemoryInterface):
    """Test that empty ID list returns empty results."""
    results = sqlite_instance.get_scenario_results(scenario_result_ids=[])
    assert len(results) == 0


def test_attack_results_populated_correctly(sqlite_instance: MemoryInterface):
    """Test that retrieving scenario results populates attack_results correctly."""
    # Create and add attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1", AttackOutcome.SUCCESS)
    attack_result2 = create_attack_result("conv_2", "Objective 2", AttackOutcome.FAILURE)
    attack_result3 = create_attack_result("conv_3", "Objective 3", AttackOutcome.SUCCESS)
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Create scenario result with multiple attacks
    scenario_result = create_scenario_result(
        name="Multi-Attack Scenario",
        attack_results={
            "PromptInjection": [attack_result1, attack_result2],
            "Crescendo": [attack_result3],
        },
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    # Retrieve and verify attack_results are populated
    results = sqlite_instance.get_scenario_results()
    assert len(results) == 1

    retrieved_scenario = results[0]
    assert len(retrieved_scenario.attack_results) == 2
    assert "PromptInjection" in retrieved_scenario.attack_results
    assert "Crescendo" in retrieved_scenario.attack_results

    # Verify PromptInjection attacks
    prompt_injection_results = retrieved_scenario.attack_results["PromptInjection"]
    assert len(prompt_injection_results) == 2
    conversation_ids = {ar.conversation_id for ar in prompt_injection_results}
    assert conversation_ids == {"conv_1", "conv_2"}

    # Verify Crescendo attacks
    crescendo_results = retrieved_scenario.attack_results["Crescendo"]
    assert len(crescendo_results) == 1
    assert crescendo_results[0].conversation_id == "conv_3"


def test_attack_order_preserved(sqlite_instance: MemoryInterface):
    """Test that attack results maintain their order within each attack name."""
    # Create and add attack results
    attack_results = [create_attack_result(f"conv_{i}", f"Objective {i}") for i in range(5)]
    sqlite_instance.add_attack_results_to_memory(attack_results=attack_results)

    # Create scenario result with ordered attacks
    scenario_result = create_scenario_result(
        name="Ordered Scenario",
        attack_results={
            "Attack1": attack_results,
        },
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    # Retrieve and verify order is preserved
    results = sqlite_instance.get_scenario_results()
    retrieved_attacks = results[0].attack_results["Attack1"]

    # Verify the conversation IDs are in the same order
    retrieved_conv_ids = [ar.conversation_id for ar in retrieved_attacks]
    original_conv_ids = [ar.conversation_id for ar in attack_results]
    assert retrieved_conv_ids == original_conv_ids


def test_stores_conversation_ids_only(sqlite_instance: MemoryInterface, sample_attack_results):
    """Test that scenario results store only conversation IDs, not full AttackResult objects."""
    # Create and add scenario result
    scenario_result = create_scenario_result(
        name="Test Scenario",
        attack_results={"Attack1": [sample_attack_results[0]]},
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    # Retrieve the scenario result to verify structure
    results = sqlite_instance.get_scenario_results(scenario_result_ids=[str(scenario_result.id)])
    assert len(results) == 1

    retrieved_result = results[0]
    assert "Attack1" in retrieved_result.attack_results
    assert len(retrieved_result.attack_results["Attack1"]) == 1
    assert retrieved_result.attack_results["Attack1"][0].conversation_id == "conv_1"


def test_handles_empty_attack_results(sqlite_instance: MemoryInterface):
    """Test that scenario results can be created with no attack results."""
    # Create scenario result with no attacks
    scenario_result = create_scenario_result(
        name="Empty Scenario",
        attack_results={},
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    # Retrieve and verify
    results = sqlite_instance.get_scenario_results()
    assert len(results) == 1
    assert len(results[0].attack_results) == 0


def test_preserves_metadata(sqlite_instance: MemoryInterface):
    """Test that scenario metadata is preserved correctly."""

    # Create scenario result with metadata
    scenario_identifier = ScenarioIdentifier(
        name="Metadata Test Scenario",
        description="A test scenario with metadata",
        scenario_version=3,
        init_data={"param1": "value1", "param2": 42},
    )

    scorer_identifier = ScorerIdentifier(
        class_name="TestScorer",
        class_module="test.module",
        class_description="",
        identifier_type="instance",
    )

    scenario_result = ScenarioResult(
        scenario_identifier=scenario_identifier,
        objective_target_identifier={"target": "test_target", "endpoint": "https://example.com"},
        attack_results={},
        objective_scorer_identifier=scorer_identifier,
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    # Retrieve and verify metadata
    results = sqlite_instance.get_scenario_results()
    assert len(results) == 1

    retrieved = results[0]
    assert retrieved.scenario_identifier.name == "Metadata Test Scenario"
    assert retrieved.scenario_identifier.description == "A test scenario with metadata"
    assert retrieved.scenario_identifier.version == 3
    assert retrieved.scenario_identifier.init_data == {"param1": "value1", "param2": 42}
    assert retrieved.objective_target_identifier == {"target": "test_target", "endpoint": "https://example.com"}
    # objective_scorer_identifier is now a ScorerIdentifier, check its properties
    assert retrieved.objective_scorer_identifier.class_name == "TestScorer"
    assert retrieved.objective_scorer_identifier.class_module == "test.module"


def test_multiple_scenarios_with_attacks(sqlite_instance: MemoryInterface):
    """Test retrieving multiple scenarios with their attack results populated."""
    # Create attack results for multiple scenarios
    attack_results_scenario1 = [create_attack_result(f"conv_s1_{i}", f"S1 Objective {i}") for i in range(5)]
    attack_results_scenario2 = [create_attack_result(f"conv_s2_{i}", f"S2 Objective {i}") for i in range(3)]

    all_attack_results = attack_results_scenario1 + attack_results_scenario2
    sqlite_instance.add_attack_results_to_memory(attack_results=all_attack_results)

    # Create multiple scenario results
    scenario1 = create_scenario_result(
        name="Scenario 1",
        attack_results={"Attack1": attack_results_scenario1},
    )
    scenario2 = create_scenario_result(
        name="Scenario 2",
        attack_results={"Attack2": attack_results_scenario2},
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2])

    # Retrieve all scenarios
    results = sqlite_instance.get_scenario_results()
    assert len(results) == 2

    # Verify each scenario has the correct attack results
    for result in results:
        if result.scenario_identifier.name == "Scenario 1":
            assert len(result.attack_results["Attack1"]) == 5
        elif result.scenario_identifier.name == "Scenario 2":
            assert len(result.attack_results["Attack2"]) == 3


def test_filter_by_name_and_version(sqlite_instance: MemoryInterface):
    """Test querying with both name and version filters."""
    # Create attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    attack_result3 = create_attack_result("conv_3", "Objective 3")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Create multiple versions of scenarios with similar names
    scenarios = [
        create_scenario_result(name="Test Scenario", version=1, attack_results={"A1": [attack_result1]}),
        create_scenario_result(name="Test Scenario", version=2, attack_results={"A2": [attack_result2]}),
        create_scenario_result(name="Other Scenario", version=1, attack_results={"A3": [attack_result3]}),
    ]
    sqlite_instance.add_scenario_results_to_memory(scenario_results=scenarios)

    # Query with both filters
    results = sqlite_instance.get_scenario_results(scenario_name="Test", scenario_version=2)
    assert len(results) == 1
    assert results[0].scenario_identifier.name == "Test Scenario"
    assert results[0].scenario_identifier.version == 2


def test_filter_by_labels(sqlite_instance: MemoryInterface, sample_attack_results):
    """Test scenario results with labels."""
    # Create scenario with labels
    scenario_identifier = ScenarioIdentifier(name="Labeled Scenario", scenario_version=1)
    scenario_result = ScenarioResult(
        scenario_identifier=scenario_identifier,
        objective_target_identifier={"target": "test_target"},
        attack_results={"Attack1": [sample_attack_results[0]]},
        labels={"environment": "testing", "team": "red-team"},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    # Query by labels
    results = sqlite_instance.get_scenario_results(labels={"environment": "testing"})
    assert len(results) == 1
    assert results[0].labels == {"environment": "testing", "team": "red-team"}


def test_filter_by_multiple_labels(sqlite_instance: MemoryInterface):
    """Test filtering scenario results by multiple labels."""
    # Create attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2])

    # Create scenarios with different labels
    scenario1_identifier = ScenarioIdentifier(name="Scenario 1", scenario_version=1)
    scenario1 = ScenarioResult(
        scenario_identifier=scenario1_identifier,
        objective_target_identifier={"target": "test_target"},
        attack_results={"Attack1": [attack_result1]},
        labels={"environment": "testing", "team": "red-team"},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2_identifier = ScenarioIdentifier(name="Scenario 2", scenario_version=1)
    scenario2 = ScenarioResult(
        scenario_identifier=scenario2_identifier,
        objective_target_identifier={"target": "test_target"},
        attack_results={"Attack2": [attack_result2]},
        labels={"environment": "production", "team": "red-team"},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2])

    # Query requiring both labels to match
    results = sqlite_instance.get_scenario_results(labels={"environment": "testing", "team": "red-team"})
    assert len(results) == 1
    assert results[0].scenario_identifier.name == "Scenario 1"


def test_filter_by_completion_time(sqlite_instance: MemoryInterface):
    """Test scenario results with completion time filtering."""
    # Create attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    attack_result3 = create_attack_result("conv_3", "Objective 3")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Create scenarios with different completion times
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    last_week = now - timedelta(days=7)

    scenario1_identifier = ScenarioIdentifier(name="Recent Scenario", scenario_version=1)
    scenario1 = ScenarioResult(
        scenario_identifier=scenario1_identifier,
        objective_target_identifier={"target": "test_target"},
        attack_results={"Attack1": [attack_result1]},
        completion_time=now,
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2_identifier = ScenarioIdentifier(name="Yesterday Scenario", scenario_version=1)
    scenario2 = ScenarioResult(
        scenario_identifier=scenario2_identifier,
        objective_target_identifier={"target": "test_target"},
        attack_results={"Attack2": [attack_result2]},
        completion_time=yesterday,
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario3_identifier = ScenarioIdentifier(name="Old Scenario", scenario_version=1)
    scenario3 = ScenarioResult(
        scenario_identifier=scenario3_identifier,
        objective_target_identifier={"target": "test_target"},
        attack_results={"Attack3": [attack_result3]},
        completion_time=last_week,
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2, scenario3])

    # Query scenarios after yesterday
    results = sqlite_instance.get_scenario_results(added_after=yesterday)
    assert len(results) == 2
    result_names = {r.scenario_identifier.name for r in results}
    assert "Recent Scenario" in result_names
    assert "Yesterday Scenario" in result_names

    # Query scenarios before yesterday
    results = sqlite_instance.get_scenario_results(added_before=yesterday)
    assert len(results) == 2
    result_names = {r.scenario_identifier.name for r in results}
    assert "Yesterday Scenario" in result_names
    assert "Old Scenario" in result_names


def test_filter_by_pyrit_version(sqlite_instance: MemoryInterface):
    """Test filtering scenario results by PyRIT version."""
    # Create attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2])

    # Create scenarios with different PyRIT versions
    scenario1_identifier = ScenarioIdentifier(name="Old Version Scenario", scenario_version=1, pyrit_version="0.4.0")
    scenario1 = ScenarioResult(
        scenario_identifier=scenario1_identifier,
        objective_target_identifier={"target": "test_target"},
        attack_results={"Attack1": [attack_result1]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2_identifier = ScenarioIdentifier(name="New Version Scenario", scenario_version=1, pyrit_version="0.5.0")
    scenario2 = ScenarioResult(
        scenario_identifier=scenario2_identifier,
        objective_target_identifier={"target": "test_target"},
        attack_results={"Attack2": [attack_result2]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2])

    # Query by PyRIT version
    results = sqlite_instance.get_scenario_results(pyrit_version="0.5.0")
    assert len(results) == 1
    assert results[0].scenario_identifier.name == "New Version Scenario"
    assert results[0].scenario_identifier.pyrit_version == "0.5.0"


def test_filter_by_target_endpoint(sqlite_instance: MemoryInterface):
    """Test filtering scenario results by target endpoint."""
    # Create attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    attack_result3 = create_attack_result("conv_3", "Objective 3")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Create scenarios with different target endpoints
    scenario1_identifier = ScenarioIdentifier(name="Azure Scenario", scenario_version=1)
    scenario1 = ScenarioResult(
        scenario_identifier=scenario1_identifier,
        objective_target_identifier={"target": "OpenAI", "endpoint": "https://myresource.openai.azure.com"},
        attack_results={"Attack1": [attack_result1]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2_identifier = ScenarioIdentifier(name="OpenAI Scenario", scenario_version=1)
    scenario2 = ScenarioResult(
        scenario_identifier=scenario2_identifier,
        objective_target_identifier={"target": "OpenAI", "endpoint": "https://api.openai.com/v1"},
        attack_results={"Attack2": [attack_result2]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario3_identifier = ScenarioIdentifier(name="No Endpoint Scenario", scenario_version=1)
    scenario3 = ScenarioResult(
        scenario_identifier=scenario3_identifier,
        objective_target_identifier={"target": "Local"},
        attack_results={"Attack3": [attack_result3]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2, scenario3])

    # Query by endpoint (case-insensitive substring match)
    results = sqlite_instance.get_scenario_results(objective_target_endpoint="azure")
    assert len(results) == 1
    assert results[0].scenario_identifier.name == "Azure Scenario"

    # Query for OpenAI endpoints
    results = sqlite_instance.get_scenario_results(objective_target_endpoint="openai")
    assert len(results) == 2
    result_names = {r.scenario_identifier.name for r in results}
    assert "Azure Scenario" in result_names
    assert "OpenAI Scenario" in result_names


def test_filter_by_target_model_name(sqlite_instance: MemoryInterface):
    """Test filtering scenario results by target model name."""
    # Create attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    attack_result3 = create_attack_result("conv_3", "Objective 3")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Create scenarios with different model names
    scenario1_identifier = ScenarioIdentifier(name="GPT-4 Scenario", scenario_version=1)
    scenario1 = ScenarioResult(
        scenario_identifier=scenario1_identifier,
        objective_target_identifier={"target": "OpenAI", "model_name": "gpt-4-0613"},
        attack_results={"Attack1": [attack_result1]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2_identifier = ScenarioIdentifier(name="GPT-4o Scenario", scenario_version=1)
    scenario2 = ScenarioResult(
        scenario_identifier=scenario2_identifier,
        objective_target_identifier={"target": "OpenAI", "model_name": "gpt-4o"},
        attack_results={"Attack2": [attack_result2]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario3_identifier = ScenarioIdentifier(name="GPT-3.5 Scenario", scenario_version=1)
    scenario3 = ScenarioResult(
        scenario_identifier=scenario3_identifier,
        objective_target_identifier={"target": "OpenAI", "model_name": "gpt-3.5-turbo"},
        attack_results={"Attack3": [attack_result3]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2, scenario3])

    # Query by model name (case-insensitive substring match)
    results = sqlite_instance.get_scenario_results(objective_target_model_name="gpt-4")
    assert len(results) == 2
    result_names = {r.scenario_identifier.name for r in results}
    assert "GPT-4 Scenario" in result_names
    assert "GPT-4o Scenario" in result_names

    # Query for GPT-3.5
    results = sqlite_instance.get_scenario_results(objective_target_model_name="3.5")
    assert len(results) == 1
    assert results[0].scenario_identifier.name == "GPT-3.5 Scenario"


def test_combined_filters(sqlite_instance: MemoryInterface):
    """Test combining multiple filters together."""
    # Create attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2])

    # Create scenarios with various properties
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    scenario1_identifier = ScenarioIdentifier(name="Test Scenario", scenario_version=1, pyrit_version="0.5.0")
    scenario1 = ScenarioResult(
        scenario_identifier=scenario1_identifier,
        objective_target_identifier={"target": "OpenAI", "endpoint": "https://api.openai.com", "model_name": "gpt-4"},
        attack_results={"Attack1": [attack_result1]},
        labels={"environment": "testing"},
        completion_time=now,
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2_identifier = ScenarioIdentifier(name="Test Scenario", scenario_version=1, pyrit_version="0.4.0")
    scenario2 = ScenarioResult(
        scenario_identifier=scenario2_identifier,
        objective_target_identifier={"target": "Azure", "endpoint": "https://azure.com", "model_name": "gpt-3.5"},
        attack_results={"Attack2": [attack_result2]},
        labels={"environment": "production"},
        completion_time=yesterday,
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2])

    # Query with multiple filters
    results = sqlite_instance.get_scenario_results(
        scenario_name="Test",
        pyrit_version="0.5.0",
        objective_target_model_name="gpt-4",
        labels={"environment": "testing"},
    )
    assert len(results) == 1
    assert results[0].scenario_identifier.pyrit_version == "0.5.0"
    assert "gpt-4" in results[0].objective_target_identifier["model_name"]
