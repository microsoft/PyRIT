# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from contextlib import closing
from dataclasses import dataclass

import pytest

from pyrit.memory import MemoryInterface
from pyrit.memory.memory_models import TargetIdentifierEntry
from pyrit.models import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackTechniqueIdentifier,
    Conversation,
    ConverterIdentifier,
    ScenarioIdentifier,
    ScorerIdentifier,
    SeedIdentifier,
    TargetIdentifier,
)


@dataclass(frozen=True)
class IdentifierGraph:
    objective_target: TargetIdentifier
    adversarial_target: TargetIdentifier
    nested_converter: ConverterIdentifier
    converter: ConverterIdentifier
    scorer: ScorerIdentifier
    scenario: ScenarioIdentifier
    technique_seed: SeedIdentifier
    dataset_seed: SeedIdentifier
    attack: AttackIdentifier
    technique: AttackTechniqueIdentifier
    atomic_attack: AtomicAttackIdentifier


@pytest.fixture
def identifier_graph(sqlite_instance: MemoryInterface) -> IdentifierGraph:
    objective_target = TargetIdentifier(
        class_name="ObjectiveTarget",
        class_module="tests.unit.memory",
        endpoint="https://objective.test",
        model_name="objective-model",
        temperature=0.5,
    )
    adversarial_target = TargetIdentifier(
        class_name="AdversarialTarget",
        class_module="tests.unit.memory",
        endpoint="https://adversarial.test",
        model_name="adversarial-model",
    )
    nested_converter = ConverterIdentifier(
        class_name="NestedConverter",
        class_module="tests.unit.memory",
        supported_input_types=["text", "image_path"],
        supported_output_types=["text", "audio_path"],
        converter_target=adversarial_target,
    )
    converter = ConverterIdentifier(
        class_name="CompositeConverter",
        class_module="tests.unit.memory",
        supported_input_types=["text", "image_path"],
        supported_output_types=["text", "audio_path"],
        sub_converter=nested_converter,
    )
    scorer = ScorerIdentifier(
        class_name="TestScorer",
        class_module="tests.unit.memory",
        scorer_type="true_false",
        score_aggregator="AND_",
        prompt_target=adversarial_target,
    )
    technique_seed = SeedIdentifier(
        class_name="TechniqueSeed",
        class_module="tests.unit.memory",
        value="technique seed",
        value_sha256="technique-sha",
        data_type="text",
        dataset_name="techniques",
        is_general_technique=True,
    )
    dataset_seed = SeedIdentifier(
        class_name="DatasetSeed",
        class_module="tests.unit.memory",
        value="dataset seed",
        value_sha256="dataset-sha",
        data_type="text",
        dataset_name="dataset-a",
        is_general_technique=False,
    )
    attack = AttackIdentifier(
        class_name="TestAttack",
        class_module="tests.unit.memory",
        adversarial_system_prompt="system prompt",
        adversarial_seed_prompt="seed prompt",
        objective_target=objective_target,
        adversarial_chat=adversarial_target,
        objective_scorer=scorer,
        request_converters=[converter],
    )
    technique = AttackTechniqueIdentifier(
        class_name="TestTechnique",
        class_module="tests.unit.memory",
        attack=attack,
        technique_seeds=[technique_seed],
    )
    atomic_attack = AtomicAttackIdentifier(
        class_name="AtomicAttack",
        class_module="tests.unit.memory",
        attack_technique=technique,
        seed_identifiers=[technique_seed, dataset_seed],
    )
    scenario = ScenarioIdentifier(
        class_name="TestScenario",
        class_module="tests.unit.memory",
        version=2,
        techniques=["TestTechnique", "OtherTechnique"],
        datasets=["dataset-a", "dataset-b"],
        objective_target=objective_target,
        objective_scorer=scorer,
    )

    with closing(sqlite_instance.get_session()) as session:
        sqlite_instance._persist_identifier(session=session, identifier=atomic_attack)
        sqlite_instance._persist_identifier(session=session, identifier=scenario)
        session.commit()

    return IdentifierGraph(
        objective_target=objective_target,
        adversarial_target=adversarial_target,
        nested_converter=nested_converter,
        converter=converter,
        scorer=scorer,
        scenario=scenario,
        technique_seed=technique_seed,
        dataset_seed=dataset_seed,
        attack=attack,
        technique=technique,
        atomic_attack=atomic_attack,
    )


def test_get_target_identifiers_by_hash_and_promoted_field(sqlite_instance: MemoryInterface) -> None:
    target = TargetIdentifier(
        class_name="TestTarget",
        class_module="tests.unit.memory",
        endpoint="https://example.test",
        model_name="test-model",
        supported_auth_modes=["api_key", "identity"],
    )
    sqlite_instance.add_conversation_to_memory(
        conversation=Conversation(conversation_id="identifier-query", target_identifier=target)
    )

    identifiers = sqlite_instance.get_target_identifiers(
        identifier_hashes=[target.hash],
        model_name="test-model",
        supported_auth_modes=["identity", "api_key"],
    )

    assert identifiers == [target]
    assert isinstance(identifiers[0], TargetIdentifier)
    assert sqlite_instance.get_target_identifiers(supported_auth_modes=["api_key"]) == []
    assert sqlite_instance.get_target_identifiers(supported_auth_modes=["API_KEY", "identity"]) == []


def test_get_identifiers_reconstructs_each_typed_graph(
    sqlite_instance: MemoryInterface, identifier_graph: IdentifierGraph
) -> None:
    queries = [
        (
            sqlite_instance.get_target_identifiers(
                identifier_hashes=[identifier_graph.objective_target.hash],
                endpoint="https://objective.test",
                temperature=0.5,
            ),
            identifier_graph.objective_target,
        ),
        (
            sqlite_instance.get_converter_identifiers(
                supported_input_types=["image_path", "text"],
                supported_output_types=["audio_path", "text"],
                sub_converter_hash=identifier_graph.nested_converter.hash,
            ),
            identifier_graph.converter,
        ),
        (
            sqlite_instance.get_scorer_identifiers(
                scorer_type="true_false",
                score_aggregator="AND_",
                prompt_target_hash=identifier_graph.adversarial_target.hash,
            ),
            identifier_graph.scorer,
        ),
        (
            sqlite_instance.get_scenario_identifiers(
                version=2,
                techniques=["OtherTechnique", "TestTechnique"],
                datasets=["dataset-b", "dataset-a"],
                objective_target_hash=identifier_graph.objective_target.hash,
                objective_scorer_hash=identifier_graph.scorer.hash,
            ),
            identifier_graph.scenario,
        ),
        (
            sqlite_instance.get_seed_identifiers(
                value_sha256="dataset-sha",
                data_type="text",
                dataset_name="dataset-a",
                is_general_technique=False,
            ),
            identifier_graph.dataset_seed,
        ),
        (
            sqlite_instance.get_attack_identifiers(
                adversarial_system_prompt="system prompt",
                adversarial_seed_prompt="seed prompt",
                objective_target_hash=identifier_graph.objective_target.hash,
                adversarial_chat_hash=identifier_graph.adversarial_target.hash,
                objective_scorer_hash=identifier_graph.scorer.hash,
            ),
            identifier_graph.attack,
        ),
        (
            sqlite_instance.get_attack_technique_identifiers(
                attack_identifier_hash=identifier_graph.attack.hash,
            ),
            identifier_graph.technique,
        ),
        (
            sqlite_instance.get_atomic_attack_identifiers(
                attack_technique_identifier_hash=identifier_graph.technique.hash,
            ),
            identifier_graph.atomic_attack,
        ),
    ]

    for identifiers, expected in queries:
        assert identifiers == [expected]
        assert type(identifiers[0]) is type(expected)


def test_get_identifiers_common_filters_and_result_semantics(
    sqlite_instance: MemoryInterface, identifier_graph: IdentifierGraph
) -> None:
    targets = sqlite_instance.get_target_identifiers()
    assert [identifier.hash for identifier in targets] == sorted(
        [identifier_graph.objective_target.hash, identifier_graph.adversarial_target.hash]
    )

    duplicate_hashes = [identifier_graph.objective_target.hash, identifier_graph.objective_target.hash]
    assert sqlite_instance.get_target_identifiers(identifier_hashes=duplicate_hashes) == [
        identifier_graph.objective_target
    ]
    assert sqlite_instance.get_target_identifiers(identifier_hashes=[]) == []
    assert sqlite_instance.get_target_identifiers(class_name="missing") == []


def test_get_identifiers_rejects_missing_identifier_json(sqlite_instance: MemoryInterface) -> None:
    identifier_hash = "0" * 64
    sqlite_instance._insert_entry(TargetIdentifierEntry(hash=identifier_hash, identifier_json=None))

    with pytest.raises(ValueError, match="has no identifier JSON"):
        sqlite_instance.get_target_identifiers(identifier_hashes=[identifier_hash])


def test_get_identifiers_rejects_hash_mismatch(sqlite_instance: MemoryInterface) -> None:
    target = TargetIdentifier(class_name="Target", class_module="tests.unit.memory")
    identifier_hash = "f" * 64
    sqlite_instance._insert_entry(TargetIdentifierEntry(hash=identifier_hash, identifier_json=target.model_dump()))

    with pytest.raises(ValueError, match="does not match its stored JSON hash"):
        sqlite_instance.get_target_identifiers(identifier_hashes=[identifier_hash])
