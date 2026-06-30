# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for the merged ``ScorerRegistry`` (buildable catalog + instance container)
and its introspection helpers.
"""

import pytest

from pyrit.models import ComponentIdentifier, Message, MessagePiece, Score
from pyrit.models.parameter import ComponentType
from pyrit.prompt_target import (
    PromptTarget,
    TargetCapabilities,
    TargetConfiguration,
)
from pyrit.registry.components import (
    ScorerMetadata,
    ScorerRegistry,
    TargetRegistry,
)
from pyrit.registry.resolution import derive_parameters
from pyrit.score import (
    SelfAskRefusalScorer,
    TrueFalseCompositeScorer,
    TrueFalseScoreAggregator,
)
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.scorer import Scorer
from pyrit.score.scorer_prompt_validator import ScorerPromptValidator
from pyrit.score.true_false.true_false_scorer import TrueFalseScorer


class DummyValidator(ScorerPromptValidator):
    """Dummy validator for testing."""

    def validate(self, message, objective=None):
        pass

    def is_message_piece_supported(self, message_piece):
        return True


class MockTrueFalseScorer(TrueFalseScorer):
    """Mock TrueFalseScorer for testing."""

    def __init__(self):
        super().__init__(validator=DummyValidator())

    def _build_identifier(self) -> ComponentIdentifier:
        """Build the scorer evaluation identifier for this mock scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier()

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        return []

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return []

    def validate_return_scores(self, scores: list[Score]):
        pass


class MockFloatScaleScorer(FloatScaleScorer):
    """Mock FloatScaleScorer for testing."""

    def __init__(self):
        super().__init__(validator=DummyValidator())

    def _build_identifier(self) -> ComponentIdentifier:
        """Build the scorer evaluation identifier for this mock scorer.

        Returns:
            ComponentIdentifier: The identifier for this scorer.
        """
        return self._create_identifier()

    async def _score_async(self, message: Message, *, objective: str | None = None) -> list[Score]:
        return []

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: str | None = None) -> list[Score]:
        return []

    def validate_return_scores(self, scores: list[Score]):
        pass


class MockChatTarget(PromptTarget):
    """Minimal multi-turn capable target so LLM scorers can be built by name."""

    _DEFAULT_CONFIGURATION: TargetConfiguration = TargetConfiguration(
        capabilities=TargetCapabilities(
            supports_multi_turn=True,
            supports_multi_message_pieces=True,
            supports_system_prompt=True,
            supports_editable_history=True,
        )
    )

    def __init__(self, *, model_name: str = "mock_model") -> None:
        super().__init__(model_name=model_name)

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        return [MessagePiece(role="assistant", original_value="mock response").to_message()]

    def _validate_request(self, *, normalized_conversation: list[Message]) -> None:
        pass


@pytest.fixture
def registry():
    """Provide a fresh ``ScorerRegistry`` singleton, reset around each test."""
    ScorerRegistry.reset_registry_singleton()
    instance = ScorerRegistry.get_registry_singleton()
    yield instance
    ScorerRegistry.reset_registry_singleton()


# ---------------------------------------------------------------------------
# Instance container (reached via the ``instances`` property)
# ---------------------------------------------------------------------------


class TestScorerRegistrySingleton:
    """Tests for the singleton pattern in ScorerRegistry."""

    def setup_method(self):
        ScorerRegistry.reset_registry_singleton()

    def teardown_method(self):
        ScorerRegistry.reset_registry_singleton()

    def test_get_registry_singleton_returns_same_instance(self):
        assert ScorerRegistry.get_registry_singleton() is ScorerRegistry.get_registry_singleton()

    def test_get_registry_singleton_returns_scorer_registry_type(self):
        assert isinstance(ScorerRegistry.get_registry_singleton(), ScorerRegistry)

    def test_reset_registry_singleton_clears_singleton(self):
        instance1 = ScorerRegistry.get_registry_singleton()
        ScorerRegistry.reset_registry_singleton()
        assert ScorerRegistry.get_registry_singleton() is not instance1


@pytest.mark.usefixtures("patch_central_database")
class TestScorerRegistryRegisterInstance:
    """Tests for instance registration via the ``instances`` property."""

    def test_register_instance_with_custom_name(self, registry: ScorerRegistry):
        scorer = MockTrueFalseScorer()
        registry.instances.register(scorer, name="custom_scorer")

        assert "custom_scorer" in registry.instances
        assert registry.instances.get("custom_scorer") is scorer

    def test_register_instance_generates_name_from_class(self, registry: ScorerRegistry):
        scorer = MockTrueFalseScorer()
        registry.instances.register(scorer)

        names = registry.instances.get_names()
        assert len(names) == 1
        assert names[0].startswith("MockTrueFalseScorer::")

    def test_register_instance_multiple_scorers_unique_names(self, registry: ScorerRegistry):
        registry.instances.register(MockTrueFalseScorer())
        registry.instances.register(MockFloatScaleScorer())

        assert len(registry.instances) == 2

    def test_register_instance_duplicate_name_overwrites(self, registry: ScorerRegistry):
        first = MockTrueFalseScorer()
        second = MockTrueFalseScorer()

        registry.instances.register(first, name="same_name")
        registry.instances.register(second, name="same_name")

        assert len(registry.instances) == 1
        assert registry.instances.get("same_name") is second

    def test_register_instance_rejects_non_scorer(self, registry: ScorerRegistry):
        class NotAScorer:
            pass

        with pytest.raises(TypeError, match="Scorer"):
            registry.instances.register(NotAScorer())  # type: ignore[arg-type]

        assert len(registry.instances) == 0


@pytest.mark.usefixtures("patch_central_database")
class TestScorerRegistryGetInstanceByName:
    """Tests for instance lookup via ``instances.get``."""

    def test_get_instance_by_name_returns_scorer(self, registry: ScorerRegistry):
        scorer = MockTrueFalseScorer()
        registry.instances.register(scorer, name="test_scorer")
        assert registry.instances.get("test_scorer") is scorer

    def test_get_instance_by_name_nonexistent_returns_none(self, registry: ScorerRegistry):
        assert registry.instances.get("nonexistent") is None


@pytest.mark.usefixtures("patch_central_database")
class TestScorerRegistryInstanceMetadata:
    """Tests for instance-level metadata (``instances.list_metadata``)."""

    def test_instance_metadata_is_component_identifier(self, registry: ScorerRegistry):
        scorer = MockTrueFalseScorer()
        registry.instances.register(scorer, name="tf_scorer")

        metadata = registry.instances.list_metadata()
        assert len(metadata) == 1
        assert isinstance(metadata[0], ComponentIdentifier)
        assert metadata[0].class_name == "MockTrueFalseScorer"

    def test_instance_metadata_filter_by_class_name(self, registry: ScorerRegistry):
        registry.instances.register(MockTrueFalseScorer(), name="tf1")
        registry.instances.register(MockTrueFalseScorer(), name="tf2")
        registry.instances.register(MockFloatScaleScorer(), name="fs1")

        tf_metadata = registry.instances.list_metadata(include_filters={"class_name": "MockTrueFalseScorer"})
        assert len(tf_metadata) == 2
        assert all(m.class_name == "MockTrueFalseScorer" for m in tf_metadata)


@pytest.mark.usefixtures("patch_central_database")
class TestScorerRegistryContainerProtocol:
    """Tests for the ``instances`` container protocol surface."""

    def test_contains_and_len_and_iter(self, registry: ScorerRegistry):
        registry.instances.register(MockTrueFalseScorer(), name="test_scorer")
        assert "test_scorer" in registry.instances
        assert "unknown_scorer" not in registry.instances
        assert len(registry.instances) == 1
        assert "test_scorer" in list(registry.instances)

    def test_get_names_returns_sorted_list(self, registry: ScorerRegistry):
        registry.instances.register(MockFloatScaleScorer(), name="zeta_scorer")
        registry.instances.register(MockFloatScaleScorer(), name="alpha_scorer")
        assert registry.instances.get_names() == ["alpha_scorer", "zeta_scorer"]

    def test_get_all_instances_returns_all(self, registry: ScorerRegistry):
        tf = MockTrueFalseScorer()
        fs = MockFloatScaleScorer()
        registry.instances.register(tf, name="tf")
        registry.instances.register(fs, name="fs")

        entry_map = {e.name: e for e in registry.instances.get_all_instances()}
        assert entry_map["tf"].instance is tf
        assert entry_map["fs"].instance is fs

    def test_get_by_tag_returns_tagged_entries(self, registry: ScorerRegistry):
        registry.instances.register(MockTrueFalseScorer(), name="tagged", tags=["best"])
        registry.instances.register(MockTrueFalseScorer(), name="untagged")

        entries = registry.instances.get_by_tag(tag="best")
        assert [e.name for e in entries] == ["tagged"]


# ---------------------------------------------------------------------------
# Buildable class catalog (discovery + introspection + build)
# ---------------------------------------------------------------------------


class TestDiscovery:
    """Tests for scorer class discovery."""

    def test_discovers_known_scorers(self, registry: ScorerRegistry):
        names = registry.get_class_names()
        assert "SelfAskRefusalScorer" in names
        assert "TrueFalseCompositeScorer" in names

    def test_does_not_register_base_class(self, registry: ScorerRegistry):
        assert "Scorer" not in registry.get_class_names()
        assert "TrueFalseScorer" not in registry.get_class_names()

    def test_keyed_by_exact_class_name(self, registry: ScorerRegistry):
        names = registry.get_class_names()
        assert "SelfAskRefusalScorer" in names
        assert "self_ask_refusal_scorer" not in names


class TestGetClass:
    """Tests for get_class (the inherited class-catalog accessor)."""

    def test_returns_class(self, registry: ScorerRegistry):
        assert registry.get_class("SelfAskRefusalScorer") is SelfAskRefusalScorer

    def test_unknown_type_raises(self, registry: ScorerRegistry):
        with pytest.raises(KeyError, match="not found"):
            registry.get_class("NotARealScorer")

    def test_is_subclass_relationship(self, registry: ScorerRegistry):
        assert issubclass(registry.get_class("SelfAskRefusalScorer"), Scorer)


@pytest.mark.usefixtures("patch_central_database")
class TestCreateLLMScorer:
    """Tests that LLM scorers are buildable by resolving a target by name."""

    def test_build_llm_scorer_resolves_chat_target_by_name(self, registry: ScorerRegistry):
        target = MockChatTarget()
        TargetRegistry.reset_registry_singleton()
        TargetRegistry.get_registry_singleton().instances.register(target, name="scorer_target")
        try:
            scorer = registry.create_instance("SelfAskRefusalScorer", chat_target="scorer_target")
            assert isinstance(scorer, SelfAskRefusalScorer)
            assert scorer.get_chat_target() is target
        finally:
            TargetRegistry.reset_registry_singleton()

    def test_build_llm_scorer_unknown_target_raises(self, registry: ScorerRegistry):
        TargetRegistry.reset_registry_singleton()
        try:
            with pytest.raises(ValueError, match="not found"):
                registry.create_instance("SelfAskRefusalScorer", chat_target="missing")
        finally:
            TargetRegistry.reset_registry_singleton()

    def test_build_llm_scorer_list_for_scalar_reference_raises(self, registry: ScorerRegistry):
        # A scalar reference (``chat_target``) must reject a list value.
        with pytest.raises(ValueError, match="expected a single"):
            registry.create_instance("SelfAskRefusalScorer", chat_target=["a", "b"])


@pytest.mark.usefixtures("patch_central_database")
class TestCreateCompositeScorer:
    """Tests the list-aware SCORER reference path (composite from a list of names)."""

    def test_build_composite_resolves_sub_scorers_by_name(self, registry: ScorerRegistry):
        registry.instances.register(MockTrueFalseScorer(), name="s1")
        registry.instances.register(MockTrueFalseScorer(), name="s2")

        composite = registry.create_instance(
            "TrueFalseCompositeScorer",
            scorers=["s1", "s2"],
            aggregator=TrueFalseScoreAggregator.OR,
        )
        assert isinstance(composite, TrueFalseCompositeScorer)

    def test_build_composite_unknown_sub_scorer_raises(self, registry: ScorerRegistry):
        registry.instances.register(MockTrueFalseScorer(), name="s1")
        with pytest.raises(ValueError, match="not found"):
            registry.create_instance(
                "TrueFalseCompositeScorer",
                scorers=["s1", "missing"],
                aggregator=TrueFalseScoreAggregator.OR,
            )

    def test_build_composite_resolves_prebuilt_scorers_in_list(self, registry: ScorerRegistry):
        # Passthrough path inside a list: already-built scorers pass through unchanged.
        composite = registry.create_instance(
            "TrueFalseCompositeScorer",
            scorers=[MockTrueFalseScorer(), MockTrueFalseScorer()],
            aggregator=TrueFalseScoreAggregator.OR,
        )
        assert isinstance(composite, TrueFalseCompositeScorer)

    def test_build_composite_mixes_names_and_instances(self, registry: ScorerRegistry):
        registry.instances.register(MockTrueFalseScorer(), name="s1")
        composite = registry.create_instance(
            "TrueFalseCompositeScorer",
            scorers=["s1", MockTrueFalseScorer()],
            aggregator=TrueFalseScoreAggregator.OR,
        )
        assert isinstance(composite, TrueFalseCompositeScorer)

    def test_build_composite_scalar_for_list_reference_raises(self, registry: ScorerRegistry):
        registry.instances.register(MockTrueFalseScorer(), name="s1")
        with pytest.raises(ValueError, match="expected a list"):
            registry.create_instance(
                "TrueFalseCompositeScorer",
                scorers="s1",
                aggregator=TrueFalseScoreAggregator.OR,
            )


class TestClassMetadata:
    """Tests for scorer class-catalog metadata building."""

    def _metadata_for(self, registry: ScorerRegistry, name: str) -> ScorerMetadata:
        return next(m for m in registry.get_all_registered_class_metadata() if m.class_name == name)

    def test_metadata_is_scorer_metadata(self, registry: ScorerRegistry):
        meta = self._metadata_for(registry, "SelfAskRefusalScorer")
        assert isinstance(meta, ScorerMetadata)
        assert meta.class_name == "SelfAskRefusalScorer"

    def test_is_llm_based_flag(self, registry: ScorerRegistry):
        # An LLM scorer takes a ``chat_target`` (TARGET reference); a composite does not.
        assert self._metadata_for(registry, "SelfAskRefusalScorer").is_llm_based is True
        assert self._metadata_for(registry, "TrueFalseCompositeScorer").is_llm_based is False

    def test_composite_scorers_param_is_reference(self, registry: ScorerRegistry):
        meta = self._metadata_for(registry, "TrueFalseCompositeScorer")
        assert any(p.is_reference_to(ComponentType.SCORER) for p in meta.parameters)


class TestRegistrationGate:
    """The identifier blueprint must line up with a resolvable contract for every scorer."""

    def test_discovery_validates_all_scorers(self, registry: ScorerRegistry) -> None:
        names = registry.get_class_names()
        assert names
        assert "SelfAskRefusalScorer" in names

    def test_is_llm_based_matches_target_reference(self, registry: ScorerRegistry) -> None:
        from pyrit.models.identifiers import ScorerIdentifier

        for meta in registry.get_all_registered_class_metadata():
            parameters = derive_parameters(cls=registry.get_class(meta.class_name), identifier_type=ScorerIdentifier)
            has_target = any(p.is_reference_to(ComponentType.TARGET) for p in parameters)
            assert meta.is_llm_based is has_target, f"is_llm_based mismatch for {meta.class_name}"
