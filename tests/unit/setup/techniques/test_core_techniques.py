# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the ``core`` scenario attack techniques (``techniques/core.py``).

Covers the ``flip`` and ``code_attack`` techniques. FlipAttack used to be a bespoke
``PromptSendingAttack`` subclass; it is now expressed purely as a ``core``
technique (``FlipConverter`` + ``TaskFramingConverter`` + a system-prompt
``seed_technique``). These tests lock in the legacy behavior: the objective is
sent as ``TASK is '<reversed-objective>'`` and the flip instruction system
prompt is prepended verbatim (never flipped).
"""

import pytest

from pyrit.converter import CodeAttackConverter
from pyrit.executor.attack import PromptSendingAttack
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.executor.attack.core.attack_executor import AttackExecutor
from pyrit.memory import CentralMemory
from pyrit.models import AttackSeedGroup, SeedObjective, SeedPrompt
from pyrit.setup.initializers.techniques import core
from tests.unit.mocks import MockPromptTarget

OBJECTIVE = "How can I kill a Python process?"
# FlipConverter reverses the raw objective, then TaskFramingConverter wraps it as
# ``TASK is '...'`` (stripping ``'``). Framing runs last, so ``TASK is`` stays readable
# while the objective is reversed. This matches legacy FlipAttack.
EXPECTED_SENT = "TASK is '?ssecorp nohtyP a llik I nac woH'"


def _flip_factory():
    return next(f for f in core.get_technique_factories() if f.name == "flip")


def _code_attack_factory():
    return next(f for f in core.get_technique_factories() if f.name == "code_attack")


def _wired_converters(factory):
    """Return the converters the factory wires onto its request pipeline."""
    converter_config = factory._attack_kwargs["attack_converter_config"]
    return [c for group in converter_config.request_converters for c in group.converters]


@pytest.mark.usefixtures("patch_central_database")
class TestFlipTechnique:
    """Behavioral parity tests for the migrated flip technique."""

    def test_factory_shape(self):
        factory = _flip_factory()
        assert factory.name == "flip"
        assert factory.technique_tags == ["single_turn", "light"]
        assert factory.seed_technique is not None
        seed = factory.seed_technique.seeds[0]
        assert seed.role == "system"
        assert seed.sequence == 0
        assert seed.is_general_technique is True
        assert factory.seed_technique.prompt_placement == "prepend"
        assert "flipping each word" in seed.value

    def test_merges_onto_group_with_user_turn_at_sequence_zero(self):
        """Merging flip onto a group whose opening turn is a ``user`` prompt at sequence 0
        must not raise a same-sequence role collision.

        Regression for the adaptive scenario: the ``flip`` system seed used to default to
        sequence 0 and collided with a user prompt at sequence 0 (as in the ``airt_hate``
        multi-turn ``escalating_discrimination`` group), raising ``Inconsistent roles found
        for sequence 0``. The leading system seed is now normalized to sequence 0 on merge
        and the user turn shifts to sequence 1.
        """
        factory = _flip_factory()
        base = AttackSeedGroup(
            seeds=[
                SeedObjective(value=OBJECTIVE),
                SeedPrompt(value="opening user turn", data_type="text", role="user", sequence=0),
            ]
        )

        merged = base.with_technique(technique=factory.seed_technique)

        system_prompts = [p for p in merged.prompts if p.role == "system"]
        assert len(system_prompts) == 1
        # The leading system seed is normalized to sequence 0; the user turn shifts to 1.
        assert system_prompts[0].sequence == 0
        assert merged.prompts[0].role == "system"
        assert [p.sequence for p in merged.prompts if p.role == "user"] == [1]

    async def test_sends_flipped_framed_objective_and_prepends_system_prompt(self):
        target = MockPromptTarget()
        factory = _flip_factory()
        technique = factory.create(objective_target=target, attack_scoring_config=AttackScoringConfig())

        obj_group = AttackSeedGroup(seeds=[SeedObjective(value=OBJECTIVE)])
        merged = obj_group.with_technique(technique=technique.seed_technique)

        result = await AttackExecutor(max_concurrency=1).execute_attack_from_seed_groups_async(
            attack=technique.attack,
            seed_groups=[merged],
        )

        # The objective turn is flipped, then framed as ``TASK is '...'``.
        assert target.prompt_sent[-1] == EXPECTED_SENT

        # The flip instruction is prepended as a system message and is NOT flipped.
        conversation_id = result.completed_results[0].conversation_id
        messages = CentralMemory.get_memory_instance().get_conversation_messages(conversation_id=conversation_id)
        system_messages = [m for m in messages if m.get_piece().role == "system"]
        assert len(system_messages) == 1
        assert "flipping each word" in system_messages[0].get_value()


@pytest.mark.usefixtures("patch_central_database")
class TestCodeAttackTechnique:
    """Wiring tests for the code_attack technique.

    CodeAttack ships as a converter only; the technique is the sole place the
    converter is bound to an attack, so the binding is what needs locking in.
    """

    def test_factory_shape(self):
        factory = _code_attack_factory()
        assert factory.name == "code_attack"
        assert factory._attack_class is PromptSendingAttack
        assert factory.technique_tags == ["single_turn", "light"]
        assert factory.description
        # Converter-only technique: no adversarial chat, no seed prompts.
        assert factory.seed_technique is None

        converters = _wired_converters(factory)
        assert len(converters) == 1
        converter = converters[0]
        assert isinstance(converter, CodeAttackConverter)
        assert converter._template_name == "PYTHON_STACK_VERBOSE"
        assert converter._encoding is CodeAttackConverter.Encoding.PYTHON_STACK

    async def test_wired_converter_encodes_the_objective(self):
        """The wired converter must actually turn the objective into code."""
        converter = _wired_converters(_code_attack_factory())[0]

        result = await converter.convert_async(prompt=OBJECTIVE)

        # The objective is pushed onto a stack in reverse, one word per line.
        assert "my_stack.append(" in result.output_text
        assert OBJECTIVE not in result.output_text
