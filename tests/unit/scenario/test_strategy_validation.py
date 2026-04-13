# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for strategy composition validation."""

import pytest

from pyrit.scenario import ScenarioCompositeStrategy
from pyrit.scenario.foundry import FoundryStrategy
from pyrit.scenario.garak import EncodingStrategy


class TestStrategyValidation:
    """Test validation of strategy compositions."""

    def test_encoding_validation_allows_single_strategy(self):
        """Test that encoding validation allows single strategies."""
        # Should not raise
        EncodingStrategy.validate_composition([EncodingStrategy.Base64])

    def test_encoding_validation_rejects_composition(self):
        """Test that encoding validation rejects composed strategies."""
        with pytest.raises(ValueError, match="EncodingStrategy does not support composition"):
            EncodingStrategy.validate_composition([EncodingStrategy.Base64, EncodingStrategy.ROT13])

    def test_foundry_validation_allows_single_strategy(self):
        """Test that foundry validation allows single strategies."""
        # Should not raise
        FoundryStrategy.validate_composition([FoundryStrategy.Base64])

    def test_foundry_validation_allows_converter_composition(self):
        """Test that foundry validation allows multiple converters."""
        # Should not raise
        FoundryStrategy.validate_composition([FoundryStrategy.Base64, FoundryStrategy.Atbash])

    def test_foundry_validation_allows_one_attack_with_converters(self):
        """Test that foundry validation allows one attack with converters."""
        # Should not raise
        FoundryStrategy.validate_composition(
            [FoundryStrategy.Base64, FoundryStrategy.Crescendo, FoundryStrategy.Atbash]
        )

    def test_foundry_validation_rejects_multiple_attacks(self):
        """Test that foundry validation rejects multiple attack strategies."""
        with pytest.raises(ValueError, match="Cannot compose multiple attack strategies"):
            FoundryStrategy.validate_composition([FoundryStrategy.Crescendo, FoundryStrategy.MultiTurn])

    def test_foundry_validation_rejects_attacks_with_converters_and_another_attack(self):
        """Test that foundry validation rejects multiple attacks even with converters."""
        with pytest.raises(ValueError, match="Cannot compose multiple attack strategies"):
            FoundryStrategy.validate_composition(
                [FoundryStrategy.Base64, FoundryStrategy.Crescendo, FoundryStrategy.MultiTurn]
            )


class TestScenarioCompositeStrategyExtraction:
    """Test extraction of strategy values from composite strategies."""

    def test_extract_single_strategy_values_with_single_strategies(self):
        """Test extracting values from single-strategy composites."""
        composites = [
            ScenarioCompositeStrategy(strategies=[EncodingStrategy.Base64]),
            ScenarioCompositeStrategy(strategies=[EncodingStrategy.ROT13]),
            ScenarioCompositeStrategy(strategies=[EncodingStrategy.Atbash]),
        ]

        values = ScenarioCompositeStrategy.extract_single_strategy_values(composites, strategy_type=EncodingStrategy)

        assert values == {"base64", "rot13", "atbash"}

    def test_extract_single_strategy_values_filters_by_type(self):
        """Test that extraction filters by strategy type."""
        composites = [
            ScenarioCompositeStrategy(strategies=[EncodingStrategy.Base64]),
            ScenarioCompositeStrategy(strategies=[FoundryStrategy.ROT13]),
        ]

        # Extract only EncodingStrategy values
        encoding_values = ScenarioCompositeStrategy.extract_single_strategy_values(
            composites, strategy_type=EncodingStrategy
        )
        assert encoding_values == {"base64"}

        # Extract only FoundryStrategy values
        foundry_values = ScenarioCompositeStrategy.extract_single_strategy_values(
            composites, strategy_type=FoundryStrategy
        )
        assert foundry_values == {"rot13"}

    def test_extract_single_strategy_values_rejects_multi_strategy_composites(self):
        """Test that extraction raises error if any composite has multiple strategies."""
        composites = [
            ScenarioCompositeStrategy(strategies=[FoundryStrategy.Base64]),
            ScenarioCompositeStrategy(strategies=[FoundryStrategy.ROT13, FoundryStrategy.Atbash]),  # Multi-strategy!
        ]

        with pytest.raises(ValueError, match="extract_single_strategy_values.*requires all composites"):
            ScenarioCompositeStrategy.extract_single_strategy_values(composites, strategy_type=FoundryStrategy)

    def test_extract_single_strategy_values_with_empty_list(self):
        """Test that extraction handles empty composite list."""
        values = ScenarioCompositeStrategy.extract_single_strategy_values([], strategy_type=EncodingStrategy)
        assert values == set()
