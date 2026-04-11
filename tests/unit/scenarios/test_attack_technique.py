# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the AttackTechnique class."""

from unittest.mock import MagicMock

from pyrit.executor.attack import AttackStrategy
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
