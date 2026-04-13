# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the AttackTechnique class."""

from unittest.mock import MagicMock

from pyrit.executor.attack import AttackStrategy
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import SeedAttackTechniqueGroup, SeedPrompt
from pyrit.scenario.core.attack_technique import AttackTechnique


def _make_technique_seeds() -> SeedAttackTechniqueGroup:
    return SeedAttackTechniqueGroup(
        seeds=[
            SeedPrompt(value="technique1", data_type="text", is_general_technique=True),
            SeedPrompt(value="technique2", data_type="text", is_general_technique=True),
        ]
    )


class TestAttackTechniqueInit:
    """Tests for AttackTechnique initialization."""

    def test_init_with_attack_only(self):
        mock_attack = MagicMock(spec=AttackStrategy)
        technique = AttackTechnique(attack=mock_attack)

        assert technique.attack is mock_attack
        assert technique.seed_technique is None

    def test_init_with_attack_and_seed_technique(self):
        mock_attack = MagicMock(spec=AttackStrategy)
        seed_technique = _make_technique_seeds()
        technique = AttackTechnique(attack=mock_attack, seed_technique=seed_technique)

        assert technique.attack is mock_attack
        assert technique.seed_technique is seed_technique

    def test_init_with_seed_technique_none_explicitly(self):
        mock_attack = MagicMock(spec=AttackStrategy)
        technique = AttackTechnique(attack=mock_attack, seed_technique=None)

        assert technique.seed_technique is None


class TestAttackTechniqueProperties:
    """Tests for AttackTechnique property access."""

    def test_attack_property_returns_same_instance(self):
        mock_attack = MagicMock(spec=AttackStrategy)
        technique = AttackTechnique(attack=mock_attack)

        assert technique.attack is technique.attack  # same object each time

    def test_seed_technique_property_returns_same_instance(self):
        mock_attack = MagicMock(spec=AttackStrategy)
        seed_technique = _make_technique_seeds()
        technique = AttackTechnique(attack=mock_attack, seed_technique=seed_technique)

        assert technique.seed_technique is technique.seed_technique


class TestAttackTechniqueIdentifier:
    """Tests for AttackTechnique.get_identifier() (Identifiable)."""

    def test_get_identifier_returns_component_identifier(self):
        mock_attack = MagicMock(spec=AttackStrategy)
        mock_attack.get_identifier.return_value = ComponentIdentifier(
            class_name="PromptSendingAttack", class_module="pyrit.executor.attack"
        )
        technique = AttackTechnique(attack=mock_attack)

        result = technique.get_identifier()
        assert isinstance(result, ComponentIdentifier)

    def test_class_name_and_module(self):
        mock_attack = MagicMock(spec=AttackStrategy)
        mock_attack.get_identifier.return_value = ComponentIdentifier(
            class_name="PromptSendingAttack", class_module="pyrit.executor.attack"
        )
        technique = AttackTechnique(attack=mock_attack)

        result = technique.get_identifier()
        assert result.class_name == "AttackTechnique"
        assert result.class_module == "pyrit.scenario.core.attack_technique"

    def test_attack_child_is_present(self):
        attack_id = ComponentIdentifier(class_name="PromptSendingAttack", class_module="pyrit.executor.attack")
        mock_attack = MagicMock(spec=AttackStrategy)
        mock_attack.get_identifier.return_value = attack_id
        technique = AttackTechnique(attack=mock_attack)

        result = technique.get_identifier()
        assert result.children["attack"] == attack_id

    def test_no_technique_seeds_when_none(self):
        mock_attack = MagicMock(spec=AttackStrategy)
        mock_attack.get_identifier.return_value = ComponentIdentifier(
            class_name="PromptSendingAttack", class_module="pyrit.executor.attack"
        )
        technique = AttackTechnique(attack=mock_attack)

        result = technique.get_identifier()
        assert "technique_seeds" not in result.children

    def test_technique_seeds_present_when_provided(self):
        mock_attack = MagicMock(spec=AttackStrategy)
        mock_attack.get_identifier.return_value = ComponentIdentifier(
            class_name="PromptSendingAttack", class_module="pyrit.executor.attack"
        )
        seed_technique = _make_technique_seeds()
        technique = AttackTechnique(attack=mock_attack, seed_technique=seed_technique)

        result = technique.get_identifier()
        assert "technique_seeds" in result.children
        assert len(result.children["technique_seeds"]) == 2

    def test_identifier_is_cached(self):
        mock_attack = MagicMock(spec=AttackStrategy)
        mock_attack.get_identifier.return_value = ComponentIdentifier(
            class_name="PromptSendingAttack", class_module="pyrit.executor.attack"
        )
        technique = AttackTechnique(attack=mock_attack)

        first = technique.get_identifier()
        second = technique.get_identifier()
        assert first is second
