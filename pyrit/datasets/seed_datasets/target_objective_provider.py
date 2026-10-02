# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
from pyrit.executor.promptgen import TargetObjectiveGenerator
from pyrit.models import SeedDataset, SeedObjective, SeedOrigin

if TYPE_CHECKING:
    from pyrit.models import SeedPrompt
    from pyrit.prompt_target import PromptTarget


class TargetObjectiveProvider(SeedDatasetProvider):
    """Explicitly generate named objectives without storing or registering them."""

    should_register = False

    def __init__(
        self,
        *,
        dataset_name: str,
        target: PromptTarget,
        instructions: str,
        count: int,
        system_prompt: SeedPrompt | None = None,
        harm_categories: list[str] | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        """
        Configure one objective generation request.

        Args:
            dataset_name: Non-empty destination name, assigned to every seed.
            target: Target used by the generation strategy.
            instructions: Guidance for generation.
            count: Exact number of distinct non-empty objectives to generate.
            system_prompt: Optional text SeedPrompt with generation rules and a response_json_schema.
                Defaults to the bundled generation YAML.
            harm_categories: Generation guidance and caller-supplied seed labels.
            timeout_seconds: Execution deadline, including retry waits.
                Cleanup can take up to five additional seconds after cancellation.

        Raises:
            ValueError: If the name or generator configuration is invalid.
        """
        if not isinstance(dataset_name, str) or not dataset_name.strip():
            raise ValueError("dataset_name must be a non-empty string.")
        self._dataset_name = dataset_name
        self._instructions = instructions
        self._count = count
        self._harm_categories = list(harm_categories or [])
        self._generator = TargetObjectiveGenerator(
            target=target, system_prompt=system_prompt, timeout_seconds=timeout_seconds
        )

    @property
    def dataset_name(self) -> str:
        """The destination name shared by every generated seed."""
        return self._dataset_name

    async def fetch_dataset_async(self, *, cache: bool = True) -> SeedDataset:
        """
        Generate a fresh, complete dataset on every explicit call.

        Args:
            cache: Accepted for provider compatibility; generated output is never cached.

        Returns:
            Text objectives with generation evidence references and no actor attribution.
        """
        result = await self._generator.execute_async(
            instructions=self._instructions, count=self._count, harm_categories=self._harm_categories
        )
        return SeedDataset(
            dataset_name=self.dataset_name,
            seeds=[
                SeedObjective(
                    value=value,
                    dataset_name=self.dataset_name,
                    origin=SeedOrigin.GENERATED,
                    is_jinja_template=False,
                    harm_categories=list(self._harm_categories),
                    metadata={"generation_conversation_id": result.conversation_id},
                )
                for value in result.objectives
            ],
        )
