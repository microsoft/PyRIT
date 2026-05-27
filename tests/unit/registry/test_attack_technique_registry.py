# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the AttackTechniqueRegistry class."""

import inspect
from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.identifiers import ComponentIdentifier
from pyrit.prompt_target import PromptTarget
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry, AttackTechniqueSpec
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory, ScorerOverridePolicy
from pyrit.scenario.core.scenario_techniques import SCENARIO_TECHNIQUES


class _StubAttack:
    """Minimal stub for testing the registry without real AttackStrategy weight."""

    def __init__(self, *, objective_target, attack_scoring_config=None, max_turns: int = 5):
        self.objective_target = objective_target
        self.attack_scoring_config = attack_scoring_config
        self.max_turns = max_turns

    def get_identifier(self):
        return ComponentIdentifier(
            class_name="_StubAttack",
            class_module="tests.unit.registry.test_attack_technique_registry",
            params={"max_turns": self.max_turns},
        )


class _StubAttackNoScorer:
    """Stub attack that does NOT accept attack_scoring_config."""

    def __init__(self, *, objective_target):
        self.objective_target = objective_target

    def get_identifier(self):
        return ComponentIdentifier(
            class_name="_StubAttackNoScorer",
            class_module="tests.unit.registry.test_attack_technique_registry",
            params={},
        )


class TestAttackTechniqueRegistrySingleton:
    """Tests for the singleton pattern."""

    def setup_method(self):
        AttackTechniqueRegistry.reset_instance()

    def teardown_method(self):
        AttackTechniqueRegistry.reset_instance()

    def test_get_registry_singleton_returns_same_instance(self):
        instance1 = AttackTechniqueRegistry.get_registry_singleton()
        instance2 = AttackTechniqueRegistry.get_registry_singleton()

        assert instance1 is instance2

    def test_get_registry_singleton_returns_correct_type(self):
        instance = AttackTechniqueRegistry.get_registry_singleton()

        assert isinstance(instance, AttackTechniqueRegistry)

    def test_reset_instance_clears_singleton(self):
        instance1 = AttackTechniqueRegistry.get_registry_singleton()
        AttackTechniqueRegistry.reset_instance()
        instance2 = AttackTechniqueRegistry.get_registry_singleton()

        assert instance1 is not instance2


class TestAttackTechniqueRegistryRegister:
    """Tests for registering technique factories."""

    def setup_method(self):
        AttackTechniqueRegistry.reset_instance()
        self.registry = AttackTechniqueRegistry.get_registry_singleton()

    def teardown_method(self):
        AttackTechniqueRegistry.reset_instance()

    def test_register_technique_stores_factory(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)

        self.registry.register_technique(name="stub_attack", factory=factory)

        assert "stub_attack" in self.registry
        assert self.registry._registry_items["stub_attack"].instance is factory

    def test_register_technique_with_tags(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)

        self.registry.register_technique(
            name="stub_attack",
            factory=factory,
            tags=["single_turn", "encoding"],
        )

        entries = self.registry.get_by_tag(tag="single_turn")
        assert len(entries) == 1
        assert entries[0].name == "stub_attack"

    def test_register_multiple_techniques(self):
        factory1 = AttackTechniqueFactory(attack_class=_StubAttack)
        factory2 = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 20},
        )

        self.registry.register_technique(name="stub_5", factory=factory1)
        self.registry.register_technique(name="stub_20", factory=factory2)

        assert len(self.registry) == 2
        assert self.registry.get_names() == ["stub_20", "stub_5"]


class TestAttackTechniqueRegistryMetadata:
    """Tests for metadata / list_metadata on the registry."""

    def setup_method(self):
        AttackTechniqueRegistry.reset_instance()
        self.registry = AttackTechniqueRegistry.get_registry_singleton()

    def teardown_method(self):
        AttackTechniqueRegistry.reset_instance()

    def test_build_metadata_returns_component_identifier(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="stub", factory=factory)

        metadata = self.registry.list_metadata()

        assert len(metadata) == 1
        assert isinstance(metadata[0], ComponentIdentifier)
        assert metadata[0].class_name == "AttackTechniqueFactory"

    def test_metadata_matches_factory_identifier(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="stub", factory=factory)

        metadata = self.registry.list_metadata()

        assert metadata[0] == factory.get_identifier()


class TestAttackTechniqueRegistryInherited:
    """Tests for inherited BaseInstanceRegistry methods."""

    def setup_method(self):
        AttackTechniqueRegistry.reset_instance()
        self.registry = AttackTechniqueRegistry.get_registry_singleton()

    def teardown_method(self):
        AttackTechniqueRegistry.reset_instance()

    def test_contains(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="exists", factory=factory)

        assert "exists" in self.registry
        assert "missing" not in self.registry

    def test_len(self):
        assert len(self.registry) == 0

        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="a", factory=factory)

        assert len(self.registry) == 1

    def test_get_names_returns_sorted(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="zeta", factory=factory)
        self.registry.register_technique(name="alpha", factory=factory)
        self.registry.register_technique(name="beta", factory=factory)

        assert self.registry.get_names() == ["alpha", "beta", "zeta"]

    def test_tag_based_queries(self):
        factory1 = AttackTechniqueFactory(attack_class=_StubAttack)
        factory2 = AttackTechniqueFactory(attack_class=_StubAttack, attack_kwargs={"max_turns": 20})

        self.registry.register_technique(name="f1", factory=factory1, tags=["multi_turn"])
        self.registry.register_technique(name="f2", factory=factory2, tags=["single_turn"])

        multi = self.registry.get_by_tag(tag="multi_turn")
        assert len(multi) == 1
        assert multi[0].name == "f1"

        single = self.registry.get_by_tag(tag="single_turn")
        assert len(single) == 1
        assert single[0].name == "f2"

    def test_iter_yields_sorted_names(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="b", factory=factory)
        self.registry.register_technique(name="a", factory=factory)

        assert list(self.registry) == ["a", "b"]

    def test_get_factories_returns_dict_mapping(self):
        factory_a = AttackTechniqueFactory(attack_class=_StubAttack)
        factory_b = AttackTechniqueFactory(attack_class=_StubAttack, attack_kwargs={"max_turns": 5})
        self.registry.register_technique(name="alpha", factory=factory_a)
        self.registry.register_technique(name="beta", factory=factory_b)

        result = self.registry.get_factories()

        assert isinstance(result, dict)
        assert set(result.keys()) == {"alpha", "beta"}
        assert result["alpha"] is factory_a
        assert result["beta"] is factory_b

    def test_get_factories_empty_registry(self):
        result = self.registry.get_factories()
        assert result == {}


class TestAttackTechniqueRegistryScorerOverridePolicy:
    """Tests for the scorer_override_policy property on the registry."""

    def setup_method(self):
        AttackTechniqueRegistry.reset_instance()
        self.registry = AttackTechniqueRegistry.get_registry_singleton()

    def teardown_method(self):
        AttackTechniqueRegistry.reset_instance()

    def test_default_policy_is_warn(self):
        """Registry defaults to WARN policy."""
        assert self.registry.scorer_override_policy == ScorerOverridePolicy.WARN

    def test_policy_is_read_only(self):
        """Policy property has no setter — it's read-only."""
        with pytest.raises(AttributeError):
            self.registry.scorer_override_policy = ScorerOverridePolicy.RAISE

    def test_policy_passed_to_factories_via_register_from_specs(self):
        """Factories built via register_from_specs inherit the registry's default policy."""
        spec = AttackTechniqueSpec(name="stub_policy", attack_class=_StubAttack, strategy_tags=["test"])
        self.registry.register_from_specs([spec])

        factory = self.registry._registry_items["stub_policy"].instance
        assert factory._scorer_override_policy == ScorerOverridePolicy.WARN


class TestScenarioTechniqueSpecsValid:
    """Validate that every AttackTechniqueSpec in SCENARIO_TECHNIQUES is well-formed."""

    @pytest.mark.parametrize("spec", SCENARIO_TECHNIQUES, ids=lambda s: s.name)
    def test_spec_extra_kwargs_match_attack_class_constructor(self, spec: AttackTechniqueSpec):
        """Each spec's extra_kwargs must be valid parameters of its attack_class."""
        factory = AttackTechniqueRegistry.build_factory_from_spec(spec)
        assert factory.attack_class is spec.attack_class

    @pytest.mark.parametrize("spec", SCENARIO_TECHNIQUES, ids=lambda s: s.name)
    def test_spec_attack_class_accepts_objective_target(self, spec: AttackTechniqueSpec):
        """Every attack class must accept objective_target (required at create time)."""
        sig = inspect.signature(spec.attack_class.__init__)
        assert "objective_target" in sig.parameters, (
            f"{spec.attack_class.__name__} is missing required 'objective_target' parameter"
        )

    def test_spec_names_are_unique(self):
        """No two specs should share the same name."""
        names = [spec.name for spec in SCENARIO_TECHNIQUES]
        assert len(names) == len(set(names)), f"Duplicate spec names: {[n for n in names if names.count(n) > 1]}"

    @pytest.mark.parametrize("spec", SCENARIO_TECHNIQUES, ids=lambda s: s.name)
    def test_spec_adversarial_fields_not_both_set(self, spec: AttackTechniqueSpec):
        """adversarial_chat and adversarial_chat_key must be mutually exclusive."""
        assert not (spec.adversarial_chat and spec.adversarial_chat_key), (
            f"Spec '{spec.name}' sets both adversarial_chat and adversarial_chat_key"
        )


class TestPairTechniqueRegistration:
    """Targeted tests for the PAIR technique spec in SCENARIO_TECHNIQUES."""

    def test_pair_spec_registered_with_pair_attack_class(self):
        from pyrit.executor.attack import PAIRAttack

        pair_specs = [s for s in SCENARIO_TECHNIQUES if s.name == "pair"]
        assert len(pair_specs) == 1, "Expected exactly one 'pair' spec in SCENARIO_TECHNIQUES"
        spec = pair_specs[0]
        assert spec.attack_class is PAIRAttack
        assert set(spec.strategy_tags) >= {"core", "multi_turn"}
        assert not spec.extra_kwargs, "PAIR defaults are encoded on PAIRAttack itself, not via extra_kwargs"


class TestScorerOverrideTypeInference:
    """
    Tests verifying scorer compatibility type inference for real attack classes.

    TAP narrows its annotation to TAPAttackScoringConfig — generic AttackScoringConfig
    should be rejected (per policy). PromptSendingAttack uses the base AttackScoringConfig
    annotation — any config should pass through.
    """

    def _make_generic_scoring_config(self):
        """Create a valid generic AttackScoringConfig with a mocked TrueFalseScorer."""
        from pyrit.score import TrueFalseScorer

        mock_scorer = MagicMock(spec=TrueFalseScorer)
        return AttackScoringConfig(objective_scorer=mock_scorer)

    def test_tap_factory_rejects_generic_config_with_raise_policy(self):
        """TAP factory raises when given a generic AttackScoringConfig and policy is RAISE."""
        from pyrit.executor.attack.multi_turn.tree_of_attacks import TreeOfAttacksWithPruningAttack

        factory = AttackTechniqueFactory(
            attack_class=TreeOfAttacksWithPruningAttack,
            scorer_override_policy=ScorerOverridePolicy.RAISE,
        )

        generic_config = self._make_generic_scoring_config()
        target = MagicMock(spec=PromptTarget)

        with pytest.raises(ValueError, match="incompatible"):
            factory.create(
                objective_target=target,
                attack_scoring_config=generic_config,
            )

    def test_tap_factory_warns_on_generic_config_with_warn_policy(self, caplog):
        """TAP factory logs warning and skips override when policy is WARN."""
        import logging

        from pyrit.executor.attack.multi_turn.tree_of_attacks import TreeOfAttacksWithPruningAttack

        factory = AttackTechniqueFactory(
            attack_class=TreeOfAttacksWithPruningAttack,
            scorer_override_policy=ScorerOverridePolicy.WARN,
        )

        generic_config = self._make_generic_scoring_config()
        target = MagicMock(spec=PromptTarget)

        # TAP will fail downstream (missing adversarial config), but the scorer
        # override should be skipped with a warning — not a scorer ValueError.
        with caplog.at_level(logging.WARNING):
            with pytest.raises(Exception) as exc_info:
                factory.create(
                    objective_target=target,
                    attack_scoring_config=generic_config,
                )

        # The downstream error should NOT be about scorer incompatibility
        assert "incompatible" not in str(exc_info.value).lower()
        # A warning about incompatibility should be logged
        assert any("incompatible" in record.message.lower() for record in caplog.records)

    def test_tap_factory_silently_skips_on_generic_config_with_skip_policy(self, caplog):
        """TAP factory silently skips override when policy is SKIP."""
        import logging

        from pyrit.executor.attack.multi_turn.tree_of_attacks import TreeOfAttacksWithPruningAttack

        factory = AttackTechniqueFactory(
            attack_class=TreeOfAttacksWithPruningAttack,
            scorer_override_policy=ScorerOverridePolicy.SKIP,
        )

        generic_config = self._make_generic_scoring_config()
        target = MagicMock(spec=PromptTarget)

        with caplog.at_level(logging.WARNING):
            with pytest.raises(Exception) as exc_info:
                factory.create(
                    objective_target=target,
                    attack_scoring_config=generic_config,
                )

        # No warning about incompatibility should be logged
        assert not any("incompatible" in record.message.lower() for record in caplog.records)
        # Downstream error should not mention scorer incompatibility
        assert "incompatible" not in str(exc_info.value).lower()

    def test_tap_factory_accepts_tap_scoring_config(self):
        """TAP factory forwards TAPAttackScoringConfig regardless of policy."""
        from pyrit.executor.attack.multi_turn.tree_of_attacks import (
            TAPAttackScoringConfig,
            TreeOfAttacksWithPruningAttack,
        )
        from pyrit.score import FloatScaleThresholdScorer

        factory = AttackTechniqueFactory(
            attack_class=TreeOfAttacksWithPruningAttack,
            scorer_override_policy=ScorerOverridePolicy.RAISE,
        )

        mock_scorer = MagicMock(spec=FloatScaleThresholdScorer)
        mock_scorer.threshold = 0.7
        tap_config = TAPAttackScoringConfig(objective_scorer=mock_scorer)
        target = MagicMock(spec=PromptTarget)

        # TAP will fail downstream (adversarial config missing), but
        # the factory should NOT raise about scorer incompatibility
        with pytest.raises(Exception) as exc_info:
            factory.create(
                objective_target=target,
                attack_scoring_config=tap_config,
            )

        # The error should NOT be about scorer compatibility
        assert "incompatible" not in str(exc_info.value).lower()

    def test_prompt_sending_factory_accepts_any_config(self):
        """PromptSendingAttack accepts base AttackScoringConfig — any config passes through."""
        from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
        from pyrit.memory import CentralMemory

        factory = AttackTechniqueFactory(
            attack_class=PromptSendingAttack,
            scorer_override_policy=ScorerOverridePolicy.RAISE,
        )

        generic_config = self._make_generic_scoring_config()
        target = MagicMock(spec=PromptTarget)

        mock_memory = MagicMock()
        CentralMemory.set_memory_instance(mock_memory)
        try:
            # Should NOT raise — PromptSendingAttack accepts base AttackScoringConfig
            technique = factory.create(
                objective_target=target,
                attack_scoring_config=generic_config,
            )
            assert technique is not None
        finally:
            CentralMemory.set_memory_instance(None)  # type: ignore[arg-type]

    def test_prompt_sending_factory_accepts_tap_scoring_config(self):
        """PromptSendingAttack accepts TAPAttackScoringConfig (subclass of base) — passes through."""
        from pyrit.executor.attack.multi_turn.tree_of_attacks import TAPAttackScoringConfig
        from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
        from pyrit.memory import CentralMemory
        from pyrit.score import FloatScaleThresholdScorer

        factory = AttackTechniqueFactory(
            attack_class=PromptSendingAttack,
            scorer_override_policy=ScorerOverridePolicy.RAISE,
        )

        mock_scorer = MagicMock(spec=FloatScaleThresholdScorer)
        mock_scorer.threshold = 0.7
        tap_config = TAPAttackScoringConfig(objective_scorer=mock_scorer)
        target = MagicMock(spec=PromptTarget)

        mock_memory = MagicMock()
        CentralMemory.set_memory_instance(mock_memory)
        try:
            # TAPAttackScoringConfig is-a AttackScoringConfig, so it passes isinstance check
            technique = factory.create(
                objective_target=target,
                attack_scoring_config=tap_config,
            )
            assert technique is not None
        finally:
            CentralMemory.set_memory_instance(None)  # type: ignore[arg-type]

    def test_factory_raises_when_attack_has_no_scoring_param_and_policy_raise(self):
        """Factory raises when attack doesn't accept attack_scoring_config and policy is RAISE."""
        factory = AttackTechniqueFactory(
            attack_class=_StubAttackNoScorer,
            scorer_override_policy=ScorerOverridePolicy.RAISE,
        )

        generic_config = self._make_generic_scoring_config()
        target = MagicMock(spec=PromptTarget)

        with pytest.raises(ValueError, match="does not accept"):
            factory.create(
                objective_target=target,
                attack_scoring_config=generic_config,
            )

    def test_factory_warns_when_attack_has_no_scoring_param_and_policy_warn(self, caplog):
        """Factory warns when attack doesn't accept attack_scoring_config and policy is WARN."""
        import logging

        factory = AttackTechniqueFactory(
            attack_class=_StubAttackNoScorer,
            scorer_override_policy=ScorerOverridePolicy.WARN,
        )

        generic_config = self._make_generic_scoring_config()
        target = MagicMock(spec=PromptTarget)

        with caplog.at_level(logging.WARNING):
            technique = factory.create(
                objective_target=target,
                attack_scoring_config=generic_config,
            )

        assert technique is not None
        assert any("does not accept" in record.message for record in caplog.records)

    def test_factory_skips_silently_when_attack_has_no_scoring_param_and_policy_skip(self, caplog):
        """Factory silently skips when attack doesn't accept attack_scoring_config and policy is SKIP."""
        import logging

        factory = AttackTechniqueFactory(
            attack_class=_StubAttackNoScorer,
            scorer_override_policy=ScorerOverridePolicy.SKIP,
        )

        generic_config = self._make_generic_scoring_config()
        target = MagicMock(spec=PromptTarget)

        with caplog.at_level(logging.WARNING):
            technique = factory.create(
                objective_target=target,
                attack_scoring_config=generic_config,
            )

        assert technique is not None
        assert not any("does not accept" in record.message for record in caplog.records)
