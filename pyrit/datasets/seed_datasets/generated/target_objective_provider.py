# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.datasets.seed_datasets.seed_dataset_provider import SeedDatasetProvider
from pyrit.executor.promptgen import TargetObjectiveGenerator
from pyrit.models import SeedDataset, SeedObjective, SeedOrigin

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.executor.promptgen import TargetObjectiveGeneratorResult
    from pyrit.models import SeedPrompt
    from pyrit.prompt_target import PromptTarget


class TargetObjectiveProvider(SeedDatasetProvider):
    """Explicitly generate named objectives without storing or registering them."""

    should_register = False
    REQUEST_METADATA_KEY: ClassVar[str] = "target_objective_request"

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
        return self.package_result(
            dataset_name=self.dataset_name,
            result=result,
            instructions=self._instructions,
            harm_categories=self._harm_categories,
        )

    @classmethod
    def package_result(
        cls,
        *,
        dataset_name: str,
        result: TargetObjectiveGeneratorResult,
        instructions: str,
        harm_categories: Sequence[str] = (),
    ) -> SeedDataset:
        """
        Package generated objectives as a named dataset that records the request that generated it.

        Args:
            dataset_name (str): Destination name, assigned to every seed.
            result (TargetObjectiveGeneratorResult): The generated objectives.
            instructions (str): The generation instructions, stored so the request can be reconstructed.
            harm_categories (Sequence[str]): Labels for every seed.

        Returns:
            SeedDataset: Text objectives with generation evidence references and no actor attribution.

        Raises:
            ValueError: If dataset_name is empty.
        """
        if not isinstance(dataset_name, str) or not dataset_name.strip():
            raise ValueError("dataset_name must be a non-empty string.")
        request = {
            "instructions": instructions,
            "count": len(result.objectives),
            "harm_categories": list(harm_categories),
        }
        return SeedDataset(
            dataset_name=dataset_name,
            seeds=[
                SeedObjective(
                    value=value,
                    dataset_name=dataset_name,
                    origin=SeedOrigin.GENERATED,
                    is_jinja_template=False,
                    harm_categories=list(harm_categories),
                    metadata={
                        "generation_conversation_id": result.conversation_id,
                        cls.REQUEST_METADATA_KEY: request,
                    },
                )
                for value in result.objectives
            ],
        )

    @classmethod
    def request_from_metadata(cls, metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
        """
        Read the generation request stored in a packaged objective's metadata.

        Args:
            metadata (Mapping[str, Any] | None): A seed's metadata.

        Returns:
            dict[str, Any] | None: The instructions, count, and harm categories, or None if the seed
                doesn't record them.
        """
        request = (metadata or {}).get(cls.REQUEST_METADATA_KEY)
        return dict(request) if isinstance(request, Mapping) else None
