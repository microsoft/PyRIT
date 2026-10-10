# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for RunScenarioRequest dataset-filter validation and the exposed filter allow-list."""

import pytest
from pydantic import ValidationError

from pyrit.models.catalog.scenario import DATASET_FILTERS, RunScenarioRequest, ScenarioRunSizeEstimateRequest


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


@pytest.mark.parametrize(
    "overrides",
    [
        {"scenario_name": "s" * 257},
        {"scenario_result_id": "r" * 257},
        {"techniques": ["technique"] * 101},
        {"initializers": ["i" * 257]},
        {"dataset_filters": {"harm_categories": ["cyber"] * 101}},
        {"labels": {f"key{i}": "value" for i in range(101)}},
        {"labels": {"k" * 129: "value"}},
        {"labels": {"key": "v" * 1_025}},
        {"scenario_params": {f"param{i}": 1 for i in range(101)}},
        {"initializer_args": {"target": {f"arg{i}": 1 for i in range(101)}}},
    ],
)
def test_run_request_rejects_values_over_limits(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RunScenarioRequest.model_validate({"scenario_name": "s", "target_name": "t", **overrides})


def test_run_request_accepts_values_at_limits() -> None:
    request = RunScenarioRequest(
        scenario_name="s" * 256,
        target_name="t",
        techniques=["technique"] * 100,
        labels={"k" * 128: "v" * 1_024},
    )

    assert request.techniques is not None
    assert len(request.techniques) == 100


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
def test_requests_accept_technique_with_converter_modifiers(model: type) -> None:
    technique = "prompt_sending" + "".join(f":converter.{'c' * 64}" for _ in range(4))

    request = model.model_validate({"scenario_name": "s", "target_name": "t", "techniques": [technique]})

    assert request.techniques == [technique]


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
def test_requests_reject_oversized_technique(model: type) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"scenario_name": "s", "target_name": "t", "techniques": ["t" * 4_097]})


def test_estimate_request_rejects_too_many_dataset_names() -> None:
    with pytest.raises(ValidationError):
        ScenarioRunSizeEstimateRequest(dataset_names=["dataset"] * 101)


def test_run_request_bounds_dataset_filter_keys() -> None:
    with pytest.raises(ValidationError) as error:
        RunScenarioRequest(scenario_name="s", target_name="t", dataset_filters={"k" * 257: ["x"]})

    assert error.value.errors()[0]["type"] == "string_too_long"
