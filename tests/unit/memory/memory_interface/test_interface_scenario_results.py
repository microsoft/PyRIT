# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from datetime import datetime, timedelta, timezone

import pytest
from unit.mocks import get_mock_scorer_identifier, make_scenario_result

from pyrit.memory import MemoryInterface
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    IdentifierFilter,
    IdentifierType,
)


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
        executed_turns=5,
        execution_time_ms=1000,
        outcome=outcome,
    )


def create_scenario_result(
    name: str = "Test Scenario",
    description: str = "Test Description",
    version: int = 1,
    attack_results: dict[str, list[AttackResult]] | None = None,
):
    """Helper function to create ScenarioResult."""
    if attack_results is None:
        attack_results = {}

    scorer_identifier = ComponentIdentifier(
        class_name="TestScorer",
        class_module="tests.unit.memory",
    )

    return make_scenario_result(
        scenario_name=name,
        scenario_version=version,
        scenario_description=description,
        objective_target_identifier=ComponentIdentifier(class_name="test_target", class_module="test"),
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
    scenario_names = {scenario.scenario_name for scenario in all_scenarios}
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
    assert results[0].scenario_name == "Test Scenario Alpha"


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
    assert results[0].scenario_version == 2


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
    assert results[0].scenario_name == "Scenario 1"
    assert results[0].id == scenario_result1.id


def test_empty_ids_returns_empty(sqlite_instance: MemoryInterface):
    """Test that empty ID list returns empty results."""
    results = sqlite_instance.get_scenario_results(scenario_result_ids=[])
    assert len(results) == 0


def test_attack_results_populated_correctly(sqlite_instance: MemoryInterface):
    """Test that retrieving scenario results populates attack_results correctly."""
    scenario_result = create_scenario_result(name="Multi-Attack Scenario", attack_results={})
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    sid = scenario_result.id
    attack_result1 = _make_attack_result_for_scenario(
        scenario_result_id=sid,
        atomic_attack_name="PromptInjection",
        objective_index=0,
        conversation_id="conv_1",
    )
    attack_result2 = _make_attack_result_for_scenario(
        scenario_result_id=sid,
        atomic_attack_name="PromptInjection",
        objective_index=1,
        conversation_id="conv_2",
        outcome=AttackOutcome.FAILURE,
    )
    attack_result3 = _make_attack_result_for_scenario(
        scenario_result_id=sid,
        atomic_attack_name="Crescendo",
        objective_index=0,
        conversation_id="conv_3",
    )
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

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
    """Hydration sorts each atomic attack's results by ``timestamp`` (which
    monotonically tracks insertion order under normal sequential execution)."""
    scenario_result = create_scenario_result(name="Ordered Scenario", attack_results={})
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    sid = scenario_result.id
    # Insert in a specific order; hydration must surface them in the same order.
    attack_results = [
        _make_attack_result_for_scenario(
            scenario_result_id=sid,
            atomic_attack_name="Attack1",
            objective_index=i,
            conversation_id=f"conv_{i}",
        )
        for i in range(5)
    ]
    for ar in attack_results:
        sqlite_instance.add_attack_results_to_memory(attack_results=[ar])

    results = sqlite_instance.get_scenario_results()
    retrieved_attacks = results[0].attack_results["Attack1"]

    retrieved_conv_ids = [ar.conversation_id for ar in retrieved_attacks]
    assert retrieved_conv_ids == [f"conv_{i}" for i in range(5)]


def test_stores_conversation_ids_only(sqlite_instance: MemoryInterface):
    """Test that scenario results expose AttackResult objects with conversation IDs after hydration."""
    scenario_result = create_scenario_result(name="Test Scenario", attack_results={})
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    ar = _make_attack_result_for_scenario(
        scenario_result_id=scenario_result.id,
        atomic_attack_name="Attack1",
        objective_index=0,
        conversation_id="conv_1",
    )
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar])

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
    scorer_identifier = ComponentIdentifier(
        class_name="TestScorer",
        class_module="test.module",
    )

    scenario_result = make_scenario_result(
        scenario_name="Metadata Test Scenario",
        scenario_version=3,
        scenario_description="A test scenario with metadata",
        params={"param1": "value1", "param2": 42},
        objective_target_identifier=ComponentIdentifier(
            class_name="test_target",
            class_module="test",
            params={"endpoint": "https://example.com"},
        ),
        attack_results={},
        objective_scorer_identifier=scorer_identifier,
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    # Retrieve and verify metadata
    results = sqlite_instance.get_scenario_results()
    assert len(results) == 1

    retrieved = results[0]
    assert retrieved.scenario_name == "Metadata Test Scenario"
    assert retrieved.scenario_description == "A test scenario with metadata"
    assert retrieved.scenario_version == 3
    assert retrieved.scenario_identifier.params["param1"] == "value1"
    assert retrieved.scenario_identifier.params["param2"] == 42
    assert retrieved.objective_target_identifier.params["endpoint"] == "https://example.com"
    # objective_scorer_identifier is now a ComponentIdentifier, check its properties
    assert retrieved.objective_scorer_identifier.class_name == "TestScorer"
    assert retrieved.objective_scorer_identifier.class_module == "test.module"


def test_multiple_scenarios_with_attacks(sqlite_instance: MemoryInterface):
    """Test retrieving multiple scenarios with their attack results populated."""
    scenario1 = create_scenario_result(name="Scenario 1", attack_results={})
    scenario2 = create_scenario_result(name="Scenario 2", attack_results={})
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2])

    all_attack_results = [
        _make_attack_result_for_scenario(
            scenario_result_id=scenario1.id,
            atomic_attack_name="Attack1",
            objective_index=i,
            conversation_id=f"conv_s1_{i}",
        )
        for i in range(5)
    ] + [
        _make_attack_result_for_scenario(
            scenario_result_id=scenario2.id,
            atomic_attack_name="Attack2",
            objective_index=i,
            conversation_id=f"conv_s2_{i}",
        )
        for i in range(3)
    ]
    sqlite_instance.add_attack_results_to_memory(attack_results=all_attack_results)

    # Retrieve all scenarios
    results = sqlite_instance.get_scenario_results()
    assert len(results) == 2

    # Verify each scenario has the correct attack results
    for result in results:
        if result.scenario_name == "Scenario 1":
            assert len(result.attack_results["Attack1"]) == 5
        elif result.scenario_name == "Scenario 2":
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
    assert results[0].scenario_name == "Test Scenario"
    assert results[0].scenario_version == 2


def test_filter_by_labels(sqlite_instance: MemoryInterface, sample_attack_results):
    """Test scenario results with labels."""
    # Create scenario with labels
    scenario_result = make_scenario_result(
        scenario_name="Labeled Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(class_name="test_target", class_module="test"),
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
    scenario1 = make_scenario_result(
        scenario_name="Scenario 1",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(class_name="test_target", class_module="test"),
        attack_results={"Attack1": [attack_result1]},
        labels={"environment": "testing", "team": "red-team"},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2 = make_scenario_result(
        scenario_name="Scenario 2",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(class_name="test_target", class_module="test"),
        attack_results={"Attack2": [attack_result2]},
        labels={"environment": "production", "team": "red-team"},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2])

    # Query requiring both labels to match
    results = sqlite_instance.get_scenario_results(labels={"environment": "testing", "team": "red-team"})
    assert len(results) == 1
    assert results[0].scenario_name == "Scenario 1"


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

    scenario1 = make_scenario_result(
        scenario_name="Recent Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(class_name="test_target", class_module="test"),
        attack_results={"Attack1": [attack_result1]},
        completion_time=now,
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2 = make_scenario_result(
        scenario_name="Yesterday Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(class_name="test_target", class_module="test"),
        attack_results={"Attack2": [attack_result2]},
        completion_time=yesterday,
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario3 = make_scenario_result(
        scenario_name="Old Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(class_name="test_target", class_module="test"),
        attack_results={"Attack3": [attack_result3]},
        completion_time=last_week,
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2, scenario3])

    # Query scenarios after yesterday
    results = sqlite_instance.get_scenario_results(added_after=yesterday)
    assert len(results) == 2
    result_names = {r.scenario_name for r in results}
    assert "Recent Scenario" in result_names
    assert "Yesterday Scenario" in result_names

    # Query scenarios before yesterday
    results = sqlite_instance.get_scenario_results(added_before=yesterday)
    assert len(results) == 2
    result_names = {r.scenario_name for r in results}
    assert "Yesterday Scenario" in result_names
    assert "Old Scenario" in result_names


def test_filter_by_pyrit_version(sqlite_instance: MemoryInterface):
    """Test filtering scenario results by PyRIT version."""
    # Create attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2])

    # Create scenarios with different PyRIT versions
    scenario1 = make_scenario_result(
        scenario_name="Old Version Scenario",
        scenario_version=1,
        pyrit_version="0.4.0",
        objective_target_identifier=ComponentIdentifier(class_name="test_target", class_module="test"),
        attack_results={"Attack1": [attack_result1]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2 = make_scenario_result(
        scenario_name="New Version Scenario",
        scenario_version=1,
        pyrit_version="0.5.0",
        objective_target_identifier=ComponentIdentifier(class_name="test_target", class_module="test"),
        attack_results={"Attack2": [attack_result2]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2])

    # Query by PyRIT version
    results = sqlite_instance.get_scenario_results(pyrit_version="0.5.0")
    assert len(results) == 1
    assert results[0].scenario_name == "New Version Scenario"
    assert results[0].pyrit_version == "0.5.0"


def test_filter_by_target_endpoint(sqlite_instance: MemoryInterface):
    """Test filtering scenario results by target endpoint."""
    # Create attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    attack_result3 = create_attack_result("conv_3", "Objective 3")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2, attack_result3])

    # Create scenarios with different target endpoints
    scenario1 = make_scenario_result(
        scenario_name="Azure Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(
            class_name="OpenAI",
            class_module="test",
            params={"endpoint": "https://myresource.openai.azure.com"},
        ),
        attack_results={"Attack1": [attack_result1]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2 = make_scenario_result(
        scenario_name="OpenAI Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(
            class_name="OpenAI",
            class_module="test",
            params={"endpoint": "https://api.openai.com/v1"},
        ),
        attack_results={"Attack2": [attack_result2]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario3 = make_scenario_result(
        scenario_name="No Endpoint Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(class_name="Local", class_module="test"),
        attack_results={"Attack3": [attack_result3]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2, scenario3])

    # Query by endpoint (case-insensitive substring match)
    results = sqlite_instance.get_scenario_results(objective_target_endpoint="azure")
    assert len(results) == 1
    assert results[0].scenario_name == "Azure Scenario"

    # Query for OpenAI endpoints
    results = sqlite_instance.get_scenario_results(objective_target_endpoint="openai")
    assert len(results) == 2
    result_names = {r.scenario_name for r in results}
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
    scenario1 = make_scenario_result(
        scenario_name="GPT-4 Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(
            class_name="OpenAI",
            class_module="test",
            params={"model_name": "gpt-4-0613"},
        ),
        attack_results={"Attack1": [attack_result1]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2 = make_scenario_result(
        scenario_name="GPT-4o Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(
            class_name="OpenAI", class_module="test", params={"model_name": "gpt-4o"}
        ),
        attack_results={"Attack2": [attack_result2]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario3 = make_scenario_result(
        scenario_name="GPT-3.5 Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(
            class_name="OpenAI",
            class_module="test",
            params={"model_name": "gpt-3.5-turbo"},
        ),
        attack_results={"Attack3": [attack_result3]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2, scenario3])

    # Query by model name (case-insensitive substring match)
    results = sqlite_instance.get_scenario_results(objective_target_model_name="gpt-4")
    assert len(results) == 2
    result_names = {r.scenario_name for r in results}
    assert "GPT-4 Scenario" in result_names
    assert "GPT-4o Scenario" in result_names

    # Query for GPT-3.5
    results = sqlite_instance.get_scenario_results(objective_target_model_name="3.5")
    assert len(results) == 1
    assert results[0].scenario_name == "GPT-3.5 Scenario"


def test_combined_filters(sqlite_instance: MemoryInterface):
    """Test combining multiple filters together."""
    # Create attack results
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2])

    # Create scenarios with various properties
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    scenario1 = make_scenario_result(
        scenario_name="Test Scenario",
        scenario_version=1,
        pyrit_version="0.5.0",
        objective_target_identifier=ComponentIdentifier(
            class_name="OpenAI",
            class_module="test",
            params={"endpoint": "https://api.openai.com", "model_name": "gpt-4"},
        ),
        attack_results={"Attack1": [attack_result1]},
        labels={"environment": "testing"},
        completion_time=now,
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )

    scenario2 = make_scenario_result(
        scenario_name="Test Scenario",
        scenario_version=1,
        pyrit_version="0.4.0",
        objective_target_identifier=ComponentIdentifier(
            class_name="Azure",
            class_module="test",
            params={"endpoint": "https://azure.com", "model_name": "gpt-3.5"},
        ),
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
    assert results[0].pyrit_version == "0.5.0"
    assert "gpt-4" in results[0].objective_target_identifier.params["model_name"]


# =============================================================================
# Scenario linkage (attribution_parent_id foreign key + attribution_data on
# AttackResultEntry) hydration tests
# =============================================================================


def _make_attack_result_for_scenario(
    *,
    scenario_result_id,
    atomic_attack_name,
    objective_index,
    conversation_id=None,
    outcome=AttackOutcome.SUCCESS,
):
    """Build an AttackResult pre-stamped with scenario linkage (mirrors what
    the event handler does when an AttackResultAttribution is on the context)."""
    return AttackResult(
        conversation_id=conversation_id or f"conv-{atomic_attack_name}-{objective_index}",
        objective=f"objective-{atomic_attack_name}-{objective_index}",
        outcome=outcome,
        executed_turns=1,
        attribution_parent_id=str(scenario_result_id),
        attribution_data={"parent_collection": atomic_attack_name},
    )


def test_get_scenario_results_loads_attack_results_via_foreign_key(
    sqlite_instance: MemoryInterface,
):
    """When AttackResultEntry rows carry the attribution_parent_id foreign key,
    hydration picks them up directly — without needing the legacy
    attack_results_json manifest. This is the path that makes mid-AtomicAttack
    interruption-recovery work."""
    scenario_result = create_scenario_result(
        name="ForeignKey-only Scenario",
        attack_results={},  # manifest intentionally empty
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])

    sid = scenario_result.id
    ar1 = _make_attack_result_for_scenario(scenario_result_id=sid, atomic_attack_name="a", objective_index=0)
    ar2 = _make_attack_result_for_scenario(scenario_result_id=sid, atomic_attack_name="a", objective_index=1)
    ar3 = _make_attack_result_for_scenario(scenario_result_id=sid, atomic_attack_name="b", objective_index=0)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar1, ar2, ar3])

    [result] = sqlite_instance.get_scenario_results(scenario_result_ids=[str(sid)])
    assert set(result.attack_results.keys()) == {"a", "b"}
    assert [r.conversation_id for r in result.attack_results["a"]] == [
        "conv-a-0",
        "conv-a-1",
    ]
    assert [r.conversation_id for r in result.attack_results["b"]] == ["conv-b-0"]


def test_get_attack_results_filters_by_scenario_result_id(
    sqlite_instance: MemoryInterface,
):
    """get_attack_results gains a scenario_result_id filter — replaces the
    removed error_attack_result_ids_json lookup path."""
    scenario_result = create_scenario_result(name="Filter Scenario")
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])
    sid = scenario_result.id

    ok = _make_attack_result_for_scenario(scenario_result_id=sid, atomic_attack_name="a", objective_index=0)
    err = _make_attack_result_for_scenario(
        scenario_result_id=sid,
        atomic_attack_name="a",
        objective_index=1,
        outcome=AttackOutcome.ERROR,
    )
    # An unrelated AttackResult NOT linked to this scenario should be excluded.
    unrelated = create_attack_result("unrelated-conv", "unrelated-obj")
    sqlite_instance.add_attack_results_to_memory(attack_results=[ok, err, unrelated])

    all_for_scenario = sqlite_instance.get_attack_results(scenario_result_id=str(sid))
    assert {r.conversation_id for r in all_for_scenario} == {
        ok.conversation_id,
        err.conversation_id,
    }

    only_errors = sqlite_instance.get_attack_results(
        scenario_result_id=str(sid),
        outcome=AttackOutcome.ERROR.value,
    )
    assert [r.conversation_id for r in only_errors] == [err.conversation_id]


def test_delete_scenario_sets_attack_result_foreign_key_to_null(
    sqlite_instance: MemoryInterface,
):
    """ON DELETE SET NULL: deleting the parent ScenarioResultEntry nulls the
    attribution_parent_id foreign key on its linked AttackResultEntries but
    the AttackResultEntries survive (attribution_data is retained as
    historical provenance).

    Note: SQLite does not enforce foreign keys by default; this test enables
    them on the session for the duration of the delete to verify the
    ON DELETE SET NULL clause works. Production deployments using SQL Server
    enforce foreign keys by default.
    """
    from contextlib import closing

    from sqlalchemy import text as _sql_text

    from pyrit.memory.memory_models import AttackResultEntry, ScenarioResultEntry

    scenario_result = create_scenario_result(name="To Be Deleted")
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])
    sid = scenario_result.id

    ar = _make_attack_result_for_scenario(scenario_result_id=sid, atomic_attack_name="a", objective_index=0)
    sqlite_instance.add_attack_results_to_memory(attack_results=[ar])

    # Enable foreign keys for the delete and verify the SET NULL clause fires.
    with closing(sqlite_instance.get_session()) as session:
        session.execute(_sql_text("PRAGMA foreign_keys = ON"))
        session.query(ScenarioResultEntry).filter_by(id=sid).delete()
        session.commit()

    # The AttackResult survives, but its foreign key is now NULL.
    # attribution_data is retained as historical provenance.
    with closing(sqlite_instance.get_session()) as session:
        entry = session.query(AttackResultEntry).filter_by(conversation_id=ar.conversation_id).one()
        assert entry.attribution_parent_id is None
        assert entry.attribution_data == {"parent_collection": "a"}


def test_update_scenario_run_state_targeted_update_preserves_manifest(
    sqlite_instance: MemoryInterface,
):
    """update_scenario_run_state must be a targeted UPDATE — it must not
    re-serialize the whole row and clobber the manifest column during the
    deprecation window."""
    scenario_result = create_scenario_result(
        name="Targeted Update",
        attack_results={"a": []},  # baseline manifest
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario_result])
    sid = str(scenario_result.id)

    sqlite_instance.update_scenario_run_state(
        scenario_result_id=sid,
        scenario_run_state="FAILED",
        error_message="boom",
        error_type="RuntimeError",
    )

    # State and error fields updated.
    [hydrated] = sqlite_instance.get_scenario_results(scenario_result_ids=[sid])
    assert hydrated.scenario_run_state == "FAILED"
    assert hydrated.error_message == "boom"
    assert hydrated.error_type == "RuntimeError"


def test_get_scenario_results_by_target_identifier_filter_hash(
    sqlite_instance: MemoryInterface,
):
    """Test filtering scenario results by identifier filter."""
    target_id_1 = ComponentIdentifier(
        class_name="OpenAI",
        class_module="test",
        params={"endpoint": "https://api.openai.com", "model_name": "gpt-4"},
    )
    target_id_2 = ComponentIdentifier(
        class_name="Azure",
        class_module="test",
        params={"endpoint": "https://azure.com", "model_name": "gpt-3.5"},
    )

    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2])

    scenario1 = make_scenario_result(
        scenario_name="Scenario OpenAI",
        scenario_version=1,
        objective_target_identifier=target_id_1,
        attack_results={"Attack1": [attack_result1]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    scenario2 = make_scenario_result(
        scenario_name="Scenario Azure",
        scenario_version=1,
        objective_target_identifier=target_id_2,
        attack_results={"Attack2": [attack_result2]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2])

    # Filter by target hash
    results = sqlite_instance.get_scenario_results(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.TARGET,
                property_path="$.hash",
                value=target_id_1.hash,
                partial_match=False,
            )
        ],
    )
    assert len(results) == 1
    assert results[0].scenario_name == "Scenario OpenAI"


def test_get_scenario_results_by_target_identifier_filter_endpoint(
    sqlite_instance: MemoryInterface,
):
    """Test filtering scenario results by identifier filter with endpoint."""
    target_id_1 = ComponentIdentifier(
        class_name="OpenAI",
        class_module="test",
        params={"endpoint": "https://api.openai.com", "model_name": "gpt-4"},
    )
    target_id_2 = ComponentIdentifier(
        class_name="Azure",
        class_module="test",
        params={"endpoint": "https://azure.com", "model_name": "gpt-3.5"},
    )

    attack_result1 = create_attack_result("conv_1", "Objective 1")
    attack_result2 = create_attack_result("conv_2", "Objective 2")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1, attack_result2])

    scenario1 = make_scenario_result(
        scenario_name="Scenario OpenAI",
        scenario_version=1,
        objective_target_identifier=target_id_1,
        attack_results={"Attack1": [attack_result1]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    scenario2 = make_scenario_result(
        scenario_name="Scenario Azure",
        scenario_version=1,
        objective_target_identifier=target_id_2,
        attack_results={"Attack2": [attack_result2]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1, scenario2])

    # Filter by endpoint partial match
    results = sqlite_instance.get_scenario_results(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.TARGET,
                property_path="$.endpoint",
                value="openai",
                partial_match=True,
            )
        ],
    )
    assert len(results) == 1
    assert results[0].scenario_name == "Scenario OpenAI"


def test_get_scenario_results_by_target_identifier_filter_no_match(
    sqlite_instance: MemoryInterface,
):
    """Test that TargetIdentifierFilter returns empty when nothing matches."""
    attack_result1 = create_attack_result("conv_1", "Objective 1")
    sqlite_instance.add_attack_results_to_memory(attack_results=[attack_result1])

    scenario1 = make_scenario_result(
        scenario_name="Test Scenario",
        scenario_version=1,
        objective_target_identifier=ComponentIdentifier(
            class_name="OpenAI",
            class_module="test",
            params={"endpoint": "https://api.openai.com"},
        ),
        attack_results={"Attack1": [attack_result1]},
        objective_scorer_identifier=get_mock_scorer_identifier(),
    )
    sqlite_instance.add_scenario_results_to_memory(scenario_results=[scenario1])

    results = sqlite_instance.get_scenario_results(
        identifier_filters=[
            IdentifierFilter(
                identifier_type=IdentifierType.TARGET,
                property_path="$.hash",
                value="nonexistent_hash",
                partial_match=False,
            )
        ],
    )
    assert len(results) == 0
