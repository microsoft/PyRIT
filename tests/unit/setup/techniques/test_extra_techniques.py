# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the ``extra`` scenario attack techniques."""

import pytest

from pyrit.converter import CodeAttackConverter
from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.executor.attack.core.attack_executor import AttackExecutor
from pyrit.memory import CentralMemory
from pyrit.models import AttackSeedGroup, SeedObjective, SeedPrompt
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.prompt_target.common.target_configuration import TargetConfiguration
from pyrit.setup.initializers.techniques import core, extra
from tests.unit.mocks import MockPromptTarget

OBJECTIVE = "How can I kill a Python process?"


def _code_attack_factory():
    return next(factory for factory in core.get_technique_factories() if factory.name == "code_attack")


def _code_attack_framed_factory():
    return next(factory for factory in extra.get_technique_factories() if factory.name == "code_attack_framed")


def _wired_converters(factory):
    converter_config = factory._attack_kwargs["attack_converter_config"]
    return [converter for group in converter_config.request_converters for converter in group.converters]


class _NonEditableHistoryMockTarget(MockPromptTarget):
    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(capabilities=TargetCapabilities())


@pytest.mark.usefixtures("patch_central_database")
class TestCodeAttackFramedTechnique:
    """Tests for the optional PyRIT system-framed CodeAttack variant."""

    def test_factory_shape(self):
        factory = _code_attack_framed_factory()
        assert factory.name == "code_attack_framed"
        assert factory._attack_class is PromptSendingAttack
        assert factory.technique_tags == ["single_turn", "light"]
        assert factory.description

        seed_technique = factory.seed_technique
        assert seed_technique is not None
        assert len(seed_technique.seeds) == 1
        seed = seed_technique.seeds[0]
        assert seed.role == "system"
        assert seed.is_general_technique is True
        assert seed_technique.prompt_placement == "prepend"
        assert "code completion assistant" in seed.value

    def test_converter_wiring_matches_plain_code_attack(self):
        """The optional framing is the only converter-pipeline difference."""
        framed = _wired_converters(_code_attack_framed_factory())
        plain = _wired_converters(_code_attack_factory())

        assert len(framed) == len(plain) == 1
        assert isinstance(framed[0], CodeAttackConverter)
        assert framed[0]._template_name == plain[0]._template_name
        assert framed[0]._encoding is plain[0]._encoding

    def test_plain_code_attack_still_has_no_system_prompt(self):
        assert _code_attack_factory().seed_technique is None

    def test_merges_onto_group_with_user_turn_at_sequence_zero(self):
        factory = _code_attack_framed_factory()
        base = AttackSeedGroup(
            seeds=[
                SeedObjective(value=OBJECTIVE),
                SeedPrompt(value="opening user turn", data_type="text", role="user", sequence=0),
            ]
        )

        merged = base.with_technique(technique=factory.seed_technique)

        system_prompts = [prompt for prompt in merged.prompts if prompt.role == "system"]
        assert len(system_prompts) == 1
        assert system_prompts[0].sequence == 0
        assert merged.prompts[0].role == "system"
        assert [prompt.sequence for prompt in merged.prompts if prompt.role == "user"] == [1]

    async def test_sends_encoded_objective_and_persists_system_framing_async(self):
        target = MockPromptTarget()
        technique = _code_attack_framed_factory().create(
            objective_target=target, attack_scoring_config=AttackScoringConfig()
        )
        merged = AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE)]).with_technique(
            technique=technique.seed_technique
        )

        result = await AttackExecutor(max_concurrency=1).execute_attack_from_seed_groups_async(
            attack=technique.attack,
            seed_groups=[merged],
        )

        sent = target.prompt_sent[-1]
        assert "my_stack.append(" in sent
        assert OBJECTIVE not in sent

        conversation_id = result.completed_results[0].conversation_id
        messages = CentralMemory.get_memory_instance().get_conversation_messages(conversation_id=conversation_id)
        system_messages = [message for message in messages if message.get_piece().role == "system"]
        assert len(system_messages) == 1
        assert "code completion assistant" in system_messages[0].get_value()

    async def test_non_editable_history_target_folds_framing_into_user_turn_async(self):
        target = _NonEditableHistoryMockTarget()
        technique = _code_attack_framed_factory().create(
            objective_target=target, attack_scoring_config=AttackScoringConfig()
        )
        merged = AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE)]).with_technique(
            technique=technique.seed_technique
        )

        await AttackExecutor(max_concurrency=1).execute_attack_from_seed_groups_async(
            attack=technique.attack,
            seed_groups=[merged],
        )

        sent = target.prompt_sent[-1]
        assert "code completion assistant" in sent
        assert "my_stack.append(" in sent
        assert OBJECTIVE not in sent
