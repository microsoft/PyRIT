# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Adapted from responsibleai/ASSERT v0.3.0 (2026-09-04).
# Source revision: 04b713fe73879208369750b3bd5ad53544f3eecc.
# Source: assert_ai/library/loader.py and assert_ai/core/judge.py (build_judge_contract).

"""Discover ASSERT judge presets and resolve the dimensions a judge configuration enables."""

from __future__ import annotations

from pathlib import Path

from pyrit.common.path import SCORER_SEED_PROMPT_PATH
from pyrit.models import AssertJudgeConfig, AssertJudgeDimension, AssertJudgePreset
from pyrit.score.true_false.assert_policy_scorer import AssertScoringDimension

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


def load_assert_judge_preset(reference: str | Path) -> AssertJudgePreset:
    """
    Load a judge preset by built-in name or from a YAML file.

    Args:
        reference (str | Path): A built-in preset name such as ``safety-extended``, or a path to a
            ``judge_preset`` YAML file.

    Returns:
        AssertJudgePreset: The preset.

    Raises:
        ValueError: If the name is unknown or the file is not a valid judge preset.
    """
    path = Path(reference)
    if isinstance(reference, Path) or path.suffix.lower() in (".yaml", ".yml"):
        if not path.is_file():
            raise ValueError(f"Judge preset file not found: {reference}")
        return AssertJudgePreset.from_yaml_file(path)
    built_in = ASSERT_JUDGE_PRESETS_PATH / f"{reference}.yaml"
    if not built_in.is_file():
        available = ", ".join(sorted(file.stem for file in ASSERT_JUDGE_PRESETS_PATH.glob("*.yaml")))
        raise ValueError(f"Unknown judge preset '{reference}'. Use a YAML file path or one of: {available}.")
    return AssertJudgePreset.from_yaml_file(built_in)


def resolve_assert_judge_dimensions(config: AssertJudgeConfig) -> tuple[AssertJudgeDimensionSpec, ...]:
    """
    Resolve the ordered dimensions a judge configuration enables.

    Built-ins come first, then each preset's dimensions in list order, then inline dimensions. A later
    definition replaces an earlier one with the same name, and ``disabled_dimensions`` removes names
    from the result.

    Args:
        config (AssertJudgeConfig): The judge configuration.

    Returns:
        tuple[AssertJudgeDimensionSpec, ...]: The enabled dimensions.

    Raises:
        ValueError: If a preset cannot be loaded, a disabled name is unknown, nothing remains enabled,
            or the objective dimension is not an enabled boolean dimension.
    """
    resolved: dict[str, AssertJudgeDimensionSpec] = {dimension.value: dimension for dimension in AssertScoringDimension}
    for reference in config.presets:
        for dimension in load_assert_judge_preset(reference).dimensions:
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
    objective = next((spec for spec in enabled if get_assert_dimension_name(spec) == config.objective_dimension), None)
    if objective is None:
        raise ValueError(
            f"objective_dimension '{config.objective_dimension}' is not an enabled judge dimension. Enabled: "
            f"{', '.join(get_assert_dimension_name(spec) for spec in enabled)}."
        )
    if isinstance(objective, AssertJudgeDimension) and objective.is_ordinal:
        raise ValueError(
            f"objective_dimension '{config.objective_dimension}' is ordinal. The objective must be a boolean "
            "dimension because an ordinal scale does not declare which grades are failures."
        )
