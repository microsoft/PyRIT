# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the AttackTechniqueRegistry class."""

import inspect
from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.models import ComponentIdentifier
from pyrit.prompt_target import PromptTarget
from pyrit.registry import TargetRegistry
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory, ScorerOverridePolicy
from pyrit.setup.initializers.components.scenario_techniques import build_scenario_technique_factories


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
        factory = AttackTechniqueFactory(name="stub_attack", attack_class=_StubAttack)

        self.registry.register_technique(name="stub_attack", factory=factory)

        assert "stub_attack" in self.registry
        assert self.registry._registry_items["stub_attack"].instance is factory

    def test_register_technique_with_tags(self):
        factory = AttackTechniqueFactory(name="stub_attack", attack_class=_StubAttack)

        self.registry.register_technique(
            name="stub_attack",
            factory=factory,
            tags=["single_turn", "encoding"],
        )

        entries = self.registry.get_by_tag(tag="single_turn")
        assert len(entries) == 1
        assert entries[0].name == "stub_attack"

    def test_register_multiple_techniques(self):
        factory1 = AttackTechniqueFactory(name="stub_5", attack_class=_StubAttack)
        factory2 = AttackTechniqueFactory(
            name="stub_20",
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
        factory = AttackTechniqueFactory(name="stub", attack_class=_StubAttack)
        self.registry.register_technique(name="stub", factory=factory)

        metadata = self.registry.list_metadata()

        assert len(metadata) == 1
        assert isinstance(metadata[0], ComponentIdentifier)
        assert metadata[0].class_name == "AttackTechniqueFactory"

    def test_metadata_matches_factory_identifier(self):
        factory = AttackTechniqueFactory(name="stub", attack_class=_StubAttack)
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
        factory = AttackTechniqueFactory(name="exists", attack_class=_StubAttack)
        self.registry.register_technique(name="exists", factory=factory)

        assert "exists" in self.registry
        assert "missing" not in self.registry

    def test_len(self):
        assert len(self.registry) == 0

        factory = AttackTechniqueFactory(name="a", attack_class=_StubAttack)
        self.registry.register_technique(name="a", factory=factory)

        assert len(self.registry) == 1

    def test_get_names_returns_sorted(self):
        factory_zeta = AttackTechniqueFactory(name="zeta", attack_class=_StubAttack)
        factory_alpha = AttackTechniqueFactory(name="alpha", attack_class=_StubAttack)
        factory_beta = AttackTechniqueFactory(name="beta", attack_class=_StubAttack)
        self.registry.register_technique(name="zeta", factory=factory_zeta)
        self.registry.register_technique(name="alpha", factory=factory_alpha)
        self.registry.register_technique(name="beta", factory=factory_beta)

        assert self.registry.get_names() == ["alpha", "beta", "zeta"]

    def test_tag_based_queries(self):
        factory1 = AttackTechniqueFactory(name="f1", attack_class=_StubAttack)
        factory2 = AttackTechniqueFactory(name="f2", attack_class=_StubAttack, attack_kwargs={"max_turns": 20})

        self.registry.register_technique(name="f1", factory=factory1, tags=["multi_turn"])
        self.registry.register_technique(name="f2", factory=factory2, tags=["single_turn"])

        multi = self.registry.get_by_tag(tag="multi_turn")
        assert len(multi) == 1
        assert multi[0].name == "f1"

        single = self.registry.get_by_tag(tag="single_turn")
        assert len(single) == 1
        assert single[0].name == "f2"

    def test_iter_yields_sorted_names(self):
        factory_b = AttackTechniqueFactory(name="b", attack_class=_StubAttack)
        factory_a = AttackTechniqueFactory(name="a", attack_class=_StubAttack)
        self.registry.register_technique(name="b", factory=factory_b)
        self.registry.register_technique(name="a", factory=factory_a)

        assert list(self.registry) == ["a", "b"]

    def test_get_factories_returns_dict_mapping(self):
        factory_a = AttackTechniqueFactory(name="alpha", attack_class=_StubAttack)
        factory_b = AttackTechniqueFactory(name="beta", attack_class=_StubAttack, attack_kwargs={"max_turns": 5})
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

    def test_get_factories_or_raise_returns_factories_when_populated(self):
        factory_a = AttackTechniqueFactory(name="alpha", attack_class=_StubAttack)
        factory_b = AttackTechniqueFactory(name="beta", attack_class=_StubAttack, attack_kwargs={"max_turns": 5})
        self.registry.register_technique(name="alpha", factory=factory_a)
        self.registry.register_technique(name="beta", factory=factory_b)

        result = self.registry.get_factories_or_raise()

        assert set(result.keys()) == {"alpha", "beta"}
        assert result["alpha"] is factory_a
        assert result["beta"] is factory_b

    def test_get_factories_or_raise_raises_when_empty(self):
        with pytest.raises(RuntimeError, match="AttackTechniqueRegistry is empty"):
            self.registry.get_factories_or_raise()


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

    def test_policy_passed_to_factories_via_register_from_factories(self):
        """Factories registered via register_from_factories inherit the registry's default policy."""
        factory = AttackTechniqueFactory(name="stub_policy", attack_class=_StubAttack, strategy_tags=["test"])
        self.registry.register_from_factories([factory])

        stored = self.registry._registry_items["stub_policy"].instance
        assert stored._scorer_override_policy == ScorerOverridePolicy.WARN


SCENARIO_FACTORIES_FIXTURE: list[AttackTechniqueFactory] = []


def _scenario_factories() -> list[AttackTechniqueFactory]:
    """Build the scenario technique factories once for parametrization.

    Uses a mock adversarial target in ``TargetRegistry`` so the build does
    not depend on environment variables or OpenAIChatTarget.
    """
    if not SCENARIO_FACTORIES_FIXTURE:
        TargetRegistry.reset_instance()
        adv_target = MagicMock(spec=PromptTarget)
        adv_target.capabilities.includes.return_value = True
        TargetRegistry.get_registry_singleton().register_instance(adv_target, name="adversarial_chat")
        SCENARIO_FACTORIES_FIXTURE.extend(build_scenario_technique_factories())
    return SCENARIO_FACTORIES_FIXTURE


class TestScenarioTechniqueFactoriesValid:
    """Validate that every factory built by ``build_scenario_technique_factories`` is well-formed."""

    @pytest.mark.parametrize("factory", _scenario_factories(), ids=lambda f: f.name)
    def test_factory_attack_class_set(self, factory: AttackTechniqueFactory):
        """Each factory references an attack class."""
        assert factory.attack_class is not None

    @pytest.mark.parametrize("factory", _scenario_factories(), ids=lambda f: f.name)
    def test_factory_attack_class_accepts_objective_target(self, factory: AttackTechniqueFactory):
        """Every attack class must accept ``objective_target`` (required at create time)."""
        sig = inspect.signature(factory.attack_class.__init__)
        assert "objective_target" in sig.parameters, (
            f"{factory.attack_class.__name__} is missing required 'objective_target' parameter"
        )

    def test_factory_names_are_unique(self):
        """No two factories should share the same name."""
        names = [f.name for f in _scenario_factories()]
        assert len(names) == len(set(names)), f"Duplicate factory names: {[n for n in names if names.count(n) > 1]}"


class TestPairTechniqueRegistration:
    """Targeted tests for the PAIR technique factory in build_scenario_technique_factories()."""

    def test_pair_factory_registered_with_pair_attack_class(self):
        from pyrit.executor.attack import PAIRAttack

        factories = build_scenario_technique_factories()
        pair_factories = [f for f in factories if f.name == "pair"]
        assert len(pair_factories) == 1, "Expected exactly one 'pair' factory"
        factory = pair_factories[0]
        assert factory.attack_class is PAIRAttack
        assert set(factory.strategy_tags) >= {"core", "multi_turn"}
        assert not factory._attack_kwargs, "PAIR defaults are encoded on PAIRAttack itself, not via attack_kwargs"


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

    def _make_adversarial_config(self):
        """Create an AttackAdversarialConfig wrapping a mock chat target."""
        from pyrit.executor.attack.core.attack_config import AttackAdversarialConfig

        chat = MagicMock(spec=PromptTarget)
        return AttackAdversarialConfig(target=chat)

    def test_tap_factory_rejects_generic_config_with_raise_policy(self):
        """TAP factory raises when given a generic AttackScoringConfig and policy is RAISE."""
        from pyrit.executor.attack.multi_turn.tree_of_attacks import TreeOfAttacksWithPruningAttack

        factory = AttackTechniqueFactory(
            name="tap_raise",
            attack_class=TreeOfAttacksWithPruningAttack,
            adversarial_config=self._make_adversarial_config(),
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
            name="tap_warn",
            attack_class=TreeOfAttacksWithPruningAttack,
            adversarial_config=self._make_adversarial_config(),
            scorer_override_policy=ScorerOverridePolicy.WARN,
        )

        generic_config = self._make_generic_scoring_config()
        target = MagicMock(spec=PromptTarget)

        # Under WARN policy, the scorer override should be skipped with a warning
        # rather than raising. The factory.create() call may succeed or fail for
        # unrelated downstream reasons — we only assert that no scorer-incompatibility
        # ValueError was raised and that a warning was emitted.
        with caplog.at_level(logging.WARNING):
            try:
                factory.create(
                    objective_target=target,
                    attack_scoring_config=generic_config,
                )
            except Exception as exc:
                assert "incompatible" not in str(exc).lower()

        # A warning about incompatibility should be logged
        assert any("incompatible" in record.message.lower() for record in caplog.records)

    def test_tap_factory_silently_skips_on_generic_config_with_skip_policy(self, caplog):
        """TAP factory silently skips override when policy is SKIP."""
        import logging

        from pyrit.executor.attack.multi_turn.tree_of_attacks import TreeOfAttacksWithPruningAttack

        factory = AttackTechniqueFactory(
            name="tap_skip",
            attack_class=TreeOfAttacksWithPruningAttack,
            adversarial_config=self._make_adversarial_config(),
            scorer_override_policy=ScorerOverridePolicy.SKIP,
        )

        generic_config = self._make_generic_scoring_config()
        target = MagicMock(spec=PromptTarget)

        # Under SKIP policy, the scorer override should be skipped silently. The
        # factory.create() call may succeed or fail for unrelated downstream reasons
        # — we only assert that no scorer-incompatibility error or warning was emitted.
        with caplog.at_level(logging.WARNING):
            try:
                factory.create(
                    objective_target=target,
                    attack_scoring_config=generic_config,
                )
            except Exception as exc:
                assert "incompatible" not in str(exc).lower()

        # No warning about incompatibility should be logged
        assert not any("incompatible" in record.message.lower() for record in caplog.records)

    def test_tap_factory_accepts_tap_scoring_config(self):
        """TAP factory forwards TAPAttackScoringConfig regardless of policy."""
        from pyrit.executor.attack.multi_turn.tree_of_attacks import (
            TAPAttackScoringConfig,
            TreeOfAttacksWithPruningAttack,
        )
        from pyrit.score import FloatScaleThresholdScorer

        factory = AttackTechniqueFactory(
            name="tap_accept",
            attack_class=TreeOfAttacksWithPruningAttack,
            adversarial_config=self._make_adversarial_config(),
            scorer_override_policy=ScorerOverridePolicy.RAISE,
        )

        mock_scorer = MagicMock(spec=FloatScaleThresholdScorer)
        mock_scorer.threshold = 0.7
        tap_config = TAPAttackScoringConfig(objective_scorer=mock_scorer)
        target = MagicMock(spec=PromptTarget)

        # The factory should NOT raise about scorer incompatibility for a TAP-typed
        # scoring config. Downstream construction may succeed or fail for unrelated
        # reasons — we only assert no scorer-compatibility error is raised.
        try:
            factory.create(
                objective_target=target,
                attack_scoring_config=tap_config,
            )
        except Exception as exc:
            assert "incompatible" not in str(exc).lower()

    def test_prompt_sending_factory_accepts_any_config(self):
        """PromptSendingAttack accepts base AttackScoringConfig — any config passes through."""
        from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
        from pyrit.memory import CentralMemory

        factory = AttackTechniqueFactory(
            name="ps_any",
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
            name="ps_tap",
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
            name="stub_noscorer_raise",
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
            name="stub_noscorer_warn",
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
            name="stub_noscorer_skip",
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
