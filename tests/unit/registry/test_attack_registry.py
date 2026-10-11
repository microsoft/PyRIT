# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Attack class discovery, constructor metadata, and shared resolution contracts."""

import inspect
from abc import abstractmethod
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from pyrit.converter import Base64Converter
from pyrit.executor import attack as attack_package
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackScoringConfig,
    AttackStrategy,
    PrependedConversationConfig,
    PromptSendingAttack,
    PromptSendingAttackParameters,
    RedTeamingAttack,
    TAPAttack,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.models import AttackIdentifier
from pyrit.models.parameter import ComponentType
from pyrit.prompt_normalizer import ConverterConfiguration, PromptNormalizer
from pyrit.registry import AttackRegistry, AttackTechniqueRegistry, Registry, RegistryMetadata, TargetRegistry
from pyrit.score import SubStringScorer
from unit.mocks import MockPromptTarget


class CustomAttack(PromptSendingAttack):
    """Custom attack with an inherited constructor."""


@pytest.fixture
def registry() -> Iterator[AttackRegistry]:
    with patch.dict(Registry._singletons):
        for registry_type in (AttackRegistry, AttackTechniqueRegistry, TargetRegistry):
            registry_type.reset_registry_singleton()
        yield AttackRegistry.get_registry_singleton()


def test_discovery_does_not_require_technique_factories(registry: AttackRegistry) -> None:
    techniques = AttackTechniqueRegistry.get_registry_singleton()
    assert techniques.get_class_names() == []
    assert techniques.instances.get_names() == []

    names = registry.get_class_names()

    assert {"PromptSendingAttack", "RedTeamingAttack", "PAIRAttack", "SequentialAttack"} <= set(names)
    assert "CustomAttack" not in names
    assert "AttackStrategy" not in names
    assert "SingleTurnAttackStrategy" not in names
    assert "MultiTurnAttackStrategy" not in names
    assert all(not inspect.isabstract(registry.get_class(name)) for name in names)
    assert all(issubclass(registry.get_class(name), AttackStrategy) for name in names)
    assert techniques.instances.get_names() == []
    assert not hasattr(registry, "instances")


def test_discovery_uses_canonical_class_name_for_export_alias(registry: AttackRegistry) -> None:
    assert TAPAttack is TreeOfAttacksWithPruningAttack
    assert registry.get_class("TreeOfAttacksWithPruningAttack") is TAPAttack
    assert "TAPAttack" not in registry.get_class_names()


def test_discovery_skips_abstract_and_deprecated_alias_classes(registry: AttackRegistry) -> None:
    class AbstractAttack(PromptSendingAttack):
        @abstractmethod
        def extra_step(self) -> None: ...

    class DeprecatedAttack(PromptSendingAttack):
        """Deprecated alias for PromptSendingAttack."""

    with (
        patch.object(AbstractAttack, "__module__", attack_package.__name__),
        patch.object(DeprecatedAttack, "__module__", attack_package.__name__),
    ):
        names = registry.get_class_names()

    assert "AbstractAttack" not in names
    assert "DeprecatedAttack" not in names
    assert "PromptSendingAttack" in names


def test_discovery_skips_unavailable_optional_exports(registry: AttackRegistry) -> None:
    original_getattr = attack_package.__getattr__

    def get_export(name: str) -> object:
        if name == "UnavailableOptionalAttack":
            raise ImportError("Optional dependency is not installed")
        return original_getattr(name)

    with (
        patch.object(attack_package, "__all__", [*attack_package.__all__, "UnavailableOptionalAttack"]),
        patch.object(attack_package, "__getattr__", side_effect=get_export),
    ):
        assert registry.get_class("PromptSendingAttack") is PromptSendingAttack


def test_discovery_and_metadata_do_not_construct_attacks(registry: AttackRegistry) -> None:
    class ConstructorGuardAttack(PromptSendingAttack):
        def __init__(self, *, value: int = 2) -> None:
            raise AssertionError("Metadata must not construct an attack")

    registry.register_class(ConstructorGuardAttack)
    with patch.object(AttackStrategy, "__init__", autospec=True, side_effect=AssertionError("No construction")):
        metadata = registry.get_all_registered_class_metadata()

    assert {item.registry_name for item in metadata} == set(registry.get_class_names())
    assert all(isinstance(item, RegistryMetadata) for item in metadata)
    custom = next(item for item in metadata if item.registry_name == "ConstructorGuardAttack")
    assert custom.parameters[0].name == "value"
    assert custom.parameters[0].default == 2


def test_metadata_describes_constructor_configs_not_projected_children(registry: AttackRegistry) -> None:
    metadata = registry.get_all_registered_class_metadata(include_filters={"class_name": "RedTeamingAttack"})
    assert len(metadata) == 1
    parameters = {parameter.name: parameter for parameter in metadata[0].parameters}

    assert parameters["objective_target"].is_reference_to(ComponentType.TARGET)
    assert parameters["objective_target"].required
    assert parameters["attack_adversarial_config"].required
    assert parameters["attack_adversarial_config"].param_type is AttackAdversarialConfig
    assert parameters["attack_scoring_config"].reference is None
    assert parameters["attack_converter_config"].reference is None
    assert parameters["max_turns"].param_type is int
    assert parameters["max_turns"].default == 10
    assert not {"adversarial_chat", "objective_scorer", "request_converters", "response_converters"} & parameters.keys()
    assert AttackIdentifier.get_reference_component_types() == {"objective_target": ComponentType.TARGET}


def test_custom_registration_refreshes_metadata(registry: AttackRegistry) -> None:
    registry.get_all_registered_class_metadata()
    registry.register_class(CustomAttack, name="custom")

    assert registry.get_class("custom") is CustomAttack
    metadata = registry.get_all_registered_class_metadata(include_filters={"registry_name": "custom"})
    assert len(metadata) == 1
    assert metadata[0].class_name == "CustomAttack"
    assert metadata[0].class_module == __name__
    assert any(parameter.name == "objective_target" for parameter in metadata[0].parameters)


def test_singleton_reset_removes_custom_classes(registry: AttackRegistry) -> None:
    registry.register_class(CustomAttack, name="custom")
    assert AttackRegistry.get_registry_singleton() is registry

    AttackRegistry.reset_registry_singleton()

    replacement = AttackRegistry.get_registry_singleton()
    assert replacement is not registry
    assert "custom" not in replacement.get_class_names()


@pytest.mark.parametrize("method", ["get_class", "create_instance"])
def test_unknown_class_raises(*, registry: AttackRegistry, method: str) -> None:
    with pytest.raises(KeyError, match="'MissingAttack' not found"):
        getattr(registry, method)("MissingAttack")


@pytest.mark.parametrize("name", ["unknown", "adversarial_chat", "objective_scorer", "request_converters"])
def test_unknown_constructor_argument_raises(*, registry: AttackRegistry, name: str) -> None:
    with pytest.raises(ValueError, match=f"Unknown parameter '{name}'"):
        registry.create_instance("RedTeamingAttack", **{name: object()})


@pytest.mark.usefixtures("patch_central_database")
class TestAttackConstruction:
    def test_build_resolves_registered_target_and_scalar(self, registry: AttackRegistry) -> None:
        target = MockPromptTarget()
        TargetRegistry.get_registry_singleton().instances.register(target, name="objective")

        with patch.object(target, "send_prompt_async") as send:
            attack = registry.create_instance(
                "PromptSendingAttack", objective_target="objective", max_attempts_on_failure="2"
            )

        assert isinstance(attack, PromptSendingAttack)
        assert attack.get_objective_target() is target
        assert attack._max_attempts_on_failure == 2
        send.assert_not_called()

    @pytest.mark.parametrize("populated", [False, True])
    def test_missing_target_reference_raises(self, *, registry: AttackRegistry, populated: bool) -> None:
        if populated:
            TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="available")
        suffix = "Available: available" if populated else "is empty"

        with pytest.raises(ValueError, match=f"PromptSendingAttack.objective_target: 'missing' not found.*{suffix}"):
            registry.create_instance("PromptSendingAttack", objective_target="missing")

    def test_custom_attack_preserves_advanced_python_values(self, registry: AttackRegistry) -> None:
        registry.register_class(CustomAttack, name="custom")
        target = MockPromptTarget()
        normalizer = PromptNormalizer()
        prepended_config = PrependedConversationConfig()

        attack = registry.create_instance(
            "custom",
            objective_target=target,
            prompt_normalizer=normalizer,
            params_type=PromptSendingAttackParameters,
            prepended_conversation_config=prepended_config,
        )

        assert isinstance(attack, CustomAttack)
        assert attack.get_objective_target() is target
        assert attack._prompt_normalizer is normalizer
        assert attack._params_type is PromptSendingAttackParameters
        assert attack._prepended_conversation_config is prepended_config

    def test_build_preserves_typed_config_components(self, registry: AttackRegistry) -> None:
        target = MockPromptTarget()
        adversarial_target = MockPromptTarget()
        scorer = SubStringScorer(substring="example")
        converter_config = AttackConverterConfig(
            request_converters=[ConverterConfiguration(converters=[Base64Converter()])]
        )
        scoring_config = AttackScoringConfig(objective_scorer=scorer)
        adversarial_config = AttackAdversarialConfig(
            target=adversarial_target, system_prompt="Test objective: {{ objective }}"
        )

        with (
            patch.object(target, "send_prompt_async") as objective_send,
            patch.object(adversarial_target, "send_prompt_async") as adversarial_send,
        ):
            attack = registry.create_instance(
                "RedTeamingAttack",
                objective_target=target,
                attack_adversarial_config=adversarial_config,
                attack_converter_config=converter_config,
                attack_scoring_config=scoring_config,
                max_turns="3",
                score_last_turn_only="true",
            )

        assert isinstance(attack, RedTeamingAttack)
        assert attack.get_objective_target() is target
        assert attack._adversarial_chat is adversarial_target
        assert attack._objective_scorer is scorer
        assert attack._request_converters is converter_config.request_converters
        assert attack._response_converters is converter_config.response_converters
        assert attack._max_turns == 3
        assert attack._score_last_turn_only is True
        objective_send.assert_not_called()
        adversarial_send.assert_not_called()
