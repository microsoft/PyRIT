# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Adapters between generic prompt generation records and PyRIT's prompt generators.

Generators own model interaction, stages, validation, and resumable progress; dataset providers
package their results. An adapter only translates the generator's request, targets, checkpoint,
review payload, and result summary to and from JSON for the backend job service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from pyrit.backend.models.prompt_generations import (
    PromptGenerationProgress,
    PromptGeneratorDescriptor,
    PromptGeneratorStage,
    PromptGeneratorTargetRole,
    TargetObjectiveGenerationRequest,
)
from pyrit.datasets import AssertProvider, TargetObjectiveProvider
from pyrit.executor.promptgen import AssertGenerator, TargetObjectiveGenerator, TargetObjectiveGeneratorResult
from pyrit.models import (
    AssertDimension,
    AssertGenerationCheckpoint,
    AssertGenerationConfig,
    AssertGenerationStage,
    AssertGroupMetadata,
    AssertTaxonomy,
    PromptGenerationEstimate,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from pydantic import JsonValue

    from pyrit.models import SeedDataset, SeedRecord
    from pyrit.prompt_target import PromptTarget

    CheckpointCallback = Callable[[dict[str, Any], PromptGenerationProgress], Awaitable[None]]


@dataclass(frozen=True)
class GenerationRunOutcome:
    """Where a generation stopped: complete, or paused for review."""

    checkpoint: dict[str, Any]
    progress: PromptGenerationProgress
    awaiting_review: bool


class PromptGenerationAdapter(ABC):
    """Translate one generator's inputs and outputs for the generic job service."""

    descriptor: ClassVar[PromptGeneratorDescriptor]

    @abstractmethod
    def normalize_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """
        Validate a request and return its canonical JSON form.

        Raises:
            ValueError: If the request is invalid.
        """

    @abstractmethod
    def validate_targets(self, *, request: Mapping[str, Any], targets: Mapping[str, PromptTarget]) -> None:
        """
        Check that resolved targets can run the request, without model calls.

        Raises:
            ValueError: If a target lacks a required capability.
        """

    @abstractmethod
    def estimate(self, *, request: Mapping[str, Any], checkpoint: Mapping[str, Any] | None) -> PromptGenerationEstimate:
        """Estimate the remaining work without model calls."""

    @abstractmethod
    def get_progress(
        self, *, request: Mapping[str, Any], checkpoint: Mapping[str, Any] | None
    ) -> PromptGenerationProgress:
        """Describe completed stages and items."""

    @abstractmethod
    async def run_async(
        self,
        *,
        request: Mapping[str, Any],
        targets: Mapping[str, PromptTarget],
        checkpoint: Mapping[str, Any] | None,
        pause_for_review: bool,
        on_checkpoint: CheckpointCallback,
    ) -> GenerationRunOutcome:
        """Run the remaining stages, stopping for review when requested."""

    @abstractmethod
    async def build_dataset_async(
        self,
        *,
        request: Mapping[str, Any],
        targets: Mapping[str, PromptTarget],
        checkpoint: Mapping[str, Any],
        dataset_name: str,
    ) -> tuple[SeedDataset, dict[str, JsonValue]]:
        """Package a complete checkpoint as a dataset and a result summary."""

    def get_review(self, *, checkpoint: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        """
        Return the stage awaiting review and its editable output.

        Raises:
            ValueError: If the generator has no review step.
        """
        raise ValueError(f"The {self.descriptor.name} generator has no review step.")

    def apply_review(self, *, checkpoint: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        """
        Return a checkpoint with reviewed output.

        Raises:
            ValueError: If the generator has no review step.
        """
        raise ValueError(f"The {self.descriptor.name} generator has no review step.")

    def request_from_seeds(self, seeds: Sequence[SeedRecord]) -> dict[str, Any] | None:
        """
        Reconstruct the request that produced stored seeds.

        Returns:
            dict[str, Any] | None: The request, or None when the seeds don't record it.
        """
        return None

    def dataset_matches_checkpoint(self, *, seeds: Sequence[SeedRecord], checkpoint: Mapping[str, Any]) -> bool:
        """
        Report whether stored seeds were saved from this checkpoint, so a retried save can finish.

        Returns:
            bool: False unless the generator can recognize its own output.
        """
        return False


class AssertGenerationAdapter(PromptGenerationAdapter):
    """ASSERT policy generation through ``AssertGenerator`` and ``AssertProvider``."""

    descriptor: ClassVar[PromptGeneratorDescriptor] = PromptGeneratorDescriptor(
        name="assert",
        display_name="ASSERT policy dataset",
        description=(
            "Turn a written policy into a taxonomy of permissible and impermissible behaviors, then generate "
            "seed groups that cover each category across the dimensions you choose."
        ),
        stages=[
            PromptGeneratorStage(name=AssertGenerationStage.SYSTEMATIZATION.value, label="Research the behavior"),
            PromptGeneratorStage(name=AssertGenerationStage.TAXONOMY.value, label="Build the taxonomy"),
            PromptGeneratorStage(
                name=AssertGenerationStage.DIMENSIONS.value, label="Resolve coverage dimensions", reviewable=True
            ),
            PromptGeneratorStage(name=AssertGenerationStage.SEED_GROUPS.value, label="Generate seed groups"),
        ],
        target_roles=[
            PromptGeneratorTargetRole(
                role=AssertGenerator.GENERATION_ROLE,
                label="Generation target",
                description="Builds the taxonomy and writes seed groups.",
                required=True,
            ),
            PromptGeneratorTargetRole(
                role=AssertGenerator.RESEARCH_ROLE,
                label="Research target",
                description="Researches the behavior and dimensions. Must support web search when web search is on.",
                required=False,
            ),
        ],
        follow_up_scenario="foundry.assert_policy",
    )
    REVIEW_STAGE: ClassVar[AssertGenerationStage] = AssertGenerationStage.DIMENSIONS

    def normalize_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """
        Validate an ``AssertGenerationConfig`` request.

        Returns:
            dict[str, Any]: The request with defaults filled in.
        """
        return AssertGenerationConfig.model_validate(dict(request)).model_dump(mode="json")

    def validate_targets(self, *, request: Mapping[str, Any], targets: Mapping[str, PromptTarget]) -> None:
        """Check target capabilities and the request's web search policy."""
        self._create_generator(targets).validate_config(config=AssertGenerationConfig.model_validate(dict(request)))

    def estimate(self, *, request: Mapping[str, Any], checkpoint: Mapping[str, Any] | None) -> PromptGenerationEstimate:
        """
        Estimate the remaining ASSERT model calls.

        Returns:
            PromptGenerationEstimate: Calls by stage and an approximate token range.
        """
        return AssertGenerator.estimate(checkpoint=self._load_checkpoint(request=request, checkpoint=checkpoint))

    def get_progress(
        self, *, request: Mapping[str, Any], checkpoint: Mapping[str, Any] | None
    ) -> PromptGenerationProgress:
        """
        Describe completed ASSERT stages and generated seed groups.

        Returns:
            PromptGenerationProgress: Stage and group counts.
        """
        return self._progress(self._load_checkpoint(request=request, checkpoint=checkpoint))

    async def run_async(
        self,
        *,
        request: Mapping[str, Any],
        targets: Mapping[str, PromptTarget],
        checkpoint: Mapping[str, Any] | None,
        pause_for_review: bool,
        on_checkpoint: CheckpointCallback,
    ) -> GenerationRunOutcome:
        """
        Advance ASSERT generation, pausing after the dimensions stage when review is requested.

        Returns:
            GenerationRunOutcome: The new checkpoint and whether it awaits review.
        """
        current = self._load_checkpoint(request=request, checkpoint=checkpoint)
        if not (pause_for_review and self._is_ready_for_review(current)):

            async def save_async(progress: AssertGenerationCheckpoint) -> None:
                await on_checkpoint(progress.model_dump(mode="json"), self._progress(progress))

            current = await self._create_generator(targets).advance_async(
                checkpoint=current,
                stop_after=self.REVIEW_STAGE if pause_for_review else None,
                on_checkpoint=save_async,
            )
        return GenerationRunOutcome(
            checkpoint=current.model_dump(mode="json"),
            progress=self._progress(current),
            awaiting_review=pause_for_review and self._is_ready_for_review(current),
        )

    async def build_dataset_async(
        self,
        *,
        request: Mapping[str, Any],
        targets: Mapping[str, PromptTarget],
        checkpoint: Mapping[str, Any],
        dataset_name: str,
    ) -> tuple[SeedDataset, dict[str, JsonValue]]:
        """
        Package generated groups with ``AssertProvider``.

        Returns:
            tuple[SeedDataset, dict[str, JsonValue]]: The dataset and its coverage and category counts.
        """
        complete = self._load_checkpoint(request=request, checkpoint=checkpoint)
        result = self._create_generator(targets).build_result(checkpoint=complete)
        dataset = await AssertProvider(dataset_name=dataset_name, result=result).fetch_dataset_async()
        counts = Counter(group.assignment["behavior"] for group in complete.groups)
        summary: dict[str, JsonValue] = {
            "group_count": len(complete.groups),
            "coverage": result.coverage.model_dump(mode="json"),
            "categories": [
                {"name": category.name, "permissible": category.permissible, "groups": counts[category.name]}
                for category in result.taxonomy.behavior_categories
            ],
            "edited_stages": [stage.value for stage in complete.provenance.edited_stages],
        }
        return dataset, summary

    def get_review(self, *, checkpoint: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        """
        Return the generated policy for review.

        Returns:
            tuple[str, dict[str, Any]]: The review stage and the systematization, taxonomy, and dimensions.

        Raises:
            ValueError: If the policy isn't ready for review.
        """
        current = AssertGenerationCheckpoint.model_validate(dict(checkpoint))
        if not self._is_ready_for_review(current):
            raise ValueError("The generated policy isn't ready for review.")
        return self.REVIEW_STAGE.value, {
            "systematization": current.systematization.model_dump(mode="json") if current.systematization else None,
            "taxonomy": current.taxonomy.model_dump(mode="json") if current.taxonomy else None,
            "dimensions": [dimension.model_dump(mode="json") for dimension in current.dimensions or ()],
        }

    def apply_review(self, *, checkpoint: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        """
        Replace the generated taxonomy and dimensions with reviewed versions.

        Returns:
            dict[str, Any]: The reviewed checkpoint.

        Raises:
            ValueError: If the payload is invalid.
        """
        if "taxonomy" not in payload or "dimensions" not in payload:
            raise ValueError("A review needs taxonomy and dimensions.")
        current = AssertGenerationCheckpoint.model_validate(dict(checkpoint))
        reviewed = current.with_reviewed_policy(
            taxonomy=AssertTaxonomy.model_validate(payload["taxonomy"]),
            dimensions=[AssertDimension.model_validate(item) for item in payload["dimensions"] or []],
        )
        return reviewed.model_dump(mode="json")

    def request_from_seeds(self, seeds: Sequence[SeedRecord]) -> dict[str, Any] | None:
        """
        Read the generation request stored in ASSERT seed metadata.

        Returns:
            dict[str, Any] | None: The request, or None when no seed has ASSERT metadata.
        """
        metadata = self._read_metadata(seeds)
        return metadata.config.model_dump(mode="json") if metadata is not None else None

    def dataset_matches_checkpoint(self, *, seeds: Sequence[SeedRecord], checkpoint: Mapping[str, Any]) -> bool:
        """
        Report whether stored seeds came from this checkpoint's generation conversations.

        Returns:
            bool: True when a stored group's research and seed-group conversations belong to the checkpoint.
        """
        metadata = self._read_metadata(seeds)
        if metadata is None:
            return False
        current = AssertGenerationCheckpoint.model_validate(dict(checkpoint))
        provenance = metadata.provenance
        return (
            provenance.systematization_conversation_id == current.provenance.systematization_conversation_id
            and provenance.group_generation_conversation_id in current.conversation_ids
        )

    @staticmethod
    def _read_metadata(seeds: Sequence[SeedRecord]) -> AssertGroupMetadata | None:
        for seed in seeds:
            if seed.seed_type == "objective" and seed.metadata and AssertGroupMetadata.METADATA_KEY in seed.metadata:
                return AssertGroupMetadata.from_metadata(seed.metadata)
        return None

    def _create_generator(self, targets: Mapping[str, PromptTarget]) -> AssertGenerator:
        return AssertGenerator(
            target=targets[AssertGenerator.GENERATION_ROLE], research_target=targets.get(AssertGenerator.RESEARCH_ROLE)
        )

    @staticmethod
    def _load_checkpoint(
        *, request: Mapping[str, Any], checkpoint: Mapping[str, Any] | None
    ) -> AssertGenerationCheckpoint:
        if checkpoint is not None:
            return AssertGenerationCheckpoint.model_validate(dict(checkpoint))
        return AssertGenerationCheckpoint(config=AssertGenerationConfig.model_validate(dict(request)))

    @staticmethod
    def _is_ready_for_review(checkpoint: AssertGenerationCheckpoint) -> bool:
        return checkpoint.next_stage == AssertGenerationStage.SEED_GROUPS and not checkpoint.groups

    @staticmethod
    def _progress(checkpoint: AssertGenerationCheckpoint) -> PromptGenerationProgress:
        stages = list(AssertGenerationStage)
        next_stage = checkpoint.next_stage
        done = stages if next_stage is None else stages[: stages.index(next_stage)]
        return PromptGenerationProgress(
            completed_stages=[stage.value for stage in done],
            current_stage=next_stage.value if next_stage is not None else None,
            items_completed=len(checkpoint.groups),
            items_total=checkpoint.config.group_count,
        )


class TargetObjectiveGenerationAdapter(PromptGenerationAdapter):
    """Objective generation through ``TargetObjectiveGenerator`` and ``TargetObjectiveProvider``."""

    STAGE: ClassVar[str] = "objectives"
    descriptor: ClassVar[PromptGeneratorDescriptor] = PromptGeneratorDescriptor(
        name="target_objective",
        display_name="Objective dataset",
        description="Generate a list of distinct attack objectives from written instructions.",
        stages=[PromptGeneratorStage(name="objectives", label="Generate objectives")],
        target_roles=[
            PromptGeneratorTargetRole(
                role=TargetObjectiveGenerator.GENERATION_ROLE,
                label="Generation target",
                description="Writes the objectives.",
                required=True,
            )
        ],
    )

    def normalize_request(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """
        Validate an objective generation request.

        Returns:
            dict[str, Any]: The request with defaults filled in.
        """
        return TargetObjectiveGenerationRequest.model_validate(dict(request)).model_dump(mode="json")

    def validate_targets(self, *, request: Mapping[str, Any], targets: Mapping[str, PromptTarget]) -> None:
        """Construct the generator, which checks the target and bundled prompt."""
        TargetObjectiveGenerator(target=targets[TargetObjectiveGenerator.GENERATION_ROLE])

    def estimate(self, *, request: Mapping[str, Any], checkpoint: Mapping[str, Any] | None) -> PromptGenerationEstimate:
        """
        Estimate one generation call, or no calls when objectives exist.

        Returns:
            PromptGenerationEstimate: Calls and an approximate token range.
        """
        if checkpoint is not None:
            return PromptGenerationEstimate(exact_calls=True)
        parsed = TargetObjectiveGenerationRequest.model_validate(dict(request))
        return TargetObjectiveGenerator.estimate(
            instructions=parsed.instructions, count=parsed.count, harm_categories=parsed.harm_categories
        )

    def get_progress(
        self, *, request: Mapping[str, Any], checkpoint: Mapping[str, Any] | None
    ) -> PromptGenerationProgress:
        """
        Describe whether objectives exist.

        Returns:
            PromptGenerationProgress: The single stage and objective counts.
        """
        total = TargetObjectiveGenerationRequest.model_validate(dict(request)).count
        if checkpoint is None:
            return PromptGenerationProgress(current_stage=self.STAGE, items_total=total)
        done = len(TargetObjectiveGeneratorResult.model_validate(dict(checkpoint)).objectives)
        return PromptGenerationProgress(completed_stages=[self.STAGE], items_completed=done, items_total=total)

    async def run_async(
        self,
        *,
        request: Mapping[str, Any],
        targets: Mapping[str, PromptTarget],
        checkpoint: Mapping[str, Any] | None,
        pause_for_review: bool,
        on_checkpoint: CheckpointCallback,
    ) -> GenerationRunOutcome:
        """
        Generate the objectives unless they already exist.

        Returns:
            GenerationRunOutcome: The objectives as the checkpoint.
        """
        if checkpoint is None:
            parsed = TargetObjectiveGenerationRequest.model_validate(dict(request))
            result = await TargetObjectiveGenerator(
                target=targets[TargetObjectiveGenerator.GENERATION_ROLE]
            ).execute_async(
                instructions=parsed.instructions, count=parsed.count, harm_categories=list(parsed.harm_categories)
            )
            checkpoint = result.model_dump(mode="json")
            await on_checkpoint(dict(checkpoint), self.get_progress(request=request, checkpoint=checkpoint))
        return GenerationRunOutcome(
            checkpoint=dict(checkpoint),
            progress=self.get_progress(request=request, checkpoint=checkpoint),
            awaiting_review=False,
        )

    async def build_dataset_async(
        self,
        *,
        request: Mapping[str, Any],
        targets: Mapping[str, PromptTarget],
        checkpoint: Mapping[str, Any],
        dataset_name: str,
    ) -> tuple[SeedDataset, dict[str, JsonValue]]:
        """
        Package the objectives with ``TargetObjectiveProvider``.

        Returns:
            tuple[SeedDataset, dict[str, JsonValue]]: The dataset and its objective count.
        """
        result = TargetObjectiveGeneratorResult.model_validate(dict(checkpoint))
        parsed = TargetObjectiveGenerationRequest.model_validate(dict(request))
        dataset = TargetObjectiveProvider.package_result(
            dataset_name=dataset_name,
            result=result,
            instructions=parsed.instructions,
            harm_categories=parsed.harm_categories,
        )
        return dataset, {"objective_count": len(result.objectives)}

    def request_from_seeds(self, seeds: Sequence[SeedRecord]) -> dict[str, Any] | None:
        """
        Read the generation request stored in objective metadata.

        Returns:
            dict[str, Any] | None: The request, or None when no seed records one.
        """
        for seed in seeds:
            request = TargetObjectiveProvider.request_from_metadata(seed.metadata)
            if request is not None:
                return self.normalize_request(request)
        return None

    def dataset_matches_checkpoint(self, *, seeds: Sequence[SeedRecord], checkpoint: Mapping[str, Any]) -> bool:
        """
        Report whether stored objectives came from this checkpoint's generation conversation.

        Returns:
            bool: True when a stored objective names the checkpoint's conversation.
        """
        conversation_id = TargetObjectiveGeneratorResult.model_validate(dict(checkpoint)).conversation_id
        return any((seed.metadata or {}).get("generation_conversation_id") == conversation_id for seed in seeds)


def create_prompt_generation_adapters() -> dict[str, PromptGenerationAdapter]:
    """
    Create the adapters for every generator the backend can run.

    Returns:
        dict[str, PromptGenerationAdapter]: Adapters keyed by generator name.
    """
    adapters: list[PromptGenerationAdapter] = [AssertGenerationAdapter(), TargetObjectiveGenerationAdapter()]
    return {adapter.descriptor.name: adapter for adapter in adapters}
