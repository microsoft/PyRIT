# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for pyrit.models.identifiers.evaluation_markers."""

from pyrit.models.identifiers import (
    AtomicAttackIdentifier,
    AttackIdentifier,
    AttackTechniqueIdentifier,
    ComponentIdentifier,
    ConverterIdentifier,
    EvalMarker,
    Evaluate,
    Exclude,
    Include,
    ScorerIdentifier,
    SeedIdentifier,
    TargetIdentifier,
    Unwrap,
)


class TestEvaluateNamespace:
    """The ``Evaluate`` namespace exposes the marker types."""

    def test_namespace_aliases(self):
        assert Evaluate.Include is Include
        assert Evaluate.Exclude is Exclude
        assert Evaluate.Unwrap is Unwrap

    def test_markers_are_eval_markers(self):
        assert isinstance(Include(), EvalMarker)
        assert isinstance(Exclude(), EvalMarker)
        assert isinstance(Unwrap(), EvalMarker)

    def test_include_defaults_and_fields(self):
        plain = Include()
        assert plain.fallback is None
        assert plain.only_params is None

        configured = Include(fallback="model_name", only_params=frozenset({"temperature"}))
        assert configured.fallback == "model_name"
        assert configured.only_params == frozenset({"temperature"})

    def test_markers_are_frozen(self):
        marker = Include()
        try:
            marker.fallback = "x"  # type: ignore[misc]
        except Exception as exc:  # FrozenInstanceError
            assert "FrozenInstanceError" in type(exc).__name__ or "frozen" in str(exc).lower()
        else:
            raise AssertionError("Expected the frozen marker to reject mutation")


class TestMarkersAttachedToFields:
    """Markers declared via ``Annotated`` are exposed on the model field metadata."""

    @staticmethod
    def _marker(model_cls, field_name):
        for meta in model_cls.model_fields[field_name].metadata:
            if isinstance(meta, EvalMarker):
                return meta
        return None

    def test_target_field_markers(self):
        assert isinstance(self._marker(TargetIdentifier, "endpoint"), Exclude)
        assert isinstance(self._marker(TargetIdentifier, "max_requests_per_minute"), Exclude)
        assert isinstance(self._marker(TargetIdentifier, "temperature"), Include)
        assert isinstance(self._marker(TargetIdentifier, "targets"), Unwrap)

        um_marker = self._marker(TargetIdentifier, "underlying_model_name")
        assert isinstance(um_marker, Include)
        assert um_marker.fallback == "model_name"

    def test_attack_objective_target_only_params(self):
        marker = self._marker(AttackIdentifier, "objective_target")
        assert isinstance(marker, Include)
        assert marker.only_params == frozenset({"temperature"})

        assert isinstance(self._marker(AttackIdentifier, "objective_scorer"), Exclude)


def _field_marker(model_cls, field_name):
    """Return the ``EvalMarker`` attached to a field, or ``None``."""
    for meta in model_cls.model_fields[field_name].metadata:
        if isinstance(meta, EvalMarker):
            return meta
    return None


def _production_identifier_subclasses():
    """All ``ComponentIdentifier`` subclasses defined in ``pyrit`` (excludes test stubs)."""
    seen: set[type] = set()
    stack = list(ComponentIdentifier.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
    return sorted(
        (c for c in seen if c.__module__.startswith("pyrit.")),
        key=lambda c: c.__name__,
    )


class TestEveryPromotedFieldIsMarked:
    """Every promoted field on a production identifier must declare an ``Evaluate.*`` marker."""

    def test_discovery_finds_known_identifiers(self):
        discovered = set(_production_identifier_subclasses())
        expected = {
            AtomicAttackIdentifier,
            AttackIdentifier,
            AttackTechniqueIdentifier,
            ConverterIdentifier,
            ScorerIdentifier,
            SeedIdentifier,
            TargetIdentifier,
        }
        assert expected <= discovered

    def test_all_promoted_fields_have_eval_marker(self):
        violations: list[str] = []
        for cls in _production_identifier_subclasses():
            promoted = (*cls._promoted_param_fields(), *cls._promoted_child_fields())
            violations.extend(
                f"{cls.__name__}.{field_name}" for field_name in promoted if _field_marker(cls, field_name) is None
            )
        assert not violations, (
            "Every promoted field on a ComponentIdentifier subclass must carry an "
            "Evaluate.* marker (use Evaluate.Include() for the default). Unmarked: " + ", ".join(sorted(violations))
        )
