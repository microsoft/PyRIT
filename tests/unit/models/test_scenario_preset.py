# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario preset model."""

import pytest

from pyrit.models.catalog.scenario_preset import PRESET_SCHEMA_VERSION, ScenarioPreset
from pyrit.models.request_limits import MAX_IDENTIFIER_LENGTH, MAX_ITEMS


def test_init_defaults_every_optional_field_to_none() -> None:
    """Test that an unset preset field is None rather than a concrete default."""
    preset = ScenarioPreset(name="nightly", scenario_name="foundry.red_team_agent")

    assert preset.techniques is None
    assert preset.dataset_names is None
    assert preset.max_dataset_size is None
    assert preset.dataset_filters is None
    assert preset.include_baseline is None
    assert preset.scenario_params is None
    assert preset.description is None


def test_preset_carries_no_storage_version() -> None:
    """Test that storage metadata stays out of the model it describes."""
    assert "version" not in ScenarioPreset.model_fields


def test_schema_version_defaults_so_a_document_without_it_still_reads() -> None:
    """Test that a document written before the field existed is read as the first shape."""
    assert ScenarioPreset(name="nightly", scenario_name="foundry.red_team_agent").schema_version == 1


def test_init_rejects_a_document_shape_this_build_cannot_read() -> None:
    """Test that a future document fails loudly instead of being reinterpreted as this shape."""
    with pytest.raises(ValueError, match="newer than this build reads"):
        ScenarioPreset(
            name="nightly",
            scenario_name="foundry.red_team_agent",
            schema_version=PRESET_SCHEMA_VERSION + 1,
        )


def test_init_rejects_unknown_field() -> None:
    """Test that a misspelled key fails loudly instead of silently falling back to the default."""
    with pytest.raises(ValueError, match="techinques"):
        ScenarioPreset(
            name="nightly",
            scenario_name="foundry.red_team_agent",
            techinques=["crescendo"],  # type: ignore[call-arg]
        )


@pytest.mark.parametrize("name", ["Nightly", "nightly-scan", "1nightly", "", "a" * 65])
def test_init_rejects_invalid_registry_names(name: str) -> None:
    """Test that a preset name must be a legal registry name."""
    with pytest.raises(ValueError, match="Invalid registry name"):
        ScenarioPreset(name=name, scenario_name="foundry.red_team_agent")


def test_init_rejects_unknown_dataset_filter() -> None:
    """Test that dataset filters are validated against the shared allow-list."""
    with pytest.raises(ValueError, match="Unknown dataset filter"):
        ScenarioPreset(
            name="nightly",
            scenario_name="foundry.red_team_agent",
            dataset_filters={"not_a_field": ["x"]},
        )


def test_init_accepts_known_dataset_filters() -> None:
    """Test that allow-listed dataset filters are preserved."""
    preset = ScenarioPreset(
        name="nightly",
        scenario_name="foundry.red_team_agent",
        dataset_filters={"harm_categories": ["violence"], "data_types": ["text"]},
    )

    assert preset.dataset_filters == {"harm_categories": ["violence"], "data_types": ["text"]}


def test_init_rejects_zero_max_dataset_size() -> None:
    """Test that a dataset cap must select at least one item."""
    with pytest.raises(ValueError):
        ScenarioPreset(name="nightly", scenario_name="foundry.red_team_agent", max_dataset_size=0)


def test_init_rejects_empty_scenario_name() -> None:
    """Test that a preset must name the scenario it configures."""
    with pytest.raises(ValueError):
        ScenarioPreset(name="nightly", scenario_name="")


def test_include_baseline_distinguishes_unset_from_false() -> None:
    """Test the tri-state contract that an unset override is not a disabled override."""
    unset = ScenarioPreset(name="nightly", scenario_name="foundry.red_team_agent")
    disabled = ScenarioPreset(name="nightly", scenario_name="foundry.red_team_agent", include_baseline=False)

    assert unset.include_baseline is None
    assert disabled.include_baseline is False
    assert unset.include_baseline != disabled.include_baseline


def test_init_rejects_a_preset_too_large_to_launch() -> None:
    """Test that the stored preset enforces the same request limits the launch request does."""
    with pytest.raises(ValueError):
        ScenarioPreset(
            name="nightly",
            scenario_name="foundry.red_team_agent",
            techniques=[f"technique_{index}" for index in range(MAX_ITEMS + 1)],
        )

    with pytest.raises(ValueError):
        ScenarioPreset(
            name="nightly",
            scenario_name="foundry.red_team_agent",
            dataset_names=["x" * (MAX_IDENTIFIER_LENGTH + 1)],
        )

    with pytest.raises(ValueError):
        ScenarioPreset(
            name="nightly",
            scenario_name="foundry.red_team_agent",
            scenario_params={f"param_{index}": index for index in range(MAX_ITEMS + 1)},
        )


def test_identity_covers_configuration_and_ignores_description() -> None:
    """Test that identity reflects what a preset runs, not how it is labelled."""
    preset = ScenarioPreset(
        name="nightly",
        scenario_name="foundry.red_team_agent",
        description="Nightly smoke",
        author="tester",
        techniques=["crescendo"],
    )

    relabelled = ScenarioPreset(
        name="weekly",
        scenario_name="foundry.red_team_agent",
        description="Weekly smoke",
        author="someone else",
        techniques=["crescendo"],
    )
    reconfigured = ScenarioPreset(
        name="nightly",
        scenario_name="foundry.red_team_agent",
        description="Nightly smoke",
        author="tester",
        techniques=["flip"],
    )

    assert preset.get_identifier().unique_name == relabelled.get_identifier().unique_name
    assert preset.get_identifier().unique_name != reconfigured.get_identifier().unique_name


def test_identity_is_rebuilt_so_a_modified_copy_does_not_report_the_original() -> None:
    """Test that a copy edited through ``model_copy`` is not given the original's identity."""
    preset = ScenarioPreset(name="nightly", scenario_name="foundry.red_team_agent", techniques=["crescendo"])
    original = preset.get_identifier().unique_name

    edited = preset.model_copy(update={"techniques": ["flip"]})

    assert edited.get_identifier().unique_name != original
    assert preset.get_identifier().unique_name == original


def test_identity_params_are_json_safe_for_nested_configuration() -> None:
    """Test that nested configuration survives into the identifier the instance registry reads."""
    preset = ScenarioPreset(
        name="nightly",
        scenario_name="foundry.red_team_agent",
        dataset_filters={"harm_categories": ["violence"]},
        scenario_params={"max_turns": 3},
    )

    params = preset.get_identifier().params

    assert params["dataset_filters"] == {"harm_categories": ["violence"]}
    assert params["scenario_params"] == {"max_turns": 3}
    assert "name" not in params
