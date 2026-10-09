# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Tests for the scenario preset model."""

import pytest

from pyrit.models.catalog.scenario_preset import ScenarioPreset
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