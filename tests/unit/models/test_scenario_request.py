# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for RunScenarioRequest dataset-filter validation and the exposed filter allow-list."""

import pytest
from pydantic import ValidationError

from pyrit.models.catalog.scenario import DATASET_FILTERS, RunScenarioRequest, ScenarioRunSizeEstimateRequest


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
@pytest.mark.parametrize(
    ("limit", "expected"),
    [
        (None, "default"),
        ("", "default"),
        ("  ", "default"),
        ("default", "default"),
        (" DEFAULT ", "default"),
        ("all", "all"),
        (" ALL ", "all"),
        (7, 7),
        ("7", 7),
    ],
)
def test_dataset_limit_values(
    *, model: type[RunScenarioRequest] | type[ScenarioRunSizeEstimateRequest], limit: object, expected: object
) -> None:
    request = model.model_validate({"scenario_name": "s", "target_name": "t", "max_dataset_size": limit})
    assert request.max_dataset_size == expected
    assert request.model_dump(mode="json")["max_dataset_size"] == expected


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
@pytest.mark.parametrize("limit", [0, -1, True, False, 1.0, 1.5, "unlimited", [], {}])
def test_dataset_limit_rejects_invalid_values(
    *, model: type[RunScenarioRequest] | type[ScenarioRunSizeEstimateRequest], limit: object
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"scenario_name": "s", "target_name": "t", "max_dataset_size": limit})


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
def test_omitted_limit_uses_explicit_default(
    model: type[RunScenarioRequest] | type[ScenarioRunSizeEstimateRequest],
) -> None:
    request = model.model_validate({"scenario_name": "s", "target_name": "t"})
    assert request.max_dataset_size == "default"
    assert request.model_dump(mode="json")["max_dataset_size"] == "default"


def _make_request(*, dataset_filters: dict[str, list[str]] | None) -> RunScenarioRequest:
    return RunScenarioRequest(scenario_name="s", target_name="t", dataset_filters=dataset_filters)


class TestRunScenarioRequestDatasetFilters:
    """The request model validates dataset-filter keys server-side (covers the GUI too)."""

    def test_none_is_allowed(self) -> None:
        assert _make_request(dataset_filters=None).dataset_filters is None

    def test_known_keys_pass_through(self) -> None:
        request = _make_request(dataset_filters={"harm_categories": ["cyber"], "data_types": ["text"]})
        assert request.dataset_filters == {"harm_categories": ["cyber"], "data_types": ["text"]}

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(ValidationError, match="Unknown dataset filter 'bogus'"):
            _make_request(dataset_filters={"bogus": ["x"]})

    def test_unexposed_get_seeds_kwarg_is_rejected(self) -> None:
        # ``authors`` is a real get_seeds kwarg but is intentionally NOT exposed as a filter.
        with pytest.raises(ValidationError, match="Unknown dataset filter 'authors'"):
            _make_request(dataset_filters={"authors": ["jones"]})

    def test_scalar_value_is_rejected(self) -> None:
        # Values must be lists; the CLI coerces raw strings before building the request.
        with pytest.raises(ValidationError):
            _make_request(dataset_filters={"harm_categories": "cyber"})  # type: ignore[dict-item]


class TestExposedDatasetFilters:
    """The exposed filter set is intentional and stays in contract with ``get_seeds``."""

    def test_exposed_filters_are_frozen(self) -> None:
        # Adding/removing a filter must be a deliberate edit to this expected set.
        assert {"harm_categories", "data_types"} == DATASET_FILTERS

    def test_every_filter_is_a_sequence_get_seeds_param(self) -> None:
        # Each exposed key must be a real get_seeds parameter AND list-valued.
        import typing
        from collections.abc import Sequence

        from pyrit.memory.memory_interface import MemoryInterface

        hints = typing.get_type_hints(MemoryInterface.get_seeds_async)

        def _allows_sequence(annotation: object) -> bool:
            for candidate in (annotation, *typing.get_args(annotation)):
                origin = typing.get_origin(candidate) or candidate
                if isinstance(origin, type) and origin is not str and issubclass(origin, Sequence):
                    return True
            return False

        for name in DATASET_FILTERS:
            assert name in hints, f"'{name}' is not a MemoryInterface.get_seeds_async parameter"
            assert _allows_sequence(hints[name]), f"'{name}' must be a Sequence-typed get_seeds parameter"
