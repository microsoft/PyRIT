# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Adapted from responsibleai/ASSERT v0.3.0 (2026-09-04).
# Source revision: 04b713fe73879208369750b3bd5ad53544f3eecc.
# Source: assert_ai/library/loader.py and assert_ai/core/judge.py (build_judge_contract).

"""Discover ASSERT judge presets and resolve the dimensions a judge configuration enables."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import AssertJudgeDimension, AssertJudgePreset
from pyrit.score.true_false.assert_policy_scorer import AssertScoringDimension

if TYPE_CHECKING:
    from pyrit.models.score.assert_judge import AssertJudgeConfig

ASSERT_JUDGE_PRESETS_PATH = Path(SCORER_SEED_PROMPT_PATH, "assert", "judge_presets").resolve()

AssertJudgeDimensionSpec = AssertScoringDimension | AssertJudgeDimension


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


def resolve_assert_judge_dimensions(config: AssertJudgeConfig) -> tuple[AssertJudgeDimensionSpec, ...]:
    """
    Resolve the ordered dimensions a judge configuration enables.

    Built-ins come first, then built-in presets, then preset files, then inline dimensions. A later
    definition replaces an earlier one with the same name, and ``disabled_dimensions`` removes names
    from the result.

    Args:
        config (AssertJudgeConfig): The judge configuration.

    Returns:
        tuple[AssertJudgeDimensionSpec, ...]: The enabled dimensions.

    Raises:
        ValueError: If a preset cannot be loaded, a disabled name is unknown, nothing remains enabled,
            or the objective dimension is not enabled.
    """
    resolved: dict[str, AssertJudgeDimensionSpec] = {dimension.value: dimension for dimension in AssertScoringDimension}
    presets = [load_assert_judge_preset(name) for name in config.presets]
    presets.extend(load_assert_judge_preset_file(path) for path in config.preset_files)
    for preset in presets:
        for dimension in preset.dimensions:
            resolved[dimension.name] = dimension
    for dimension in config.dimensions:
        resolved[dimension.name] = dimension

    unknown = sorted(set(config.disabled_dimensions) - set(resolved))
    if unknown:
        raise ValueError(f"Unknown disabled judge dimensions: {', '.join(unknown)}. Known: {', '.join(resolved)}.")
    enabled = tuple(spec for name, spec in resolved.items() if name not in config.disabled_dimensions)
    if not enabled:
        raise ValueError("The judge configuration must leave at least one dimension enabled.")
    _validate_objective_dimension(config=config, enabled=enabled)
    return enabled


def _list_built_in_preset_names() -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in ASSERT_JUDGE_PRESETS_PATH.glob("*.yaml")))


def get_assert_dimension_name(dimension: AssertJudgeDimensionSpec) -> str:
    """
    Return the score category a dimension's judge writes.

    Args:
        dimension (AssertJudgeDimensionSpec): A built-in or custom dimension.

    Returns:
        str: The dimension name.
    """
    return dimension.value if isinstance(dimension, AssertScoringDimension) else dimension.name


def _validate_objective_dimension(*, config: AssertJudgeConfig, enabled: tuple[AssertJudgeDimensionSpec, ...]) -> None:
    names = [get_assert_dimension_name(spec) for spec in enabled]
    if config.objective_dimension not in names:
        raise ValueError(
            f"objective_dimension '{config.objective_dimension}' is not an enabled judge dimension. "
            f"Enabled: {', '.join(names)}."
        )
