# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import warnings

from pyrit.identifiers import ComponentIdentifier
from pyrit.identifiers.atomic_attack_identifier import build_atomic_attack_identifier
from pyrit.models.attack_result import AttackResult


class TestAttackResultDeprecation:
    """Tests for the AttackResult attack_identifier deprecation behaviour."""

    def _make_attack_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier(class_name="TestAttack", class_module="tests.unit")

    def _make_atomic_identifier(self) -> ComponentIdentifier:
        attack_id = self._make_attack_identifier()
        return build_atomic_attack_identifier(attack_identifier=attack_id)

    # -- property deprecation -------------------------------------------------

    def test_attack_identifier_property_emits_deprecation_warning(self) -> None:
        """Accessing .attack_identifier should emit a DeprecationWarning."""
        result = AttackResult(
            conversation_id="c1",
            objective="test",
            atomic_attack_identifier=self._make_atomic_identifier(),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _ = result.attack_identifier

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1, "Expected a DeprecationWarning from .attack_identifier"
        assert "attack_identifier" in str(deprecation_warnings[0].message).lower()

    def test_attack_identifier_property_returns_correct_value(self) -> None:
        """Accessing .attack_identifier should return the attack strategy child."""
        result = AttackResult(
            conversation_id="c1",
            objective="test",
            atomic_attack_identifier=self._make_atomic_identifier(),
        )
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            value = result.attack_identifier

        assert value is not None
        assert value.class_name == "TestAttack"

    def test_attack_identifier_property_returns_none_when_unset(self) -> None:
        """Property returns None when atomic_attack_identifier is not set."""
        result = AttackResult(conversation_id="c1", objective="test")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            assert result.attack_identifier is None

    # -- get_attack_strategy_identifier (non-deprecated) ----------------------

    def test_get_attack_strategy_identifier_no_warning(self) -> None:
        """get_attack_strategy_identifier() must NOT emit a deprecation warning."""
        result = AttackResult(
            conversation_id="c1",
            objective="test",
            atomic_attack_identifier=self._make_atomic_identifier(),
        )
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            value = result.get_attack_strategy_identifier()

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0, "get_attack_strategy_identifier should not warn"
        assert value is not None
        assert value.class_name == "TestAttack"

    def test_get_attack_strategy_identifier_returns_none_when_unset(self) -> None:
        result = AttackResult(conversation_id="c1", objective="test")
        assert result.get_attack_strategy_identifier() is None

    # -- backward-compat constructor ------------------------------------------

    def test_constructor_with_attack_identifier_kwarg_emits_warning(self) -> None:
        """Passing attack_identifier= to the constructor should emit DeprecationWarning."""
        attack_id = self._make_attack_identifier()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = AttackResult(
                conversation_id="c1",
                objective="test",
                attack_identifier=attack_id,
            )

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1, "Constructor should warn on attack_identifier="
        # The value should be promoted to atomic_attack_identifier
        assert result.atomic_attack_identifier is not None
        assert result.get_attack_strategy_identifier() == attack_id

    def test_constructor_attack_identifier_does_not_override_atomic(self) -> None:
        """If both are supplied, atomic_attack_identifier takes precedence."""
        attack_id = self._make_attack_identifier()
        atomic_id = self._make_atomic_identifier()
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            result = AttackResult(
                conversation_id="c1",
                objective="test",
                attack_identifier=attack_id,
                atomic_attack_identifier=atomic_id,
            )

        assert result.atomic_attack_identifier is atomic_id

    # -- construction without deprecated kwarg --------------------------------

    def test_constructor_with_atomic_attack_identifier_only(self) -> None:
        """Normal construction with atomic_attack_identifier should work with no warnings."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = AttackResult(
                conversation_id="c1",
                objective="test",
                atomic_attack_identifier=self._make_atomic_identifier(),
            )

        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 0
        assert result.get_attack_strategy_identifier() is not None

    def test_constructor_with_no_identifier_at_all(self) -> None:
        """Construction with neither identifier should be fine."""
        result = AttackResult(conversation_id="c1", objective="test")
        assert result.atomic_attack_identifier is None
        assert result.get_attack_strategy_identifier() is None
