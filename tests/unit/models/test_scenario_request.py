# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for RunScenarioRequest dataset-filter validation and the exposed filter allow-list."""

import pytest
from pydantic import ValidationError

from pyrit.models import DatasetSourceInput, ScenarioDatasetRequest
from pyrit.models.catalog.scenario import DATASET_FILTERS, RunScenarioRequest, ScenarioRunSizeEstimateRequest


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
@pytest.mark.parametrize("policy", ["never", "if_missing", "always"])
def test_new_dataset_request_transport_round_trip(*, model: type, policy: str) -> None:
    data = {
        "scenario_name": "s",
        "target_name": "t",
        "datasets": {
            "sources": [{"name": "harmbench"}, {"name": "custom", "max_size": "all", "fetch": policy}],
            "max_per_dataset": 4,
            "max_total": 12,
            "fetch": policy,
            "filters": {"data_types": ["text"]},
        },
    }
    request = model.model_validate(data)
    assert request.dataset_request == request.datasets
    assert model.model_validate(request.model_dump(mode="json", exclude_none=True)) == request
    assert model.model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
@pytest.mark.parametrize(
    "legacy",
    [{"dataset_names": ["a"]}, {"max_dataset_size": 3}, {"dataset_filters": {"data_types": ["text"]}}],
)
def test_new_dataset_request_rejects_deprecated_values(*, model: type, legacy: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="not both"):
        model.model_validate({"scenario_name": "s", "target_name": "t", "datasets": {"max_total": 2}, **legacy})


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
def test_new_dataset_request_accepts_deprecated_defaults(model: type) -> None:
    request = model.model_validate(
        {
            "scenario_name": "s",
            "target_name": "t",
            "datasets": {"max_total": 2},
            "dataset_names": None,
            "max_dataset_size": "default",
            "dataset_filters": None,
        }
    )
    assert request.dataset_request == ScenarioDatasetRequest(max_total=2)


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
def test_deprecated_dataset_fields_convert_to_one_request(model: type) -> None:
    request = model.model_validate(
        {
            "scenario_name": "s",
            "target_name": "t",
            "dataset_names": ["a", "b"],
            "max_dataset_size": 3,
            "dataset_filters": {"data_types": ["text"]},
        }
    )
    assert request.datasets is None
    assert request.dataset_request == ScenarioDatasetRequest(
        sources=[DatasetSourceInput(name="a"), DatasetSourceInput(name="b")],
        max_total=3,
        filters={"data_types": ["text"]},
    )
    assert model.model_validate({"scenario_name": "s", "target_name": "t"}).dataset_request is None


@pytest.mark.parametrize("model", [RunScenarioRequest, ScenarioRunSizeEstimateRequest])
def test_invalid_deprecated_dataset_names_fail_validation(model: type) -> None:
    with pytest.raises(ValidationError, match="Duplicate"):
        model.model_validate({"scenario_name": "s", "target_name": "t", "dataset_names": ["a", "a"]})


@pytest.mark.parametrize(
    "data",
    [
        {"sources": []},
        {"sources": [{"name": "a"}, {"name": "a"}]},
        {"sources": [{"name": " "}]},
        {"sources": [{"name": "a", "provider": {}}]},
        {"max_total": True},
        {"max_per_dataset": 0},
        {"max_total": 1.5},
        {"fetch": "sometimes"},
        {"filters": {"authors": ["me"]}},
        {"filters": {"data_types": "text"}},
        {"unexpected": True},
    ],
)
def test_shared_dataset_request_rejects_invalid_inputs(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScenarioDatasetRequest.model_validate(data)


@pytest.mark.parametrize("value", [None, "", "default"])
def test_shared_dataset_request_default_semantics(value: object) -> None:
    request = ScenarioDatasetRequest.model_validate({"max_total": value, "max_per_dataset": value, "fetch": value})
    assert request.max_total == request.max_per_dataset == "default"
    assert request.fetch is None


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
