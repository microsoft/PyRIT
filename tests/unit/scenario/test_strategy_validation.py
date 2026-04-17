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
        composite = FoundryComposite(attack=FoundryStrategy.Crescendo)
        assert composite.converters == []

    def test_converter_in_attack_slot_raises(self):
        """Putting a converter-tagged strategy in the attack slot should raise."""
        with pytest.raises(ValueError, match="attack must be an attack-tagged strategy"):
            FoundryComposite(attack=FoundryStrategy.Base64)

    def test_attack_in_converters_raises(self):
        """Putting an attack-tagged strategy in converters should raise."""
        with pytest.raises(ValueError, match="converters must only contain converter-tagged"):
            FoundryComposite(attack=None, converters=[FoundryStrategy.Crescendo])

    def test_aggregate_in_converters_raises(self):
        """Aggregates (e.g. EASY) in converters slot should fail early rather than silently later."""
        with pytest.raises(ValueError, match="converters must only contain converter-tagged"):
            FoundryComposite(attack=None, converters=[FoundryStrategy.EASY])


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
