# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Package complete ASSERT generation results as named seed datasets."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
from pyrit.executor.promptgen import AssertGeneratorResult
from pyrit.models import (
    AssertGenerationConfig,
    SeedDataset,
)

if TYPE_CHECKING:
    from pyrit.executor.promptgen import AssertGenerator


class AssertProvider(SeedDatasetProvider):
    """
    A named dataset from an ASSERT generation result or configured generator.

    Reference: [@agarwal2026systematization]
    """

    should_register = False

    def __init__(
        self,
        *,
        dataset_name: str,
        generator: AssertGenerator | None = None,
        config: AssertGenerationConfig | None = None,
        result: AssertGeneratorResult | None = None,
    ) -> None:
        """
        Select a result or a generator/config pair.

        Args:
            dataset_name (str): Destination dataset name.
            generator (AssertGenerator | None): Native strategy, required with config.
            config (AssertGenerationConfig | None): Request, required with generator.
            result (AssertGeneratorResult | None): Complete result, instead of generator/config.

        Raises:
            ValueError: If the name or source combination is invalid.
        """
        if not isinstance(dataset_name, str) or not dataset_name.strip():
            raise ValueError("dataset_name must be a non-empty string.")
        if result is not None:
            if generator is not None or config is not None:
                raise ValueError("Supply result or generator/config, not both.")
            result = AssertGeneratorResult.model_validate(result)
        elif generator is None or config is None:
            raise ValueError("Supply a complete result or both generator and config.")
        self._dataset_name = dataset_name
        self._generator = generator
        self._config = AssertGenerationConfig.model_validate(config) if config is not None else None
        self._result = result

    @property
    def dataset_name(self) -> str:
        """The destination dataset name."""
        return self._dataset_name

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Package a validated generation result.

        Args:
            cache (bool): Unused provider compatibility option.

        Returns:
            SeedDataset: Named seeds with their associations and generation metadata.
        """
        result = await self._get_result_async()
        seeds = [seed.model_copy(deep=True) for group in result.seed_groups for seed in group.seeds]
        for seed in seeds:
            seed.dataset_name = self.dataset_name
        return SeedDataset(
            dataset_name=self.dataset_name,
            seeds=seeds,
        )

    async def _get_result_async(self) -> AssertGeneratorResult:
        if self._result is not None:
            return AssertGeneratorResult.model_validate(self._result)
        if self._generator is None or self._config is None:
            raise ValueError("ASSERT generation requires a generator and config.")
        result = await self._generator.execute_async(config=self._config)
        result = AssertGeneratorResult.model_validate(result)
        if result.config != self._config:
            raise ValueError("The generated ASSERT result does not match the provider's request.")
        return result
