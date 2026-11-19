# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The bundled ASSERT behavior and suite library, adapted from responsibleai/ASSERT v0.3.0."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import TypeVar

from pyrit.common.path import DATASETS_PATH
from pyrit.models import AssertBehaviorPreset, AssertSuitePreset

ASSERT_BEHAVIOR_PRESETS_PATH = Path(DATASETS_PATH, "assert_behaviors").resolve()
ASSERT_SUITE_PRESETS_PATH = Path(DATASETS_PATH, "assert_suites").resolve()

PresetT = TypeVar("PresetT", AssertBehaviorPreset, AssertSuitePreset)


def list_assert_behavior_presets() -> dict[str, AssertBehaviorPreset]:
    """
    Load every bundled ASSERT behavior preset.

    Returns:
        dict[str, AssertBehaviorPreset]: Presets keyed by name, in name order.
    """
    return dict(_load_behaviors())


def load_assert_behavior_preset(name: str) -> AssertBehaviorPreset:
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


def load_assert_suite_preset(name: str) -> AssertSuitePreset:
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
    return _load_presets(preset_type=AssertBehaviorPreset, directory=ASSERT_BEHAVIOR_PRESETS_PATH)


@functools.cache
def _load_suites() -> dict[str, AssertSuitePreset]:
    behaviors = _load_behaviors()
    suites = _load_presets(preset_type=AssertSuitePreset, directory=ASSERT_SUITE_PRESETS_PATH)
    for suite in suites.values():
        missing = sorted(set(suite.behaviors) - set(behaviors))
        if missing:
            raise ValueError(f"ASSERT suite {suite.name!r} refers to unknown behaviors: {missing}.")
    return suites


def _load_presets(*, preset_type: type[PresetT], directory: Path) -> dict[str, PresetT]:
    presets: dict[str, PresetT] = {}
    for path in sorted(directory.glob("*.yaml")):
        preset = preset_type.from_yaml_file(path)
        if preset.name != path.stem:
            raise ValueError(f"{path} must declare name {path.stem!r}.")
        presets[preset.name] = preset
    return presets


def _lookup(*, presets: dict[str, PresetT], name: str, kind: str) -> PresetT:
    preset = presets.get(name)
    if preset is None:
        raise ValueError(f"No bundled ASSERT {kind} is named {name!r}. Available: {sorted(presets)}.")
    return preset
