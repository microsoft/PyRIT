# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Optional

from pyrit.identifiers import ScorerIdentifier
from pyrit.models import Message, MessagePiece, Score
from pyrit.registry.instance_registries.scorer_registry import ScorerRegistry
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

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this mock scorer."""
        self._set_identifier()

    async def _score_async(self, message: Message, *, objective: Optional[str] = None) -> list[Score]:
        return []

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        return []

    def validate_return_scores(self, scores: list[Score]):
        pass


class MockFloatScaleScorer(FloatScaleScorer):
    """Mock FloatScaleScorer for testing."""

    def __init__(self):
        super().__init__(validator=DummyValidator())

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this mock scorer."""
        self._set_identifier()

    async def _score_async(self, message: Message, *, objective: Optional[str] = None) -> list[Score]:
        return []

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        return []

    def validate_return_scores(self, scores: list[Score]):
        pass


class MockGenericScorer(Scorer):
    """Mock generic Scorer (not TrueFalse or FloatScale) for testing."""

    def __init__(self):
        super().__init__(validator=DummyValidator())

    def _build_identifier(self) -> None:
        """Build the scorer evaluation identifier for this mock scorer."""
        self._set_identifier()

    async def _score_async(self, message: Message, *, objective: Optional[str] = None) -> list[Score]:
        return []

    async def _score_piece_async(self, message_piece: MessagePiece, *, objective: Optional[str] = None) -> list[Score]:
        return []

    def validate_return_scores(self, scores: list[Score]):
        pass

    def get_scorer_metrics(self):
        return None


class TestScorerRegistrySingleton:
    """Tests for the singleton pattern in ScorerRegistry."""

    def setup_method(self):
        """Reset the singleton before each test."""
        ScorerRegistry.reset_instance()

    def teardown_method(self):
        """Reset the singleton after each test."""
        ScorerRegistry.reset_instance()

    def test_get_registry_singleton_returns_same_instance(self):
        """Test that get_registry_singleton returns the same singleton each time."""
        instance1 = ScorerRegistry.get_registry_singleton()
        instance2 = ScorerRegistry.get_registry_singleton()

        assert instance1 is instance2

    def test_get_registry_singleton_returns_scorer_registry_type(self):
        """Test that get_registry_singleton returns a ScorerRegistry instance."""
        instance = ScorerRegistry.get_registry_singleton()
        assert isinstance(instance, ScorerRegistry)

    def test_reset_instance_clears_singleton(self):
        """Test that reset_instance clears the singleton."""
        instance1 = ScorerRegistry.get_registry_singleton()
        ScorerRegistry.reset_instance()
        instance2 = ScorerRegistry.get_registry_singleton()

        assert instance1 is not instance2


class TestScorerRegistryRegisterInstance:
    """Tests for register_instance functionality in ScorerRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ScorerRegistry.reset_instance()
        self.registry = ScorerRegistry.get_registry_singleton()

    def teardown_method(self):
        """Reset the singleton after each test."""
        ScorerRegistry.reset_instance()

    def test_register_instance_with_custom_name(self):
        """Test registering a scorer with a custom name."""
        scorer = MockTrueFalseScorer()
        self.registry.register_instance(scorer, name="custom_scorer")

        assert "custom_scorer" in self.registry
        assert self.registry.get("custom_scorer") is scorer

    def test_register_instance_generates_name_from_class(self):
        """Test that register_instance generates a name from class name when not provided."""
        scorer = MockTrueFalseScorer()
        self.registry.register_instance(scorer)

        # Name should be derived from class name with hash suffix
        names = self.registry.get_names()
        assert len(names) == 1
        assert names[0].startswith("mock_true_false_")

    def test_register_instance_multiple_scorers_unique_names(self):
        """Test registering multiple scorers generates unique names."""
        scorer1 = MockTrueFalseScorer()
        scorer2 = MockFloatScaleScorer()

        self.registry.register_instance(scorer1)
        self.registry.register_instance(scorer2)

        assert len(self.registry) == 2

    def test_register_instance_same_scorer_type_different_hash(self):
        """Test that same scorer class can be registered with different identifiers."""
        scorer1 = MockTrueFalseScorer()
        scorer2 = MockTrueFalseScorer()

        # Register with explicit names since scorers may have same hash
        self.registry.register_instance(scorer1, name="scorer_1")
        self.registry.register_instance(scorer2, name="scorer_2")

        assert len(self.registry) == 2


class TestScorerRegistryGetInstanceByName:
    """Tests for get_instance_by_name functionality in ScorerRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ScorerRegistry.reset_instance()
        self.registry = ScorerRegistry.get_registry_singleton()
        self.scorer = MockTrueFalseScorer()
        self.registry.register_instance(self.scorer, name="test_scorer")

    def teardown_method(self):
        """Reset the singleton after each test."""
        ScorerRegistry.reset_instance()

    def test_get_instance_by_name_returns_scorer(self):
        """Test getting a registered scorer by name."""
        result = self.registry.get_instance_by_name("test_scorer")
        assert result is self.scorer

    def test_get_instance_by_name_nonexistent_returns_none(self):
        """Test that getting a non-existent scorer returns None."""
        result = self.registry.get_instance_by_name("nonexistent")
        assert result is None


class TestScorerRegistryBuildMetadata:
    """Tests for _build_metadata functionality in ScorerRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry for each test."""
        ScorerRegistry.reset_instance()
        self.registry = ScorerRegistry.get_registry_singleton()

    def teardown_method(self):
        """Reset the singleton after each test."""
        ScorerRegistry.reset_instance()

    def test_build_metadata_true_false_scorer(self):
        """Test that metadata correctly identifies TrueFalseScorer type."""
        scorer = MockTrueFalseScorer()
        self.registry.register_instance(scorer, name="tf_scorer")

        metadata = self.registry.list_metadata()
        assert len(metadata) == 1
        assert metadata[0].scorer_type == "true_false"
        assert metadata[0].class_name == "MockTrueFalseScorer"
        # unique_name is auto-computed from class_name, not the registry key
        assert "mock_true_false_scorer" in metadata[0].unique_name

    def test_build_metadata_float_scale_scorer(self):
        """Test that metadata correctly identifies FloatScaleScorer type."""
        scorer = MockFloatScaleScorer()
        self.registry.register_instance(scorer, name="fs_scorer")

        metadata = self.registry.list_metadata()
        assert len(metadata) == 1
        assert metadata[0].scorer_type == "float_scale"
        assert metadata[0].class_name == "MockFloatScaleScorer"

    def test_build_metadata_unknown_scorer_type(self):
        """Test that non-standard scorers get 'unknown' scorer_type."""
        scorer = MockGenericScorer()
        self.registry.register_instance(scorer, name="generic_scorer")

        metadata = self.registry.list_metadata()
        assert len(metadata) == 1
        assert metadata[0].scorer_type == "unknown"

    def test_build_metadata_is_scorer_identifier(self):
        """Test that metadata is the scorer's ScorerIdentifier."""
        scorer = MockTrueFalseScorer()
        self.registry.register_instance(scorer, name="tf_scorer")

        metadata = self.registry.list_metadata()
        assert isinstance(metadata[0], ScorerIdentifier)
        assert metadata[0] == scorer.get_identifier()

    def test_build_metadata_description_from_docstring(self):
        """Test that class_description is derived from the scorer's docstring."""
        scorer = MockTrueFalseScorer()
        self.registry.register_instance(scorer, name="tf_scorer")

        metadata = self.registry.list_metadata()
        # MockTrueFalseScorer has a docstring
        assert "Mock TrueFalseScorer for testing" in metadata[0].class_description


class TestScorerRegistryListMetadataFiltering:
    """Tests for list_metadata filtering in ScorerRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry with multiple scorers."""
        ScorerRegistry.reset_instance()
        self.registry = ScorerRegistry.get_registry_singleton()

        self.tf_scorer1 = MockTrueFalseScorer()
        self.tf_scorer2 = MockTrueFalseScorer()
        self.fs_scorer = MockFloatScaleScorer()

        self.registry.register_instance(self.tf_scorer1, name="tf_scorer_1")
        self.registry.register_instance(self.tf_scorer2, name="tf_scorer_2")
        self.registry.register_instance(self.fs_scorer, name="fs_scorer")

    def teardown_method(self):
        """Reset the singleton after each test."""
        ScorerRegistry.reset_instance()

    def test_list_metadata_filter_by_scorer_type(self):
        """Test filtering metadata by scorer_type."""
        tf_metadata = self.registry.list_metadata(include_filters={"scorer_type": "true_false"})
        assert len(tf_metadata) == 2
        assert all(m.scorer_type == "true_false" for m in tf_metadata)

        fs_metadata = self.registry.list_metadata(include_filters={"scorer_type": "float_scale"})
        assert len(fs_metadata) == 1
        assert fs_metadata[0].scorer_type == "float_scale"

    def test_list_metadata_filter_by_class_name(self):
        """Test filtering metadata by class_name."""
        metadata = self.registry.list_metadata(include_filters={"class_name": "MockTrueFalseScorer"})
        assert len(metadata) == 2
        assert all(m.class_name == "MockTrueFalseScorer" for m in metadata)

    def test_list_metadata_no_filter_returns_all(self):
        """Test that list_metadata without filters returns all items."""
        metadata = self.registry.list_metadata()
        assert len(metadata) == 3

    def test_list_metadata_exclude_by_scorer_type(self):
        """Test excluding metadata by scorer_type."""
        metadata = self.registry.list_metadata(exclude_filters={"scorer_type": "true_false"})
        assert len(metadata) == 1
        assert metadata[0].scorer_type == "float_scale"

    def test_list_metadata_combined_include_and_exclude(self):
        """Test combined include and exclude filters."""
        # Filter to include true_false scorers, exclude float_scale
        # This tests that both filters work together
        metadata = self.registry.list_metadata(
            include_filters={"scorer_type": "true_false"}, exclude_filters={"scorer_type": "float_scale"}
        )
        # Should return both true_false scorers (exclude filter doesn't match any of them)
        assert len(metadata) == 2
        assert all(m.scorer_type == "true_false" for m in metadata)

        # Test excluding by class_name
        metadata = self.registry.list_metadata(
            include_filters={"scorer_type": "true_false"}, exclude_filters={"class_name": "MockTrueFalseScorer"}
        )
        # Should return 0 since all true_false scorers are MockTrueFalseScorer
        assert len(metadata) == 0


class TestScorerRegistryInheritedMethods:
    """Tests for inherited methods from BaseInstanceRegistry."""

    def setup_method(self):
        """Reset and get a fresh registry."""
        ScorerRegistry.reset_instance()
        self.registry = ScorerRegistry.get_registry_singleton()
        self.scorer = MockTrueFalseScorer()
        self.registry.register_instance(self.scorer, name="test_scorer")

    def teardown_method(self):
        """Reset the singleton after each test."""
        ScorerRegistry.reset_instance()

    def test_contains_registered_name(self):
        """Test __contains__ for registered name."""
        assert "test_scorer" in self.registry

    def test_contains_unregistered_name(self):
        """Test __contains__ for unregistered name."""
        assert "unknown_scorer" not in self.registry

    def test_len_returns_count(self):
        """Test __len__ returns correct count."""
        assert len(self.registry) == 1

    def test_iter_yields_names(self):
        """Test __iter__ yields registered names."""
        names = list(self.registry)
        assert "test_scorer" in names

    def test_get_names_returns_sorted_list(self):
        """Test get_names returns sorted list of names."""
        self.registry.register_instance(MockFloatScaleScorer(), name="alpha_scorer")
        self.registry.register_instance(MockFloatScaleScorer(), name="zeta_scorer")

        names = self.registry.get_names()
        assert names == ["alpha_scorer", "test_scorer", "zeta_scorer"]


class TestScorerIdentifierType:
    """Tests for ScorerIdentifier scorer_type field."""

    def test_scorer_identifier_has_scorer_type_field(self):
        """Test that ScorerIdentifier includes scorer_type field."""
        identifier = ScorerIdentifier(
            class_name="TestScorer",
            class_module="test.module",
            class_description="A test scorer",
            identifier_type="instance",
            scorer_type="true_false",
        )

        assert identifier.identifier_type == "instance"
        assert identifier.class_name == "TestScorer"
        assert identifier.class_module == "test.module"
        assert identifier.class_description == "A test scorer"
        assert identifier.scorer_type == "true_false"
        # unique_name is auto-computed
        assert identifier.unique_name is not None
