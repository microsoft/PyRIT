# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Adversarial target resolution validates without changing execution scopes."""

from unittest.mock import MagicMock, patch

import pytest

from pyrit.backend.services.scenario_configuration_resolver import ScenarioConfigurationResolver
from pyrit.prompt_target.common.target_capabilities import TargetCapabilities
from pyrit.registry import TargetRegistry
from pyrit.scenario.core import (
    get_default_adversarial_target,
    override_default_adversarial_target,
    scenario_target_defaults,
)
from pyrit.scenario.scenarios.garak.api_key import ApiKey
from unit.mocks import MockPromptTarget


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(("limit", "expected"), [(None, 20), (7, 7)])
def test_dataset_name_override_preserves_default_limit(*, limit: int | None, expected: int) -> None:
    resolved = ScenarioConfigurationResolver.resolve_configuration(
        scenario_name="garak.api_key",
        scenario_class=ApiKey,
        dataset_names=ApiKey.required_datasets(),
        max_dataset_size=limit,
    )
    assert resolved["dataset_config"].max_dataset_size == expected


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("selection", [None, "selected"])
def test_resolve_adversarial_target_does_not_mutate_scope(selection: str | None) -> None:
    outer, selected = MockPromptTarget(), MockPromptTarget()
    registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
    registry.instances.get.return_value = selected
    with (
        override_default_adversarial_target(outer),
        patch.object(TargetRegistry, "get_registry_singleton", return_value=registry),
        patch.object(
            scenario_target_defaults,
            "_adversarial_target_override",
            wraps=scenario_target_defaults._adversarial_target_override,
        ) as scoped_default,
    ):
        result = ScenarioConfigurationResolver.resolve_adversarial_target(target_name=selection)
        assert result is (selected if selection else None)
        assert get_default_adversarial_target() is outer
        scoped_default.set.assert_not_called()
        if selection is None:
            registry.instances.get.assert_not_called()


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("selection", "message"),
    [
        ("missing", "not found"),
        ("wrong_type", "must be a PromptTarget"),
        ("single_turn", "must support multi_turn"),
    ],
)
def test_resolve_adversarial_target_rejects_invalid_selection_without_changing_scope(
    *, selection: str, message: str
) -> None:
    outer, single_turn = MockPromptTarget(), MockPromptTarget()
    single_turn.apply_capabilities(capabilities=TargetCapabilities(supports_multi_turn=False))
    targets = {"wrong_type": object(), "single_turn": single_turn}
    registry = MagicMock(spec=TargetRegistry.get_registry_singleton())
    registry.instances.get.side_effect = targets.get
    registry.instances.get_names.return_value = list(targets)
    with (
        patch.object(TargetRegistry, "get_registry_singleton", return_value=registry),
        override_default_adversarial_target(outer),
    ):
        with pytest.raises(ValueError, match=message):
            ScenarioConfigurationResolver.resolve_adversarial_target(target_name=selection)
        assert get_default_adversarial_target() is outer
