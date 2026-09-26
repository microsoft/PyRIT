# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for pyrit.scenario.core.technique_resolution."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pyrit.prompt_target import PromptTarget
from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
from pyrit.scenario.core.scenario_context import ScenarioContext
from pyrit.scenario.core.technique_resolution import (
    TechniqueResolutionError,
    resolve_technique_factories,
    resolve_technique_factories_for_techniques,
)


def _mock_factory(*, name: str) -> MagicMock:
    factory = MagicMock(spec=AttackTechniqueFactory)
    factory.name = name
    return factory


def _technique(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value)


def _context(*, techniques) -> ScenarioContext:
    return ScenarioContext(
        objective_target=MagicMock(spec=PromptTarget),
        scenario_techniques=techniques,
        dataset_config=MagicMock(),
        memory_labels={"op": "unit"},
        include_baseline=False,
        seed_groups_by_dataset={},
    )


def _patch_registry(factories: dict):
    registry = MagicMock()
    registry.get_factories_or_raise.return_value = factories
    return patch(
        "pyrit.registry.components.attack_technique_registry.AttackTechniqueRegistry.get_registry_singleton",
        return_value=registry,
    )


@pytest.mark.usefixtures("patch_central_database")
class TestTechniqueResolutionError:
    """TechniqueResolutionError is a ValueError subclass."""

    def test_subclasses_value_error(self):
        assert issubclass(TechniqueResolutionError, ValueError)
        err = TechniqueResolutionError("missing factory")
        assert isinstance(err, ValueError)


@pytest.mark.usefixtures("patch_central_database")
class TestResolveTechniqueFactories:
    """resolve_technique_factories resolves context scenario techniques."""

    def test_keeps_only_selected_in_order(self):
        factories = {
            "alpha": _mock_factory(name="alpha"),
            "beta": _mock_factory(name="beta"),
            "gamma": _mock_factory(name="gamma"),
        }
        context = _context(techniques=[_technique("beta"), _technique("alpha")])
        with _patch_registry(factories):
            resolved = resolve_technique_factories(context=context)
        assert list(resolved.keys()) == ["beta", "alpha"]

    def test_raises_when_any_selected_technique_is_missing(self):
        factories = {"alpha": _mock_factory(name="alpha")}
        context = _context(techniques=[_technique("alpha"), _technique("missing")])
        with _patch_registry(factories), pytest.raises(TechniqueResolutionError, match="missing"):
            resolve_technique_factories(context=context)

    def test_raises_when_all_selected_techniques_missing(self):
        factories = {"alpha": _mock_factory(name="alpha")}
        context = _context(techniques=[_technique("missing_a"), _technique("missing_b")])
        with _patch_registry(factories), pytest.raises(TechniqueResolutionError, match="missing_a"):
            resolve_technique_factories(context=context)

    def test_empty_selection_resolves_without_error(self):
        context = _context(techniques=[])
        with _patch_registry({}):
            assert resolve_technique_factories(context=context) == {}

    def test_error_lists_each_missing_technique_once_in_selection_order(self):
        factories = {"alpha": _mock_factory(name="alpha")}
        context = _context(
            techniques=[
                _technique("missing_a"),
                _technique("alpha"),
                _technique("missing_b"),
                _technique("missing_a"),
            ]
        )
        with _patch_registry(factories), pytest.raises(TechniqueResolutionError) as exc_info:
            resolve_technique_factories(context=context)
        message = str(exc_info.value)
        assert message.index("missing_a") < message.index("missing_b")
        assert message.count("missing_a") == 1
        assert "Register the techniques (or pass them via extra_factories)" in message

    def test_extra_factories_merged_and_override_registry(self):
        registry_factories = {"alpha": _mock_factory(name="alpha")}
        local_alpha = _mock_factory(name="alpha")
        local_only = _mock_factory(name="local")
        context = _context(techniques=[_technique("alpha"), _technique("local")])
        with _patch_registry(registry_factories):
            resolved = resolve_technique_factories(
                context=context,
                extra_factories={"alpha": local_alpha, "local": local_only},
            )
        assert list(resolved.keys()) == ["alpha", "local"]
        assert resolved["alpha"] is local_alpha
        assert resolved["local"] is local_only


@pytest.mark.usefixtures("patch_central_database")
class TestResolveTechniqueFactoriesForTechniques:
    """resolve_technique_factories_for_techniques accepts raw sequences of ScenarioTechnique."""

    def test_resolves_raw_sequence_preserving_order(self):
        factories = {
            "tech1": _mock_factory(name="tech1"),
            "tech2": _mock_factory(name="tech2"),
        }
        techniques = [_technique("tech2"), _technique("tech1")]
        with _patch_registry(factories):
            resolved = resolve_technique_factories_for_techniques(scenario_techniques=techniques)
        assert list(resolved.keys()) == ["tech2", "tech1"]

    def test_supports_extra_factories_without_context(self):
        local_factory = _mock_factory(name="custom")
        with _patch_registry({}):
            resolved = resolve_technique_factories_for_techniques(
                scenario_techniques=[_technique("custom")],
                extra_factories={"custom": local_factory},
            )
        assert resolved["custom"] is local_factory
