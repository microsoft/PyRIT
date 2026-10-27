# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for ``SourceRegistry`` (buildable catalog + instance container) and for
scorers resolving their ``source`` argument through it.
"""

from pathlib import Path

import pytest

from pyrit.models.identifiers import ScorerEvaluationIdentifier, ScorerIdentifier
from pyrit.models.parameter import ComponentType
from pyrit.registry import Registry
from pyrit.registry.components import ScorerRegistry, SourceMetadata, SourceRegistry
from pyrit.score import FileWriteScorer, OtelToolCallScorer
from pyrit.score.observation import (
    ConversationSource,
    InMemoryTraceClient,
    LocalFileSurfaceSource,
    ObservationSource,
    OtelTraceSource,
)
from pyrit.setup.initialization import reset_setup_registries


@pytest.fixture
def registry():
    """Provide a fresh ``SourceRegistry`` singleton, reset around each test."""
    SourceRegistry.reset_registry_singleton()
    instance = SourceRegistry.get_registry_singleton()
    yield instance
    SourceRegistry.reset_registry_singleton()


@pytest.fixture
def scorer_registry():
    """Provide a fresh ``ScorerRegistry`` singleton, reset around each test."""
    ScorerRegistry.reset_registry_singleton()
    instance = ScorerRegistry.get_registry_singleton()
    yield instance
    ScorerRegistry.reset_registry_singleton()


class TestSourceRegistrySingleton:
    """Tests for the singleton pattern in SourceRegistry."""

    def setup_method(self):
        SourceRegistry.reset_registry_singleton()

    def teardown_method(self):
        SourceRegistry.reset_registry_singleton()

    def test_get_registry_singleton_returns_same_instance(self):
        assert SourceRegistry.get_registry_singleton() is SourceRegistry.get_registry_singleton()

    def test_reset_setup_registries_resets_source_registry(self):
        instance = SourceRegistry.get_registry_singleton()
        reset_setup_registries()
        assert SourceRegistry.get_registry_singleton() is not instance


class TestDiscovery:
    """Tests for source class discovery."""

    def test_discovers_exported_sources(self, registry: SourceRegistry):
        names = registry.get_class_names()
        assert {"ConversationSource", "LocalFileSurfaceSource", "OtelTraceSource"} <= set(names)

    def test_skips_protocols_and_non_source_exports(self, registry: SourceRegistry):
        names = set(registry.get_class_names())
        assert names.isdisjoint(
            {"ObservationSource", "SurfaceLifecycleSource", "AttemptObserver", "TraceClient", "InMemoryTraceClient"}
        )

    def test_every_discovered_class_satisfies_protocol(self, registry: SourceRegistry):
        for name in registry.get_class_names():
            assert issubclass(registry.get_class(name), ObservationSource)

    def test_metadata_carries_constructor_contract(self, registry: SourceRegistry):
        metadata = registry.get_registered_class_metadata("LocalFileSurfaceSource")
        assert isinstance(metadata, SourceMetadata)
        assert [p.name for p in metadata.parameters][:2] == ["root", "max_files"]
        assert all(p.reference is None for p in metadata.parameters)


@pytest.mark.usefixtures("patch_central_database")
class TestInstances:
    """Tests for building and holding configured sources."""

    def test_create_instance_builds_configured_source(self, registry: SourceRegistry, tmp_path: Path):
        source = registry.create_instance("LocalFileSurfaceSource", root=str(tmp_path), max_files=5)
        assert isinstance(source, LocalFileSurfaceSource)
        assert source.get_identifier().params["max_files"] == 5

    def test_create_named_instance_stores_source(self, registry: SourceRegistry, tmp_path: Path):
        source = registry.create_named_instance(
            name="workspace", type_name="LocalFileSurfaceSource", params={"root": str(tmp_path)}
        )
        assert registry.instances.get("workspace") is source

    def test_register_accepts_any_protocol_implementation(self, registry: SourceRegistry):
        source = OtelTraceSource(trace_client=InMemoryTraceClient())
        registry.instances.register(source, name="traces")
        assert registry.instances.get("traces") is source

    def test_register_rejects_non_source(self, registry: SourceRegistry):
        with pytest.raises(TypeError):
            registry.instances.register(InMemoryTraceClient(), name="not_a_source")


@pytest.mark.usefixtures("patch_central_database")
class TestScorerSourceReference:
    """Tests for scorers resolving ``source`` by name from the SourceRegistry."""

    def test_source_component_type_is_resolvable(self):
        assert Registry._is_component_type_resolvable(ComponentType.SOURCE)

    @pytest.mark.parametrize("scorer_name", ["FileWriteScorer", "OtelToolCallScorer"])
    def test_scorer_source_parameter_is_source_reference(self, scorer_registry: ScorerRegistry, scorer_name: str):
        metadata = scorer_registry.get_registered_class_metadata(scorer_name)
        source_parameter = next(p for p in metadata.parameters if p.name == "source")
        assert source_parameter.is_reference_to(ComponentType.SOURCE)
        assert source_parameter.reference_type == "source"

    def test_build_scorer_resolves_source_by_name(
        self, registry: SourceRegistry, scorer_registry: ScorerRegistry, tmp_path: Path
    ):
        source = registry.create_named_instance(
            name="workspace", type_name="LocalFileSurfaceSource", params={"root": str(tmp_path)}
        )

        scorer = scorer_registry.create_instance("FileWriteScorer", source="workspace")

        assert isinstance(scorer, FileWriteScorer)
        assert scorer.get_identifier().children["source"].hash == source.get_identifier().hash

    def test_build_scorer_unknown_source_name_raises(self, registry: SourceRegistry, scorer_registry: ScorerRegistry):
        with pytest.raises(ValueError, match="not found"):
            scorer_registry.create_instance("OtelToolCallScorer", source="missing")

    def test_build_scorer_passes_live_source_through(self, registry: SourceRegistry, scorer_registry: ScorerRegistry):
        source = OtelTraceSource(trace_client=InMemoryTraceClient())
        scorer = scorer_registry.create_instance("OtelToolCallScorer", source=source)
        assert isinstance(scorer, OtelToolCallScorer)
        assert scorer.get_identifier().children["source"].hash == source.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
class TestScorerIdentifierSourceReference:
    """Declaring ``source`` as a reference must not change any stored identity."""

    def test_source_is_declared_reference_not_promoted_field(self):
        assert ScorerIdentifier.get_reference_component_types()["source"] is ComponentType.SOURCE
        assert "source" not in ScorerIdentifier.promoted_child_field_names()

    @pytest.mark.parametrize(
        "make_scorer",
        [
            lambda: FileWriteScorer(source=LocalFileSurfaceSource(root="workspace", max_files=7)),
            lambda: OtelToolCallScorer(source=OtelTraceSource(trace_client=InMemoryTraceClient())),
        ],
    )
    def test_typed_projection_keeps_source_child_and_hashes(self, make_scorer):
        identifier = make_scorer().get_identifier()
        typed = ScorerIdentifier.model_validate(identifier.model_dump())

        assert typed.children["source"].hash == identifier.children["source"].hash
        assert typed.hash == identifier.hash
        assert ScorerEvaluationIdentifier(typed).eval_hash == ScorerEvaluationIdentifier(identifier).eval_hash

    def test_conversation_scorer_default_source_is_unchanged(self):
        child = ConversationSource().get_identifier()
        typed = ScorerIdentifier(class_name="SomeScorer", class_module="tests", children={"source": child})
        assert typed.children["source"].hash == child.hash
