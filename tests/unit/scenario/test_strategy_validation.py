# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for strategy composition validation."""

import warnings

import pytest

from pyrit.scenario import ScenarioCompositeStrategy
from pyrit.scenario.foundry import FoundryStrategy
from pyrit.scenario.foundry.red_team_agent import FoundryComposite
from pyrit.scenario.garak import EncodingStrategy


class TestFoundryComposite:
    """Tests for FoundryComposite dataclass construction and naming."""

    def test_converter_only_composite_name(self):
        """Test name for a composite with only a converter strategy — matches old single-strategy convention."""
        composite = FoundryComposite(attack=None, converters=[FoundryStrategy.Base64])
        assert composite.name == "base64"

    def test_attack_only_composite_name(self):
        """Test name for a composite with only an attack strategy."""
        composite = FoundryComposite(attack=FoundryStrategy.Crescendo)
        assert composite.name == "crescendo"

    def test_attack_with_converter_composite_name(self):
        """Test name for attack + converter composition."""
        composite = FoundryComposite(attack=FoundryStrategy.Crescendo, converters=[FoundryStrategy.Base64])
        assert composite.name == "ComposedStrategy(crescendo, base64)"

    def test_attack_with_multiple_converters_composite_name(self):
        """Test name with multiple converters."""
        composite = FoundryComposite(
            attack=FoundryStrategy.Crescendo, converters=[FoundryStrategy.Base64, FoundryStrategy.Atbash]
        )
        assert composite.name == "ComposedStrategy(crescendo, base64, atbash)"

    def test_empty_composite_defaults(self):
        """Test that FoundryComposite defaults converters to empty list."""
        composite = FoundryComposite(attack=FoundryStrategy.Base64)
        assert composite.converters == []


class TestScenarioCompositeStrategyDeprecation:
    """Test that ScenarioCompositeStrategy emits deprecation warnings."""

    def test_init_emits_deprecation_warning(self):
        """Creating a ScenarioCompositeStrategy should emit a DeprecationWarning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ScenarioCompositeStrategy(strategies=[EncodingStrategy.Base64])
        assert any(issubclass(warning.category, DeprecationWarning) for warning in w)
        assert any("ScenarioCompositeStrategy" in str(warning.message) for warning in w)

    def test_init_warning_mentions_foundry_composite(self):
        """The deprecation warning should point users to FoundryComposite."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            ScenarioCompositeStrategy(strategies=[EncodingStrategy.Base64])
        messages = [str(warning.message) for warning in w if issubclass(warning.category, DeprecationWarning)]
        assert any("FoundryComposite" in msg for msg in messages)


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
