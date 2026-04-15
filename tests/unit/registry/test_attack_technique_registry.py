# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the AttackTechniqueRegistry class."""

from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.identifiers import ComponentIdentifier
from pyrit.prompt_target import PromptTarget
from pyrit.registry.object_registries.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory


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

        technique = self.registry.create_technique("stub", objective_target=target, attack_scoring_config=scoring)

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
            "scoring_stub", objective_target=target, attack_scoring_config=scoring
        )

        assert technique.attack.attack_scoring_config is scoring

    def test_create_technique_raises_on_missing_name(self):
        with pytest.raises(KeyError, match="No technique registered with name 'nonexistent'"):
            self.registry.create_technique(
                "nonexistent",
                objective_target=MagicMock(spec=PromptTarget),
                attack_scoring_config=MagicMock(spec=AttackScoringConfig),
            )

    def test_create_technique_preserves_frozen_kwargs(self):
        factory = AttackTechniqueFactory(
            attack_class=_StubAttack,
            attack_kwargs={"max_turns": 42},
        )
        self.registry.register_technique(name="custom", factory=factory)
        target = MagicMock(spec=PromptTarget)

        technique = self.registry.create_technique(
            "custom", objective_target=target, attack_scoring_config=MagicMock(spec=AttackScoringConfig)
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
