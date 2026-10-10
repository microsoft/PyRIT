# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for technique composition validation."""

import pytest

from pyrit.scenario.foundry import FoundryComposite, FoundryTechnique  # type: ignore[ty:unresolved-import]


class TestFoundryComposite:
    """Tests for FoundryComposite dataclass construction and naming."""

    def test_converter_only_composite_name(self):
        """Test name for a composite with only a converter technique — matches old single-technique convention."""
        composite = FoundryComposite(attack=None, converters=[FoundryTechnique.Base64])
        assert composite.name == "base64"

    def test_attack_only_composite_name(self):
        """Test name for a composite with only an attack technique."""
        composite = FoundryComposite(attack=FoundryTechnique.Crescendo)
        assert composite.name == "crescendo"

    def test_attack_with_converter_composite_name(self):
        """Test name for attack + converter composition."""
        composite = FoundryComposite(attack=FoundryTechnique.Crescendo, converters=[FoundryTechnique.Base64])
        assert composite.name == "ComposedTechnique(crescendo, base64)"

    def test_attack_with_multiple_converters_composite_name(self):
        """Test name with multiple converters."""
        composite = FoundryComposite(
            attack=FoundryTechnique.Crescendo, converters=[FoundryTechnique.Base64, FoundryTechnique.Atbash]
        )
        assert composite.name == "ComposedTechnique(crescendo, base64, atbash)"

    def test_empty_composite_defaults(self):
        """Test that FoundryComposite defaults converters to empty list."""
        composite = FoundryComposite(attack=FoundryTechnique.Crescendo)
        assert composite.converters == []

    def test_converter_in_attack_slot_raises(self):
        """Putting a converter-tagged technique in the attack slot should raise."""
        with pytest.raises(ValueError, match="attack must be an attack-tagged technique"):
            FoundryComposite(attack=FoundryTechnique.Base64)

    def test_attack_in_converters_raises(self):
        """Putting an attack-tagged technique in converters should raise."""
        with pytest.raises(ValueError, match="converters must only contain converter-tagged"):
            FoundryComposite(attack=None, converters=[FoundryTechnique.Crescendo])

    def test_aggregate_in_converters_raises(self):
        """Aggregates (e.g. EASY) in converters slot should fail early rather than silently later."""
        with pytest.raises(ValueError, match="converters must only contain converter-tagged"):
            FoundryComposite(attack=None, converters=[FoundryTechnique.EASY])
