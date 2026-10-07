# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Typed technique definitions, deferred construction, and atomic admission."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from pyrit.converter import Base64Converter
from pyrit.executor.attack import AttackScoringConfig, PromptSendingAttack, RedTeamingAttack
from pyrit.models import ComponentType, SeedPrompt, SeedSimulatedConversation, StructuredParameterValue
from pyrit.models.technique_definition import TechniqueDefinition
from pyrit.registry import (
    AttackRegistry,
    AttackTechniqueRegistry,
    ConverterRegistry,
    Registry,
    ScorerRegistry,
    TargetRegistry,
)
from pyrit.registry.resolution import register_structured_input
from pyrit.scenario.core import AttackTechniqueFactory
from pyrit.score import FloatScaleThresholdScorer, SelfAskScaleScorer, SubStringScorer
from unit.mocks import MockPromptTarget


@dataclass
class CustomSettings(StructuredParameterValue):
    prompts: list[SeedPrompt]
    enabled: bool = True
    limit: int = 9

    @classmethod
    def get_registry_input_variants(cls) -> dict[str, type[StructuredParameterValue]]:
        return {"custom_settings": cls}


class CustomAttack(PromptSendingAttack):
    def __init__(self, *, objective_target: MockPromptTarget, settings: CustomSettings) -> None:
        super().__init__(objective_target=objective_target)
        self.settings = settings


class UndeclaredSettings(BaseModel):
    enabled: bool


class UndeclaredAttack(PromptSendingAttack):
    def __init__(self, *, objective_target: MockPromptTarget, settings: dict[str, UndeclaredSettings]) -> None:
        super().__init__(objective_target=objective_target)
        self.settings = settings


@dataclass
class ReferencedSettings(CustomSettings):
    target: MockPromptTarget | None = None


class ModelSettings(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    target: MockPromptTarget
    enabled: bool = True


class ModelAttack(PromptSendingAttack):
    def __init__(self, *, objective_target: MockPromptTarget, settings: ModelSettings) -> None:
        super().__init__(objective_target=objective_target)
        self.settings = settings


@pytest.fixture
def registry(patch_central_database: Any) -> Iterator[AttackTechniqueRegistry]:
    with patch.dict(Registry._singletons, {}, clear=True):
        targets = TargetRegistry.get_registry_singleton()
        targets.instances.register(MockPromptTarget(), name="local")
        ConverterRegistry.get_registry_singleton().instances.register(Base64Converter(), name="b64")
        ScorerRegistry.get_registry_singleton().instances.register(SubStringScorer(substring="yes"), name="yes")
        yield AttackTechniqueRegistry.get_registry_singleton()


def recipe(*, name: str = "example", attack_type: str = "PromptSendingAttack", **kwargs: Any) -> TechniqueDefinition:
    return TechniqueDefinition.model_validate({"name": name, "attack_type": attack_type, **kwargs})


def structured(name: str, **parameters: Any) -> dict[str, Any]:
    return {"type": name, "parameters": parameters}


def test_nested_configs_preserve_values_without_constructing_attack(registry: AttackTechniqueRegistry) -> None:
    converters = structured(
        "AttackConverterConfig",
        request_converters=[
            structured(
                "ConverterConfiguration",
                converters=["b64", "b64"],
                indexes_to_apply=[],
                prompt_data_types_to_apply=None,
            )
        ],
        response_converters=[],
    )
    scoring = structured(
        "AttackScoringConfig", objective_scorer="yes", auxiliary_scorers=["yes", "yes"], use_score_as_feedback=False
    )
    with patch.object(
        PromptSendingAttack, "__init__", autospec=True, side_effect=AssertionError("Attack must remain deferred")
    ):
        factory = registry.register_definition(
            recipe(
                attack_args={
                    "max_attempts_on_failure": 0,
                    "attack_converter_config": converters,
                    "attack_scoring_config": scoring,
                    "prepended_conversation_config": structured(
                        "PrependedConversationConfig", apply_converters_to_roles=[], message_normalizer=None
                    ),
                }
            )
        )
    config = factory.get_configuration()
    assert config["attack_args"]["max_attempts_on_failure"] == 0
    converter_settings = config["attack_args"]["attack_converter_config"]["parameters"]
    request = converter_settings["request_converters"][0]["parameters"]
    assert len(request["converters"]) == 2
    assert request["converters"][0] == request["converters"][1]
    assert request["indexes_to_apply"] == []
    assert request["prompt_data_types_to_apply"] is None
    assert converter_settings["response_converters"] == []
    assert config["attack_args"]["attack_scoring_config"]["parameters"]["use_score_as_feedback"] is False
    assert config["attack_args"]["prepended_conversation_config"]["parameters"]["apply_converters_to_roles"] == []
    assert factory.technique_tags == []


def test_seed_groups_and_simulated_conversation_are_real_models(registry: AttackTechniqueRegistry) -> None:
    factory = registry.register_definition(
        recipe(
            seed_technique={
                "insertion_index": 0,
                "prompt_placement": "prepend",
                "seeds": [
                    structured(
                        "SeedPrompt",
                        value="prefix",
                        role="system",
                        sequence=0,
                        parameters=[],
                        is_general_technique=True,
                    ),
                    structured(
                        "SeedSimulatedConversation",
                        num_turns=2,
                        sequence=1,
                        adversarial_chat_system_prompt=structured("SeedPrompt", value="attacker"),
                        simulated_target_system_prompt=structured("SeedPrompt", value="target"),
                        next_message_system_prompt=None,
                    ),
                ],
            }
        )
    )
    seeds = factory.seed_technique
    assert seeds is not None
    assert seeds.insertion_index == 0
    assert seeds.prompt_placement == "prepend"
    assert any(isinstance(seed, SeedPrompt) for seed in seeds.seeds)
    simulated = next(seed for seed in seeds.seeds if isinstance(seed, SeedSimulatedConversation))
    assert isinstance(simulated, SeedSimulatedConversation)
    assert simulated.num_turns == 2
    assert simulated.adversarial_chat_system_prompt.value == "attacker"
    assert factory.uses_adversarial
    assert factory.uses_default_adversarial_target


def test_seed_uuid_datetime_and_json_metadata(registry: AttackTechniqueRegistry) -> None:
    seed_id = "20f9fe29-d2d8-46ce-870c-63998b11603c"
    factory = registry.register_definition(
        recipe(
            seed_technique={
                "seeds": [
                    structured(
                        "SeedPrompt",
                        value="prefix",
                        is_general_technique=True,
                        id=seed_id,
                        date_added="2026-01-01T00:00:00Z",
                        metadata={"nested": [False, 0, None, {}]},
                        response_json_schema={"type": "object", "properties": {}},
                    )
                ]
            }
        )
    )
    assert factory.seed_technique is not None
    seed = factory.seed_technique.seeds[0]
    assert seed.id == UUID(seed_id)
    assert seed.date_added == datetime(2026, 1, 1, tzinfo=UTC)
    assert seed.metadata == {"nested": [False, 0, None, {}]}


def test_nested_undeclared_models_are_not_constructed(registry: AttackTechniqueRegistry) -> None:
    AttackRegistry.get_registry_singleton().register_class(UndeclaredAttack)
    before = registry.instances.revision
    with patch.object(UndeclaredSettings, "__init__", autospec=True) as constructor:
        with pytest.raises(ValueError, match="requires a Python value"):
            registry.register_definition(
                recipe(
                    attack_type="UndeclaredAttack",
                    attack_args={"settings": {"nested": {"enabled": True}}},
                )
            )
        constructor.assert_not_called()
    assert registry.instances.revision == before
    assert registry.instances.get("example") is None


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_json_values_are_rejected(number: float, registry: AttackTechniqueRegistry) -> None:
    before = registry.instances.revision
    with pytest.raises(ValidationError):
        recipe(attack_args={"nested": {"value": number}})
    assert registry.instances.revision == before


def test_custom_declared_variant_resolves_nested_target(registry: AttackTechniqueRegistry) -> None:
    AttackRegistry.get_registry_singleton().register_class(CustomAttack)
    with (
        patch.dict("pyrit.registry.resolution._REGISTERED_STRUCTURED_INPUTS"),
        patch.dict("pyrit.registry.resolution._STRUCTURED_INPUT_REFERENCES"),
    ):
        register_structured_input(
            base_type=CustomSettings,
            variants={"referenced": ReferencedSettings},
            references={"target": ComponentType.TARGET},
        )
        factory = registry.register_definition(
            recipe(
                attack_type="CustomAttack",
                attack_args={"settings": structured("referenced", prompts=[], enabled=False, limit=0, target="local")},
            )
        )
        target = TargetRegistry.get_registry_singleton().instances.get("local")
        assert isinstance(target, MockPromptTarget)
        technique = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
        assert isinstance(technique.attack, CustomAttack)
        assert isinstance(technique.attack.settings, ReferencedSettings)
        assert technique.attack.settings.target is target
        assert technique.attack.settings.enabled is False and technique.attack.settings.limit == 0
        assert target.prompt_sent == []


def test_tap_scoring_variant_resolves_live_scorers(registry: AttackTechniqueRegistry) -> None:
    from pyrit.executor.attack.multi_turn.tree_of_attacks import TAPAttackScoringConfig

    target = TargetRegistry.get_registry_singleton().instances.get("local")
    assert isinstance(target, MockPromptTarget)
    scorer = FloatScaleThresholdScorer(scorer=SelfAskScaleScorer.from_scale(chat_target=target), threshold=0.5)
    ScorerRegistry.get_registry_singleton().instances.register(scorer, name="scale")
    factory = registry.register_definition(
        recipe(
            attack_type="TreeOfAttacksWithPruningAttack",
            attack_args={
                "attack_scoring_config": structured(
                    "TAPAttackScoringConfig",
                    objective_scorer="scale",
                    refusal_scorer="yes",
                    use_score_as_feedback=False,
                )
            },
            factory_options={"adversarial_chat": "local", "scorer_override_policy": "skip"},
        )
    )
    technique = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
    scoring = technique.attack.get_attack_scoring_config()
    assert isinstance(scoring, TAPAttackScoringConfig)
    assert scoring.objective_scorer is scorer
    assert scoring.use_score_as_feedback is False
    assert target.prompt_sent == []


def test_custom_model_variant_resolves_and_validates_reference(registry: AttackTechniqueRegistry) -> None:
    attacks = AttackRegistry.get_registry_singleton()
    attacks.register_class(ModelAttack)
    with (
        patch.dict("pyrit.registry.resolution._REGISTERED_STRUCTURED_INPUTS"),
        patch.dict("pyrit.registry.resolution._STRUCTURED_INPUT_REFERENCES"),
    ):
        register_structured_input(
            base_type=ModelSettings,
            variants={"model_settings": ModelSettings},
            references={"target": ComponentType.TARGET},
        )
        factory = registry.register_definition(
            recipe(
                attack_type="ModelAttack",
                attack_args={"settings": structured("model_settings", target="local", enabled=False)},
            )
        )
        target = TargetRegistry.get_registry_singleton().instances.get("local")
        assert isinstance(target, MockPromptTarget)
        technique = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
        assert isinstance(technique.attack, ModelAttack)
        assert technique.attack.settings.target is target
        assert technique.attack.settings.enabled is False
        before = registry.instances.revision
        with pytest.raises(ValueError, match="wrong component type"):
            registry.register_definition(
                recipe(
                    name="invalid_reference",
                    attack_type="ModelAttack",
                    attack_args={"settings": structured("model_settings", target={"name": "local"})},
                )
            )
        assert registry.instances.revision == before
        assert target.prompt_sent == []


def test_custom_attack_alias_and_declared_config_are_not_gui_specific(registry: AttackTechniqueRegistry) -> None:
    AttackRegistry.get_registry_singleton().register_class(CustomAttack, name="custom")
    definition = recipe(
        attack_type="custom",
        attack_args={
            "settings": structured(
                "custom_settings", prompts=[structured("SeedPrompt", value="one")], enabled=False, limit=0
            ),
        },
    )
    factory = registry.register_definition(definition)
    target = TargetRegistry.get_registry_singleton().instances.get("local")
    first = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
    second = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
    assert isinstance(first.attack, CustomAttack)
    assert first.attack is not second.attack
    assert first.attack.settings.enabled is False
    assert first.attack.settings.limit == 0
    assert isinstance(first.attack.settings.prompts[0], SeedPrompt)


def test_null_and_omission_remain_distinct(registry: AttackTechniqueRegistry) -> None:
    omitted = registry.build_from_definition(recipe())
    explicit = registry.build_from_definition(recipe(attack_args={"attack_converter_config": None}))
    assert "attack_converter_config" not in omitted.get_configuration()["attack_args"]
    assert explicit.get_configuration()["attack_args"] == {"attack_converter_config": None}


def test_adversarial_reference_and_prompts_are_deferred_and_safe(registry: AttackTechniqueRegistry) -> None:
    with patch.object(
        RedTeamingAttack, "__init__", autospec=True, side_effect=AssertionError("No attack construction")
    ):
        factory = registry.register_definition(
            recipe(
                attack_type="RedTeamingAttack",
                factory_options={
                    "adversarial_chat": "local",
                    "adversarial_system_prompt": "",
                    "adversarial_seed_prompt": structured("SeedPrompt", value="seed"),
                    "adversarial_prompt_template": "{{ feedback_text }}",
                    "use_score_as_feedback": False,
                },
            )
        )
    assert factory.adversarial_chat is TargetRegistry.get_registry_singleton().instances.get("local")
    assert factory.uses_adversarial
    assert not factory.uses_default_adversarial_target
    options = factory.get_configuration()["factory_options"]
    assert set(options["adversarial_chat"]) == {"class_name", "hash"}
    assert options["adversarial_system_prompt"] == ""
    assert options["use_score_as_feedback"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attack_type": "does_not_exist"},
        {"attack_args": {"unknown": 1}},
        {"attack_args": {"objective_target": "local"}},
        {"attack_args": {"max_attempts_on_failure": False}},
        {"attack_args": {"max_attempts_on_failure": "2"}},
        {"attack_args": {"max_attempts_on_failure": None}},
        {"attack_args": {"attack_converter_config": {"type": "unknown"}}},
        {"attack_args": {"attack_converter_config": structured("AttackConverterConfig", response_converters=None)}},
        {
            "attack_args": {
                "attack_converter_config": structured(
                    "AttackConverterConfig",
                    request_converters=[structured("ConverterConfiguration", converters=["missing"])],
                )
            }
        },
        {"attack_args": {"attack_scoring_config": structured("AttackScoringConfig", objective_scorer="missing")}},
        {
            "attack_args": {
                "prepended_conversation_config": structured("PrependedConversationConfig", message_normalizer={})
            }
        },
        {"factory_options": {"adversarial_chat": "missing"}},
        {"factory_options": {"adversarial_system_prompt": "ignored"}},
        {"seed_technique": {"seeds": [structured("SeedPrompt", unknown="x")]}},
        {"seed_technique": {"seeds": [structured("SeedPrompt")]}},
        {"seed_technique": {"seeds": [structured("SeedSimulatedConversation", num_turns=0)]}},
        {
            "seed_technique": {
                "seeds": [structured("SeedSimulatedConversation", adversarial_chat_system_prompt_path="local.yaml")]
            }
        },
    ],
)
def test_invalid_inputs_are_atomic(registry: AttackTechniqueRegistry, kwargs: dict[str, Any]) -> None:
    before = registry.catalog_revision
    with pytest.raises((ValueError, TypeError)):
        registry.register_definition(recipe(**kwargs))
    assert registry.instances.get_names() == []
    assert registry.catalog_revision == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "all"},
        {"name": "types"},
        {"name": "TYPES"},
        {"name": "DEFAULT"},
        {"name": "bad-name"},
        {"name": "x" * 65},
        {"tags": ["all"]},
        {"tags": ["alpha", "Alpha"]},
        {"tags": ["bad-tag"]},
        {"factory_options": {"uses_adversarial": "false"}},
        {"factory_options": {"unknown": True}},
        {"seed_technique": {"seeds": []}},
        {"seed_technique": {"seeds": [structured("SeedPrompt", value="x")], "prompt_placement": "unknown"}},
    ],
)
def test_invalid_definition_models(registry: AttackTechniqueRegistry, kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        recipe(**kwargs)
    assert registry.instances.get_names() == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "existing"},
        {"name": "Existing"},
        {"name": "group"},
        {"tags": ["existing"]},
        {"tags": ["GROUP"]},
        {"name": "same", "tags": ["Same"]},
        {"name": "first_letter"},
        {"name": "image"},
        {"name": "prompt_sending"},
        {"tags": ["FIRST_LETTER"]},
    ],
)
def test_selector_collisions_do_not_change_pool(registry: AttackTechniqueRegistry, kwargs: dict[str, Any]) -> None:
    registry.instances.register(
        AttackTechniqueFactory(name="existing", attack_class=PromptSendingAttack, technique_tags=["group"]),
        name="existing",
    )
    before = registry.catalog_revision
    with pytest.raises(ValueError):
        registry.register_definition(recipe(**kwargs))
    assert registry.instances.get_names() == ["existing"]
    assert registry.catalog_revision == before


def test_custom_required_fields_fail_before_registration(registry: AttackTechniqueRegistry) -> None:
    AttackRegistry.get_registry_singleton().register_class(CustomAttack)
    with pytest.raises(ValueError, match="Missing required.*settings"):
        registry.register_definition(recipe(attack_type="CustomAttack"))
    assert registry.instances.get_names() == []
