# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Adapted from responsibleai/ASSERT v0.3.0 (2026-09-04).
# Source revision: 04b713fe73879208369750b3bd5ad53544f3eecc.
# Source: assert_ai/library/loader.py.

"""Discover and load ASSERT judge presets."""

from __future__ import annotations

from pathlib import Path

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import AssertJudgePreset

ASSERT_JUDGE_PRESETS_PATH = Path(SCORER_SEED_PROMPT_PATH, "assert", "judge_presets").resolve()


def list_assert_judge_presets() -> dict[str, AssertJudgePreset]:
    """
    Load every judge preset that ships with PyRIT.

    Returns:
        dict[str, AssertJudgePreset]: Presets keyed by name, in name order.
    """
    presets = (AssertJudgePreset.from_yaml_file(path) for path in sorted(ASSERT_JUDGE_PRESETS_PATH.glob("*.yaml")))
    return {preset.name: preset for preset in presets}


def load_assert_judge_preset(name: str) -> AssertJudgePreset:
    """
    Load a judge preset that ships with PyRIT.

    Only shipped names are accepted, so a name can't reach other files. Load your own preset files
    with ``load_assert_judge_preset_file``.

    Args:
        name (str): A built-in preset name such as ``safety-extended``.

    Returns:
        AssertJudgePreset: The preset.

    Raises:
        ValueError: If the name is not a built-in preset.
    """
    available = _list_built_in_preset_names()
    if name not in available:
        raise ValueError(f"Unknown judge preset. Use one of: {', '.join(available)}.")
    return AssertJudgePreset.from_yaml_file(ASSERT_JUDGE_PRESETS_PATH / f"{name}.yaml")


def load_assert_judge_preset_file(path: str | Path) -> AssertJudgePreset:
    """
    Load a judge preset from your own ``judge_preset`` YAML file.

    Args:
        path (str | Path): The preset file.

    Returns:
        AssertJudgePreset: The preset.

    Raises:
        ValueError: If the file doesn't exist or is not a valid judge preset.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"Judge preset file not found: {path}")
    return AssertJudgePreset.from_yaml_file(path)


def _list_built_in_preset_names() -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in ASSERT_JUDGE_PRESETS_PATH.glob("*.yaml")))
