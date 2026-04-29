# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the AttackTechniqueFactory class."""

from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.core.attack_config import AttackConverterConfig, AttackScoringConfig
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.identifiers import ComponentIdentifier, Identifiable
from pyrit.models import SeedAttackTechniqueGroup, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory


def _make_seed_technique() -> SeedAttackTechniqueGroup:
    return SeedAttackTechniqueGroup(
        seeds=[
            SeedPrompt(value="technique1", data_type="text", is_general_technique=True),
        ]
    )


class _StubAttack:
    """
    Minimal stub that mimics an AttackStrategy constructor signature.

    We use a plain class rather than a real AttackStrategy subclass to keep
    the unit tests fast and free of heavyweight base-class initialization.
    ``inspect.signature`` sees the same keyword-only parameters that the
    factory's ``_validate_kwargs`` expects.
    """

    def __init__(
        self,
        *,
        objective_target: PromptTarget,
        attack_scoring_config: AttackScoringConfig | None = None,
        attack_converter_config: AttackConverterConfig | None = None,
        max_turns: int = 5,
    ) -> None:
        self.objective_target = objective_target
        self.attack_scoring_config = attack_scoring_config
        self.attack_converter_config = attack_converter_config
        self.max_turns = max_turns

    def get_identifier(self) -> ComponentIdentifier:
        return ComponentIdentifier(
            class_name="_StubAttack",
            class_module="tests.unit.scenario.test_attack_technique_factory",
        )


class TestFactoryInit:
    """Tests for AttackTechniqueFactory construction and validation."""

    def test_init_defaults(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)

        assert factory.attack_class is _StubAttack
        assert factory.seed_technique is None

    def test_init_stores_seed_technique(self):
        seeds = _make_seed_technique()
        factory = AttackTechniqueFactory(attack_class=_StubAttack, seed_technique=seeds)

        assert factory.seed_technique is seeds

    def test_validate_kwargs_accepts_valid_params(self):
        """All valid kwarg names should pass without error."""
        factory = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 10, "attack_scoring_config": None},
        )
        assert factory.attack_class is _StubAttack

    def test_validate_kwargs_rejects_unknown_params(self):
        """Typo or nonexistent kwarg should raise TypeError immediately."""
        with pytest.raises(TypeError, match="Invalid kwargs.*max_turn"):
            AttackTechniqueFactory(
                attack_class=_StubAttack,
                attack_kwargs={"max_turn": 10},  # typo: should be max_turns
            )

    def test_validate_kwargs_rejects_objective_target(self):
        """objective_target must not be in attack_kwargs."""
        target = MagicMock(spec=PromptTarget)
        with pytest.raises(ValueError, match="objective_target must not be in attack_kwargs"):
            AttackTechniqueFactory(
                attack_class=_StubAttack,
                attack_kwargs={"objective_target": target},
            )

    def test_validate_kwargs_rejects_multiple_invalid(self):
        """Multiple bad kwargs should all be reported."""
        with pytest.raises(TypeError, match="Invalid kwargs"):
            AttackTechniqueFactory(
                attack_class=_StubAttack,
                attack_kwargs={"bad_param_1": 1, "bad_param_2": 2},
            )

    def test_validate_kwargs_rejects_var_keyword_constructor(self):
        """Constructors with **kwargs prevent parameter validation and should be rejected."""

        class _KwargsAttack:
            def __init__(self, **kwargs):
                pass

        with pytest.raises(TypeError, match="accepts \\*\\*kwargs.*parameter validation"):
            AttackTechniqueFactory(attack_class=_KwargsAttack)

    def test_validate_kwargs_rejects_var_keyword_even_with_named_params(self):
        """Mixed named params + **kwargs should still be rejected."""

        class _MixedAttack:
            def __init__(self, *, objective_target, max_turns: int = 5, **extra):
                pass

        with pytest.raises(TypeError, match="accepts \\*\\*kwargs"):
            AttackTechniqueFactory(
                attack_class=_MixedAttack,
                attack_kwargs={"max_turns": 10},
            )

    def test_validate_kwargs_works_with_real_attack_class(self):
        """
        Validate that inspect.signature correctly sees through @apply_defaults
        and functools.wraps on a real AttackStrategy subclass.
        """
        # PromptSendingAttack uses @apply_defaults — factory should see its real params
        factory = AttackTechniqueFactory(attack_class=PromptSendingAttack)
        assert factory.attack_class is PromptSendingAttack

    def test_validate_kwargs_rejects_invalid_param_on_real_attack_class(self):
        """A typo kwarg should be caught even through @apply_defaults."""
        with pytest.raises(TypeError, match="Invalid kwargs.*nonexistent_param"):
            AttackTechniqueFactory(
                attack_class=PromptSendingAttack,
                attack_kwargs={"nonexistent_param": 42},
            )


class TestFactoryCreate:
    """Tests for AttackTechniqueFactory.create()."""

    def _scoring(self) -> AttackScoringConfig:
        return MagicMock(spec=AttackScoringConfig)

    def test_create_produces_attack_technique(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        target = MagicMock(spec=PromptTarget)

        technique = factory.create(objective_target=target, attack_scoring_config_override=self._scoring())

        assert isinstance(technique, AttackTechnique)
        assert isinstance(technique.attack, _StubAttack)
        assert technique.attack.objective_target is target

    def test_create_passes_frozen_kwargs(self):
        factory = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 42},
        )
        target = MagicMock(spec=PromptTarget)

        technique = factory.create(objective_target=target, attack_scoring_config_override=self._scoring())

        assert technique.attack.max_turns == 42

    def test_create_passes_scoring_config(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        target = MagicMock(spec=PromptTarget)
        scoring = MagicMock(spec=AttackScoringConfig)

        technique = factory.create(objective_target=target, attack_scoring_config_override=scoring)

        assert technique.attack.attack_scoring_config is scoring

    def test_create_overrides_frozen_scoring_config(self):
        """Create-time scoring config should override the frozen one."""
        frozen_scoring = MagicMock(spec=AttackScoringConfig)
        factory = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"attack_scoring_config": frozen_scoring},
        )
        target = MagicMock(spec=PromptTarget)
        override_scoring = MagicMock(spec=AttackScoringConfig)

        technique = factory.create(objective_target=target, attack_scoring_config_override=override_scoring)

        assert technique.attack.attack_scoring_config is override_scoring
        assert technique.attack.attack_scoring_config is not frozen_scoring

    def test_create_preserves_seed_technique(self):
        seeds = _make_seed_technique()
        factory = AttackTechniqueFactory(attack_class=_StubAttack, seed_technique=seeds)
        target = MagicMock(spec=PromptTarget)

        technique = factory.create(objective_target=target, attack_scoring_config_override=self._scoring())

        assert technique.seed_technique is seeds

    def test_create_produces_independent_instances(self):
        """Two create() calls should produce fully independent attack instances."""
        factory = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 10},
        )
        target1 = MagicMock(spec=PromptTarget)
        target2 = MagicMock(spec=PromptTarget)
        scoring = self._scoring()

        technique1 = factory.create(objective_target=target1, attack_scoring_config_override=scoring)
        technique2 = factory.create(objective_target=target2, attack_scoring_config_override=scoring)

        assert technique1.attack is not technique2.attack
        assert technique1.attack.objective_target is target1
        assert technique2.attack.objective_target is target2

    def test_create_shares_kwargs_values(self):
        """Factory uses shallow copy — mutable values inside kwargs are shared (by design)."""
        mutable_list = [1, 2, 3]

        class _ListAttack:
            def __init__(self, *, objective_target, attack_scoring_config=None, items: list | None = None):
                self.objective_target = objective_target
                self.items = items

            def get_identifier(self):
                return ComponentIdentifier(class_name="_ListAttack", class_module="test")

        factory = AttackTechniqueFactory(
            attack_class=_ListAttack,
            attack_kwargs={"items": mutable_list},
        )
        target = MagicMock(spec=PromptTarget)

        technique1 = factory.create(objective_target=target, attack_scoring_config_override=self._scoring())
        assert technique1.attack.items == [1, 2, 3]

        # Mutating the original list is visible to future creates (shallow copy)
        mutable_list.append(999)
        technique2 = factory.create(objective_target=target, attack_scoring_config_override=self._scoring())
        assert technique2.attack.items == [1, 2, 3, 999]

    def test_create_without_optional_configs_omits_them(self):
        """When optional configs are None, adversarial and converter should not be passed."""
        unset = object()

        class _SentinelAttack:
            def __init__(
                self,
                *,
                objective_target,
                attack_scoring_config,
                attack_adversarial_config=unset,
                attack_converter_config=unset,
            ):
                self.objective_target = objective_target
                self.adversarial_was_passed = attack_adversarial_config is not unset
                self.converter_was_passed = attack_converter_config is not unset

            def get_identifier(self):
                return ComponentIdentifier(class_name="_SentinelAttack", class_module="test")

        factory = AttackTechniqueFactory(attack_class=_SentinelAttack)
        target = MagicMock(spec=PromptTarget)
        technique = factory.create(objective_target=target, attack_scoring_config_override=self._scoring())

        assert not technique.attack.adversarial_was_passed
        assert not technique.attack.converter_was_passed


class TestFactoryIdentifier:
    """Tests for AttackTechniqueFactory._build_identifier()."""

    def test_identifier_includes_attack_class_name(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)

        identifier = factory.get_identifier()

        assert isinstance(identifier, ComponentIdentifier)
        assert identifier.class_name == "AttackTechniqueFactory"
        assert identifier.params["attack_class"] == "_StubAttack"

    def test_identifier_includes_kwargs_with_values(self):
        factory = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 10, "attack_scoring_config": None},
        )

        identifier = factory.get_identifier()

        assert identifier.params["kwargs"] == {"attack_scoring_config": None, "max_turns": 10}

    def test_identifier_empty_kwargs(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)

        identifier = factory.get_identifier()

        assert identifier.params["kwargs"] == {}

    def test_same_keys_different_values_produce_different_hashes(self):
        """Two factories with max_turns=5 vs max_turns=50 must have different hashes."""
        factory1 = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 5},
        )
        factory2 = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 50},
        )

        assert factory1.get_identifier().hash != factory2.get_identifier().hash

    def test_different_kwargs_keys_produce_different_hashes(self):
        factory1 = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 10},
        )
        factory2 = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 10, "attack_scoring_config": None},
        )

        assert factory1.get_identifier().hash != factory2.get_identifier().hash

    def test_identifier_serializes_identifiable_values(self):
        """Identifiable objects in kwargs should contribute their hash to the identifier."""
        expected_id = ComponentIdentifier(
            class_name="MockConfig",
            class_module="test",
            params={"key": "value"},
        )
        mock_identifiable = MagicMock(spec=Identifiable)
        mock_identifiable.get_identifier.return_value = expected_id

        class _IdentifiableParamAttack:
            def __init__(self, *, objective_target, config=None):
                pass

            def get_identifier(self):
                return ComponentIdentifier(class_name="_IdentifiableParamAttack", class_module="test")

        factory = AttackTechniqueFactory(
            attack_class=_IdentifiableParamAttack,
            attack_kwargs={"config": mock_identifiable},
        )

        identifier = factory.get_identifier()
        config_value = identifier.params["kwargs"]["config"]
        # Should be the hash string from the identifiable, not the object itself
        assert isinstance(config_value, str)
        assert config_value == expected_id.hash

    def test_identifier_is_cached(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)

        first = factory.get_identifier()
        second = factory.get_identifier()

        assert first is second

    def test_seed_technique_included_in_identifier(self):
        """A factory with seed_technique should have technique_seeds children."""
        seed_technique = _make_seed_technique()
        factory = AttackTechniqueFactory(attack_class=_StubAttack, seed_technique=seed_technique)

        identifier = factory.get_identifier()

        assert "technique_seeds" in identifier.children
        assert len(identifier.children["technique_seeds"]) == 1

    def test_no_seed_technique_means_no_children(self):
        """A factory without seed_technique should have no technique_seeds children."""
        factory = AttackTechniqueFactory(attack_class=_StubAttack)

        identifier = factory.get_identifier()

        assert "technique_seeds" not in identifier.children

    def test_different_seed_techniques_produce_different_hashes(self):
        """Two factories differing only by seed_technique must have different hashes."""
        seed1 = SeedAttackTechniqueGroup(
            seeds=[SeedPrompt(value="technique_a", data_type="text", is_general_technique=True)],
        )
        seed2 = SeedAttackTechniqueGroup(
            seeds=[SeedPrompt(value="technique_b", data_type="text", is_general_technique=True)],
        )
        factory1 = AttackTechniqueFactory(attack_class=_StubAttack, seed_technique=seed1)
        factory2 = AttackTechniqueFactory(attack_class=_StubAttack, seed_technique=seed2)

        assert factory1.get_identifier().hash != factory2.get_identifier().hash
