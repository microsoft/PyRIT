# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The bundled ASSERT behavior and suite library, adapted from responsibleai/ASSERT v0.3.0."""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Any, TypeVar

from pyrit.common.path import ASSERT_BEHAVIORS_PATH, ASSERT_SUITES_PATH
from pyrit.common.yaml_helper import safe_load_yaml
from pyrit.models import AssertBehaviorPreset, AssertSuitePreset

if TYPE_CHECKING:
    from pathlib import Path

PresetT = TypeVar("PresetT", AssertBehaviorPreset, AssertSuitePreset)


def list_assert_behavior_presets() -> dict[str, AssertBehaviorPreset]:
    """
    Load every bundled ASSERT behavior preset.

    Returns:
        dict[str, AssertBehaviorPreset]: Presets keyed by name, in name order.
    """
    return dict(_load_behaviors())


def get_assert_behavior_preset(name: str) -> AssertBehaviorPreset:
    """
    Load one bundled ASSERT behavior preset.

    Args:
        name (str): The preset name, for example ``prompt_injection``.

    Returns:
        AssertBehaviorPreset: The preset.

    Raises:
        ValueError: If no bundled behavior has the name.
    """
    return _lookup(presets=_load_behaviors(), name=name, kind="behavior")


def list_assert_suite_presets() -> dict[str, AssertSuitePreset]:
    """
    Load every bundled ASSERT suite preset.

    Returns:
        dict[str, AssertSuitePreset]: Suites keyed by name, in name order.
    """
    return dict(_load_suites())


def get_assert_suite_preset(name: str) -> AssertSuitePreset:
    """
    Load one bundled ASSERT suite preset.

    Args:
        name (str): The suite name, for example ``travel_planner``.

    Returns:
        AssertSuitePreset: The suite.

    Raises:
        ValueError: If no bundled suite has the name.
    """
    return _lookup(presets=_load_suites(), name=name, kind="suite")


@functools.cache
def _load_behaviors() -> dict[str, AssertBehaviorPreset]:
    presets = [
        AssertBehaviorPreset.model_validate(_read_preset(path=path, kind="behavior"))
        for path in sorted(ASSERT_BEHAVIORS_PATH.glob("*.yaml"))
    ]
    return _index(presets)


@functools.cache
def _load_suites() -> dict[str, AssertSuitePreset]:
    behaviors = _load_behaviors()
    suites = [
        AssertSuitePreset.model_validate(_read_preset(path=path, kind="scenario"))
        for path in sorted(ASSERT_SUITES_PATH.glob("*.yaml"))
    ]
    for suite in suites:
        missing = sorted(set(suite.behaviors) - set(behaviors))
        if missing:
            raise ValueError(f"ASSERT suite {suite.name!r} refers to unknown behaviors: {missing}.")
    return _index(suites)


def _read_preset(*, path: Path, kind: str) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        data = safe_load_yaml(file)
    if not isinstance(data, dict) or data.pop("kind", None) != kind:
        raise ValueError(f"{path} is not an ASSERT {kind} preset.")
    if data.get("name") != path.stem:
        raise ValueError(f"{path} must declare name {path.stem!r}.")
    return data


def _index(presets: list[PresetT]) -> dict[str, PresetT]:
    return {preset.name: preset for preset in presets}


def _lookup(*, presets: dict[str, PresetT], name: str, kind: str) -> PresetT:
    preset = presets.get(name)
    if preset is None:
        raise ValueError(f"No bundled ASSERT {kind} is named {name!r}. Available: {sorted(presets)}.")
    return preset
