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
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
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


class TestAttackTechniqueRegistryCreateTechnique:
    """Tests for create_technique()."""

    def setup_method(self):
        AttackTechniqueRegistry.reset_instance()
        self.registry = AttackTechniqueRegistry.get_registry_singleton()

    def teardown_method(self):
        AttackTechniqueRegistry.reset_instance()

    def test_create_technique_returns_attack_technique(self):
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="stub", factory=factory)
        target = MagicMock(spec=PromptTarget)
        scoring = MagicMock(spec=AttackScoringConfig)

        technique = self.registry.create_technique(
            "stub", objective_target=target, attack_scoring_config_override=scoring
        )

        assert isinstance(technique, AttackTechnique)
        assert isinstance(technique.attack, _StubAttack)
        assert technique.attack.objective_target is target

    def test_create_technique_passes_scoring_config(self):
        class _ScoringStub:
            def __init__(self, *, objective_target, attack_scoring_config=None):
                self.objective_target = objective_target
                self.attack_scoring_config = attack_scoring_config

            def get_identifier(self):
                return ComponentIdentifier(class_name="_ScoringStub", class_module="test")

        factory = AttackTechniqueFactory(attack_class=_ScoringStub)
        self.registry.register_technique(name="scoring_stub", factory=factory)
        target = MagicMock(spec=PromptTarget)
        scoring = MagicMock(spec=AttackScoringConfig)

        technique = self.registry.create_technique(
            "scoring_stub", objective_target=target, attack_scoring_config_override=scoring
        )

        assert technique.attack.attack_scoring_config is scoring

    def test_create_technique_raises_on_missing_name(self):
        with pytest.raises(KeyError, match="No technique registered with name 'nonexistent'"):
            self.registry.create_technique(
                "nonexistent",
                objective_target=MagicMock(spec=PromptTarget),
                attack_scoring_config_override=MagicMock(spec=AttackScoringConfig),
            )

    def test_create_technique_preserves_frozen_kwargs(self):
        factory = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 42},
        )
        self.registry.register_technique(name="custom", factory=factory)
        target = MagicMock(spec=PromptTarget)

        technique = self.registry.create_technique(
            "custom", objective_target=target, attack_scoring_config_override=MagicMock(spec=AttackScoringConfig)
        )

        assert technique.attack.max_turns == 42


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


class TestAttackTechniqueRegistryAcceptsScorerOverride:
    """Tests for the accepts_scorer_override() method."""

    def setup_method(self):
        AttackTechniqueRegistry.reset_instance()
        self.registry = AttackTechniqueRegistry.get_registry_singleton()

    def teardown_method(self):
        AttackTechniqueRegistry.reset_instance()

    def test_accepts_scorer_override_defaults_to_true(self):
        """Technique registered without explicit setting defaults to True."""
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="default_technique", factory=factory)

        assert self.registry.accepts_scorer_override("default_technique") is True

    def test_accepts_scorer_override_explicit_false(self):
        """Technique registered with accepts_scorer_override=False returns False."""
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="tap_like", factory=factory, accepts_scorer_override=False)

        assert self.registry.accepts_scorer_override("tap_like") is False

    def test_accepts_scorer_override_explicit_true(self):
        """Technique registered with accepts_scorer_override=True returns True."""
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="standard", factory=factory, accepts_scorer_override=True)

        assert self.registry.accepts_scorer_override("standard") is True

    def test_accepts_scorer_override_raises_on_missing_name(self):
        """KeyError when querying a non-existent technique."""
        with pytest.raises(KeyError):
            self.registry.accepts_scorer_override("nonexistent")

    def test_accepts_scorer_override_not_stored_in_tags(self):
        """The accepts_scorer_override flag must not pollute the tag namespace."""
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(
            name="clean_tags",
            factory=factory,
            tags=["single_turn"],
            accepts_scorer_override=False,
        )

        entry = self.registry._registry_items["clean_tags"]
        assert "accepts_scorer_override" not in entry.tags

    def test_accepts_scorer_override_stored_in_metadata(self):
        """The flag is stored in entry.metadata as a native bool."""
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="meta_check", factory=factory, accepts_scorer_override=False)

        entry = self.registry._registry_items["meta_check"]
        assert entry.metadata["accepts_scorer_override"] is False

    def test_get_by_tag_does_not_return_accepts_scorer_override(self):
        """get_by_tag('accepts_scorer_override') must return empty — it's not a tag."""
        factory = AttackTechniqueFactory(attack_class=_StubAttack)
        self.registry.register_technique(name="technique", factory=factory, accepts_scorer_override=False)

        results = self.registry.get_by_tag(tag="accepts_scorer_override")
        assert results == []


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
