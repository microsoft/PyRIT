# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Basic registry construction stays deferred and preserves Python factories."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from pyrit.converter import Base64Converter, ROT13Converter
from pyrit.executor.attack import AttackConverterConfig, AttackScoringConfig, PromptSendingAttack, RedTeamingAttack
from pyrit.models import AttackTechniqueSeedGroup, SeedPrompt, StructuredParameterValue
from pyrit.registry import AttackRegistry, AttackTechniqueRegistry, ConverterRegistry, Registry, TargetRegistry
from pyrit.scenario.core import AttackTechniqueFactory
from unit.mocks import MockPromptTarget


@dataclass
class CustomSettings(StructuredParameterValue):
    enabled: bool = True
    limit: int = 9
    labels: list[str] | None = None

    @classmethod
    def get_registry_input_variants(cls) -> dict[str, type[StructuredParameterValue]]:
        return {"basic": cls}


class CustomAttack(PromptSendingAttack):
    def __init__(self, *, objective_target: MockPromptTarget, settings: CustomSettings) -> None:
        super().__init__(objective_target=objective_target)
        self.settings = settings


@pytest.fixture
def registry(patch_central_database: Any) -> Iterator[AttackTechniqueRegistry]:
    with patch.dict(Registry._singletons, {}, clear=True):
        TargetRegistry.get_registry_singleton().instances.register(MockPromptTarget(), name="local")
        converters = ConverterRegistry.get_registry_singleton()
        converters.instances.register(Base64Converter(), name="b64")
        converters.instances.register(ROT13Converter(), name="rot13")
        yield AttackTechniqueRegistry.get_registry_singleton()


def test_converter_references_preserve_order_duplicates_and_zero(registry: AttackTechniqueRegistry) -> None:
    with patch.object(PromptSendingAttack, "__init__", autospec=True, side_effect=AssertionError("Not deferred")):
        factory = registry.create_factory(
            name="encoded",
            attack_type="PromptSendingAttack",
            params={"max_attempts_on_failure": 0},
            request_converters=["b64", "rot13", "b64"],
            response_converters=[],
        )
    assert registry.instances.get_names() == []
    config = factory.get_creation_kwargs()["attack_kwargs"]
    assert config["max_attempts_on_failure"] == 0
    converter_config = config["attack_converter_config"]
    assert isinstance(converter_config, AttackConverterConfig)
    assert converter_config.response_converters == []
    request = converter_config.request_converters
    assert [type(entry.converters[0]).__name__ for entry in request] == [
        "Base64Converter",
        "ROT13Converter",
        "Base64Converter",
    ]
    assert request[0] == request[2]
    assert factory.technique_tags == []


def test_custom_variant_uses_existing_resolver_and_creates_fresh_attacks(registry: AttackTechniqueRegistry) -> None:
    AttackRegistry.get_registry_singleton().register_class(CustomAttack, name="custom")
    factory = registry.create_factory(
        name="custom_settings",
        attack_type="custom",
        params={"settings": {"type": "basic", "parameters": {"enabled": False, "limit": 0, "labels": []}}},
    )
    target = TargetRegistry.get_registry_singleton().instances.get("local")
    assert isinstance(target, MockPromptTarget)
    first = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
    second = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())
    assert isinstance(first.attack, CustomAttack)
    assert first.attack is not second.attack
    assert first.attack.settings == CustomSettings(enabled=False, limit=0, labels=[])
    assert factory.get_creation_kwargs()["attack_kwargs"]["settings"] == CustomSettings(
        enabled=False, limit=0, labels=[]
    )
    assert target.prompt_sent == []


def test_omission_null_and_empty_converter_list_remain_distinct(registry: AttackTechniqueRegistry) -> None:
    omitted = registry.create_factory(name="omitted", attack_type="PromptSendingAttack")
    explicit = registry.create_factory(
        name="explicit", attack_type="PromptSendingAttack", params={"attack_converter_config": None}
    )
    empty = registry.create_factory(name="empty", attack_type="PromptSendingAttack", request_converters=[])
    assert "attack_converter_config" not in omitted.get_creation_kwargs()["attack_kwargs"]
    assert explicit.get_creation_kwargs()["attack_kwargs"] == {"attack_converter_config": None}
    assert empty.get_creation_kwargs()["attack_kwargs"]["attack_converter_config"].request_converters == []


def test_adversarial_target_and_inline_prompts_stay_deferred(registry: AttackTechniqueRegistry) -> None:
    with patch.object(RedTeamingAttack, "__init__", autospec=True, side_effect=AssertionError("Not deferred")):
        factory = registry.create_factory(
            name="adversarial",
            attack_type="RedTeamingAttack",
            adversarial_chat="local",
            adversarial_system_prompt="",
            adversarial_seed_prompt="seed",
            adversarial_prompt_template="turn",
        )
        default = registry.create_factory(name="default_target", attack_type="RedTeamingAttack")
    assert factory.adversarial_chat is TargetRegistry.get_registry_singleton().instances.get("local")
    assert factory.uses_adversarial and not factory.uses_default_adversarial_target
    assert default.uses_default_adversarial_target
    options = factory.get_creation_kwargs()
    assert options["adversarial_system_prompt"] == ""
    assert options["adversarial_chat"] is factory.adversarial_chat


def test_advanced_programmatic_factories_remain_supported(registry: AttackTechniqueRegistry) -> None:
    seeds = AttackTechniqueSeedGroup(seeds=[SeedPrompt(value="prefix", is_general_technique=True)])
    scoring = AttackScoringConfig(use_score_as_feedback=False)
    factory = AttackTechniqueFactory(
        name="python_only",
        attack_class=PromptSendingAttack,
        seed_technique=seeds,
        attack_kwargs={"attack_scoring_config": scoring},
    )
    registry.register_from_factories([factory])
    assert registry.get_factories()["python_only"] is factory
    assert factory.seed_technique is seeds


def test_factory_keeps_existing_constructor_coercion(registry: AttackTechniqueRegistry) -> None:
    factory = registry.create_factory(
        name="coerced", attack_type="PromptSendingAttack", params={"max_attempts_on_failure": "2"}
    )
    assert factory.get_creation_kwargs()["attack_kwargs"]["max_attempts_on_failure"] == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"attack_type": "missing"},
        {"params": {"unknown": 1}},
        {"params": {"objective_target": "local"}},
        {"params": {"attack_scoring_config": {}}},
        {"params": {"attack_adversarial_config": {}}},
        {"params": {"attack_converter_config": None}, "request_converters": []},
        {"request_converters": ["b64", "missing"]},
        {"response_converters": ["missing"]},
        {"adversarial_chat": "missing", "attack_type": "RedTeamingAttack"},
        {"adversarial_system_prompt": ""},
    ],
)
def test_invalid_inputs_leave_registry_unchanged(registry: AttackTechniqueRegistry, kwargs: dict[str, Any]) -> None:
    before = registry.catalog_revision
    with pytest.raises((ValueError, TypeError)):
        registry.create_factory(name="invalid", **{"attack_type": "PromptSendingAttack", **kwargs})
    assert registry.instances.get_names() == []
    assert registry.catalog_revision == before


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "existing"},
        {"name": "Existing"},
        {"name": "group"},
        {"technique_tags": ["existing"]},
        {"technique_tags": ["GROUP"]},
        {"name": "same", "technique_tags": ["Same"]},
    ],
)
def test_selector_collisions_do_not_change_pool(registry: AttackTechniqueRegistry, kwargs: dict[str, Any]) -> None:
    registry.register_from_factories(
        [AttackTechniqueFactory(name="existing", attack_class=PromptSendingAttack, technique_tags=["group"])]
    )
    before = registry.catalog_revision
    factory = registry.create_factory(**{"name": "example", "attack_type": "PromptSendingAttack", **kwargs})
    with pytest.raises(ValueError):
        registry.instances.register_runtime(factory)
    assert registry.instances.get_names() == ["existing"]
    assert registry.catalog_revision == before


def test_runtime_tag_can_match_a_scenario_local_name(registry: AttackTechniqueRegistry) -> None:
    factory = registry.create_factory(
        name="local_tag", attack_type="PromptSendingAttack", technique_tags=["FIRST_LETTER"]
    )
    registry.instances.register_runtime(factory)
    assert registry.instances.get("local_tag") is factory


def test_missing_required_inputs_fail_before_registration(registry: AttackTechniqueRegistry) -> None:
    AttackRegistry.get_registry_singleton().register_class(CustomAttack)
    with pytest.raises(ValueError, match="Missing required.*settings"):
        registry.create_factory(name="missing", attack_type="CustomAttack")
    assert registry.instances.get_names() == []
