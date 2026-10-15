# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Scenario-owned override validation and lightweight dataset projections."""

from dataclasses import replace
from typing import Any

from pyrit.models.scenario_dataset import (
    DatasetSelectionMode,
    ScenarioDatasetOptions,
    ScenarioDatasetRequest,
    ScenarioDatasetSettings,
    ScenarioDatasetSourceSettings,
)
from pyrit.scenario.core.dataset_configuration import (
    CompoundDatasetAttackConfiguration,
    DatasetAttackConfiguration,
    DatasetConstraintError,
    DatasetSource,
    DatasetSourceKind,
)


def apply_dataset_overrides(
    *,
    config: DatasetAttackConfiguration,
    request: ScenarioDatasetRequest,
    options: ScenarioDatasetOptions,
) -> DatasetAttackConfiguration:
    """
    Apply named-source edits without rebuilding custom configurations.

    Returns:
        DatasetAttackConfiguration: Shallow copy preserving live object identity.

    Raises:
        DatasetConstraintError: If a requested edit is unsupported.
    """
    if not options.supports_overrides and request != ScenarioDatasetRequest():
        raise DatasetConstraintError("This scenario does not support dataset overrides.")
    if not options.per_dataset_limit and (
        request.max_per_dataset != "default" or any(source.max_size != "default" for source in request.sources or [])
    ):
        raise DatasetConstraintError("This scenario does not support per-dataset limits.")
    if not options.total_limit and request.max_total != "default":
        raise DatasetConstraintError("This scenario does not support a total dataset limit.")
    if not options.filters and request.filters:
        raise DatasetConstraintError("This scenario does not support dataset filters.")
    kwargs: dict[str, Any] = {}
    if request.sources is not None:
        if isinstance(config, CompoundDatasetAttackConfiguration):
            raise DatasetConstraintError("Compound sources are fixed; source edits are ambiguous.")
        existing = {source.name: source for source in config.sources}
        selected = []
        for source in request.sources:
            base = existing.get(source.name, DatasetSource(name=source.name))
            edits = {
                **({"max_size": source.max_size} if source.max_size != "default" else {}),
                **({"fetch": source.fetch} if source.fetch is not None else {}),
            }
            selected.append(replace(base, **edits) if edits else base)
        kwargs["sources"] = selected
    if request.fetch is not None:
        kwargs["fetch"] = request.fetch
    effective = config.with_overrides(
        **kwargs,
        max_per_dataset=request.max_per_dataset,
        max_total=request.max_total,
        filters=request.filters,
    )
    validate_dataset_selection(config=effective, defaults=config, options=options)
    return effective


def validate_dataset_selection(
    *,
    config: DatasetAttackConfiguration,
    defaults: DatasetAttackConfiguration,
    options: ScenarioDatasetOptions,
) -> None:
    """
    Validate names before preparation while preserving inline Python inputs.

    Raises:
        DatasetConstraintError: If the source selection violates scenario capabilities.
    """
    if config.source_kind is DatasetSourceKind.INLINE:
        return
    names = config.dataset_names
    if options.mode is DatasetSelectionMode.FIXED and names != defaults.dataset_names:
        raise DatasetConstraintError(f"Dataset sources are fixed: {', '.join(defaults.dataset_names)}.")
    if options.mode is DatasetSelectionMode.SUBSET and (not names or set(names) - set(options.allowed_names)):
        raise DatasetConstraintError(f"Select datasets from: {', '.join(options.allowed_names)}.")
    if options.max_selected is not None and len(names) > options.max_selected:
        raise DatasetConstraintError(f"Select at most {options.max_selected} dataset(s).")


def project_dataset_settings(config: DatasetAttackConfiguration) -> ScenarioDatasetSettings:
    """
    Project configuration settings without memory reads or live wire objects.

    Returns:
        ScenarioDatasetSettings: Effective settings for response data.
    """
    children = config.configurations if isinstance(config, CompoundDatasetAttackConfiguration) else (config,)
    return ScenarioDatasetSettings(
        sources=[
            ScenarioDatasetSourceSettings(
                name=source.name,
                max_size=child.source_limit(source.name),
                fetch=source.fetch or child.fetch,
                configured_provider=getattr(source, "provider", None) is not None,
            )
            for child in children
            for source in child.sources
        ],
        max_per_dataset=config.max_per_dataset,
        max_total=config.max_total,
        fetch=children[0].fetch,
        filters=config.filters,
    )
