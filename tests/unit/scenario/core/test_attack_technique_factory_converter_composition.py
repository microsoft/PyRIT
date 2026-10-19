# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Focused regression tests for attack converter configuration composition."""

from unittest.mock import MagicMock

import pytest
from unit.mocks import MockPromptTarget

from pyrit.converter import Base64Converter, ROT13Converter
from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
from pyrit.models import AttackSeedGroup, ComponentIdentifier, SeedObjective
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import PromptTarget, TargetRequirements
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.matrix_atomic_attack_builder import MatrixAtomicAttackBuilder
from pyrit.score import TrueFalseScorer


class _ConverterAttack:
    TARGET_REQUIREMENTS = TargetRequirements()

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        attack_scoring_config: AttackScoringConfig | None = None,
        attack_converter_config: AttackConverterConfig | None = None,
    ) -> None:
        self.objective_target = objective_target
        self.attack_scoring_config = attack_scoring_config
        self.attack_converter_config = attack_converter_config

    def get_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier(class_name="_ConverterAttack", class_module="test")


def _converter_config(*, request=None, response=None) -> AttackConverterConfig:
    return AttackConverterConfig(
        request_converters=request or [],
        response_converters=response or [],
    )


def _create(factory: AttackTechniqueFactory, **kwargs):
    return factory.create(
        objective_target=MagicMock(spec=PromptTarget),
        attack_scoring_config=MagicMock(spec=AttackScoringConfig),
        **kwargs,
    )


def test_no_override_or_extras_preserves_baked_configuration():
    baked = _converter_config(
        request=ConverterConfiguration.from_converters(converters=[Base64Converter()]),
        response=ConverterConfiguration.from_converters(converters=[ROT13Converter()]),
    )
    factory = AttackTechniqueFactory(
        name="test",
        attack_class=_ConverterAttack,
        attack_kwargs={"attack_converter_config": baked},
    )

    technique = _create(factory)

    assert technique.attack.attack_converter_config == baked


def test_full_override_replaces_baked_configuration():
    baked = _converter_config(
        request=ConverterConfiguration.from_converters(converters=[Base64Converter()]),
        response=ConverterConfiguration.from_converters(converters=[ROT13Converter()]),
    )
    override = _converter_config(
        request=ConverterConfiguration.from_converters(converters=[ROT13Converter()]),
    )
    factory = AttackTechniqueFactory(
        name="test",
        attack_class=_ConverterAttack,
        attack_kwargs={"attack_converter_config": baked},
    )

    technique = _create(factory, attack_converter_config_override=override)

    assert technique.attack.attack_converter_config is override


def test_explicit_empty_override_replaces_baked_configuration():
    baked = _converter_config(
        request=ConverterConfiguration.from_converters(converters=[Base64Converter()]),
        response=ConverterConfiguration.from_converters(converters=[ROT13Converter()]),
    )
    override = _converter_config()
    factory = AttackTechniqueFactory(
        name="test",
        attack_class=_ConverterAttack,
        attack_kwargs={"attack_converter_config": baked},
    )

    technique = _create(factory, attack_converter_config_override=override)

    assert technique.attack.attack_converter_config is override
    assert technique.attack.attack_converter_config.request_converters == []
    assert technique.attack.attack_converter_config.response_converters == []


def test_extras_append_after_baked_requests_and_preserve_responses():
    baked_request = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    baked_response = ConverterConfiguration.from_converters(converters=[ROT13Converter()])
    extras = ConverterConfiguration.from_converters(converters=[ROT13Converter()])
    baked = _converter_config(request=baked_request, response=baked_response)
    factory = AttackTechniqueFactory(
        name="test",
        attack_class=_ConverterAttack,
        attack_kwargs={"attack_converter_config": baked},
    )

    technique = _create(factory, extra_request_converters=extras)

    composed = technique.attack.attack_converter_config
    assert composed.request_converters == baked_request + extras
    assert composed.response_converters == baked_response


def test_extras_append_after_override_requests_and_preserve_override_responses():
    baked = _converter_config(
        request=ConverterConfiguration.from_converters(converters=[Base64Converter()]),
    )
    override_request = ConverterConfiguration.from_converters(converters=[ROT13Converter()])
    override_response = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    override = _converter_config(request=override_request, response=override_response)
    extras = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    factory = AttackTechniqueFactory(
        name="test",
        attack_class=_ConverterAttack,
        attack_kwargs={"attack_converter_config": baked},
    )

    technique = _create(
        factory,
        attack_converter_config_override=override,
        extra_request_converters=extras,
    )

    composed = technique.attack.attack_converter_config
    assert composed.request_converters == override_request + extras
    assert composed.response_converters == override_response


def test_repeated_creation_does_not_accumulate_or_mutate_inputs():
    baked_request = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    baked_response = ConverterConfiguration.from_converters(converters=[ROT13Converter()])
    baked = _converter_config(request=baked_request, response=baked_response)
    first_extras = ConverterConfiguration.from_converters(converters=[ROT13Converter()])
    second_extras = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    first_extras_snapshot = list(first_extras)
    second_extras_snapshot = list(second_extras)
    factory = AttackTechniqueFactory(
        name="test",
        attack_class=_ConverterAttack,
        attack_kwargs={"attack_converter_config": baked},
    )

    first = _create(factory, extra_request_converters=first_extras)
    second = _create(factory, extra_request_converters=second_extras)

    assert first.attack.attack_converter_config.request_converters == baked_request + first_extras
    assert second.attack.attack_converter_config.request_converters == baked_request + second_extras
    assert baked.request_converters == baked_request
    assert baked.response_converters == baked_response
    assert first_extras == first_extras_snapshot
    assert second_extras == second_extras_snapshot


def test_create_delegates_converter_composition_to_private_helper(monkeypatch):
    factory = AttackTechniqueFactory(name="test", attack_class=_ConverterAttack)
    extras = ConverterConfiguration.from_converters(converters=[Base64Converter()])
    composed = _converter_config(request=extras)
    helper = MagicMock(return_value=composed)
    monkeypatch.setattr(factory, "_compose_converter_config", helper, raising=False)

    technique = _create(factory, extra_request_converters=extras)

    helper.assert_called_once()
    assert technique.attack.attack_converter_config is composed


class _NoConverterAttack:
    TARGET_REQUIREMENTS = TargetRequirements()

    def __init__(self, *, objective_target: PromptTarget, attack_scoring_config: AttackScoringConfig | None = None):
        self.objective_target = objective_target
        self.attack_scoring_config = attack_scoring_config

    def get_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier(class_name="_NoConverterAttack", class_module="test")


def test_extras_raise_when_the_attack_cannot_take_converters():
    factory = AttackTechniqueFactory(name="custom", attack_class=_NoConverterAttack)
    extras = ConverterConfiguration.from_converters(converters=[Base64Converter()])

    with pytest.raises(ValueError, match="does not accept 'attack_converter_config'"):
        _create(factory, extra_request_converters=extras)


def test_no_extras_still_work_when_the_attack_cannot_take_converters():
    factory = AttackTechniqueFactory(name="custom", attack_class=_NoConverterAttack)

    assert isinstance(_create(factory).attack, _NoConverterAttack)
    assert isinstance(_create(factory, extra_request_converters=[]).attack, _NoConverterAttack)


class _NoConverterPromptSendingAttack(PromptSendingAttack):
    """A real registered-style attack whose constructor has no attack_converter_config."""

    def __init__(self, *, objective_target: PromptTarget, attack_scoring_config: AttackScoringConfig | None = None):
        super().__init__(objective_target=objective_target, attack_scoring_config=attack_scoring_config)


@pytest.mark.usefixtures("patch_central_database")
def test_matrix_builder_rejects_technique_converters_a_real_attack_cannot_apply():
    target = MockPromptTarget()
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.get_identifier.return_value = ComponentIdentifier(class_name="Scorer", class_module="test")
    builder = MatrixAtomicAttackBuilder(objective_target=target, objective_scorer=scorer)
    factory = AttackTechniqueFactory(name="custom", attack_class=_NoConverterPromptSendingAttack)

    with pytest.raises(ValueError, match="does not accept 'attack_converter_config'"):
        builder.build(
            technique_factories={"custom": factory},
            dataset_groups={"ds": [AttackSeedGroup(seeds=[SeedObjective(value="objective")])]},
            technique_converters={"custom": [Base64Converter()]},
        )
