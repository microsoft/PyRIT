# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for pyrit.models.identifiers.evaluation_markers."""

from pyrit.models.identifiers import (
    AttackIdentifier,
    EvalMarker,
    Evaluate,
    Exclude,
    Include,
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
