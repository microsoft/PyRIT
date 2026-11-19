# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Adapted from responsibleai/ASSERT v0.3.0 (2026-09-04).
# Source revision: 04b713fe73879208369750b3bd5ad53544f3eecc.

"""Generate native attack seed groups with ASSERT's taxonomy and coverage method."""

from __future__ import annotations

import asyncio
import functools
import json
import math
import random
import uuid
from collections import Counter
from dataclasses import dataclass, field
from itertools import product
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.exceptions import BadRequestException, InvalidJsonException
from pyrit.executor.promptgen.core import (
    PromptGeneratorStrategy,
    PromptGeneratorStrategyContext,
    PromptGeneratorStrategyResult,
)
from pyrit.models import (
    AdversarialPrompts,
    AssertCoverage,
    AssertDimension,
    AssertGeneratedGroupContent,
    AssertGenerationCheckpoint,
    AssertGenerationConfig,
    AssertGenerationStage,
    AssertGroupMetadata,
    AssertReplication,
    AssertSamplingConfig,
    AssertSamplingMethod,
    AssertSystematization,
    AssertTaxonomy,
    AttackSeedGroup,
    JsonResponseConfig,
    Message,
    PromptGenerationEstimate,
    PromptGenerationStageEstimate,
    SeedObjective,
    SeedOrigin,
    SeedPrompt,
    TargetIdentifier,
    WebSearchConfig,
    WebSearchMode,
)
from pyrit.prompt_normalizer import PromptNormalizer, send_json_with_retry_async
from pyrit.prompt_target import CapabilityName, TargetRequirements

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence

    from pyrit.models import SeedUnion
    from pyrit.prompt_target import PromptTarget

T = TypeVar("T")


class _DimensionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimensions: tuple[AssertDimension, ...]


_Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]


class _GeneratedGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: _Text
    objective: _Text
    user_prompt: _Text | None = None
    adversarial_instructions: _Text | None = None
    system_prompt: Annotated[str, StringConstraints(max_length=0)] | _Text = ""


class _GroupBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed_groups: tuple[_GeneratedGroup, ...]


@dataclass
class AssertGeneratorContext(PromptGeneratorStrategyContext):
    """Single-use request and the generator-owned conversations for its stages."""

    config: AssertGenerationConfig
    _used: bool = field(default=False, init=False, repr=False)
    _conversations: list[tuple[PromptTarget, str]] = field(default_factory=list, init=False, repr=False)


class AssertGeneratorResult(PromptGeneratorStrategyResult):
    """Complete native seed groups, policy, coverage, and generation evidence."""

    model_config = ConfigDict(revalidate_instances="always")

    config: AssertGenerationConfig
    seed_groups: tuple[AttackSeedGroup, ...]
    taxonomy: AssertTaxonomy
    dimensions: tuple[AssertDimension, ...]
    coverage: AssertCoverage
    systematization: AssertSystematization | None = None
    generation_conversation_ids: tuple[str, ...] = ()
    generation_targets: dict[str, JsonValue] = Field(default_factory=dict)
    source_revision: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = (
        AssertGroupMetadata.SOURCE_REVISION
    )

    @model_validator(mode="after")
    def _validate_complete_result(self) -> Self:
        if self.systematization is not None and self.systematization.behavior != self.config.behavior_name:
            raise ValueError("The systematization behavior must match the generation request.")
        if len(self.seed_groups) != self.config.group_count:
            raise ValueError(f"Requested {self.config.group_count} seed groups, received {len(self.seed_groups)}.")
        if len({group.objective.prompt_group_id for group in self.seed_groups}) != len(self.seed_groups):
            raise ValueError("Each generated group needs a distinct prompt_group_id for storage.")
        seed_ids = [seed.id for group in self.seed_groups for seed in group.seeds]
        if len(set(seed_ids)) != len(seed_ids):
            raise ValueError("Generated groups must have distinct seed row IDs.")
        metadata = [AssertGroupMetadata.from_metadata(group.objective.metadata) for group in self.seed_groups]
        if len({item.group_id for item in metadata}) != len(metadata):
            raise ValueError("Generated group IDs must be unique.")
        for group, item in zip(self.seed_groups, metadata, strict=True):
            if item.config != self.config or item.taxonomy != self.taxonomy or item.dimensions != self.dimensions:
                raise ValueError("Seed group metadata must match the generation request and policy.")
            self._validate_group_prompts(group)
            if not set(item.provenance.conversation_ids).issubset(self.generation_conversation_ids):
                raise ValueError(f"Group {item.group_id!r} refers to unknown generation conversations.")
        levels = self.taxonomy.get_coverage_levels(self.dimensions)
        measured = AssertCoverage.from_assignments(levels=levels, assignments=[item.assignment for item in metadata])
        if measured != self.coverage:
            raise ValueError("Coverage must match the actual seed group assignments.")
        return self

    def _validate_group_prompts(self, group: AttackSeedGroup) -> None:
        if len(group.seeds) != 1 + len(group.prompts) or any(
            prompt.role not in {"system", "user"} or not prompt.value.strip() for prompt in group.prompts
        ):
            raise ValueError("Generated groups contain an objective and non-empty target system/user prompts only.")
        users = [prompt for prompt in group.prompts if prompt.role == "user"]
        if len(users) != int(self.config.include_user_prompt):
            raise ValueError("Seed groups must contain the requested user prompt.")
        if bool(group.adversarial_prompts) != self.config.include_adversarial_prompt:
            raise ValueError("Seed groups must contain the requested adversarial prompts.")
        systems = [prompt.value for prompt in group.prompts if prompt.role == "system"]
        if len(systems) > 1:
            raise ValueError("Generated groups can contain at most one target system prompt.")
        if self.config.target_owns_instructions and systems:
            raise ValueError("Target-owned instructions cannot be replaced by generated system prompts.")
        if self.config.target_system_prompt is not None and systems != [self.config.target_system_prompt]:
            raise ValueError("Seed groups must retain the fixed target system prompt.")


class AssertGenerator(PromptGeneratorStrategy[AssertGeneratorContext, AssertGeneratorResult]):
    """
    Generate a taxonomy, coverage assignments, and related objective and prompt seeds.

    Model calls are sequential. Targets own request timeouts and transport retries;
    the generator validates responses through PyRIT's JSON retry mechanism.
    """

    _PROMPT_DIRECTORY = EXECUTOR_SEED_PROMPT_PATH / "promptgen" / "assert"
    _BATCH_SIZE = 5
    _CLEANUP_TIMEOUT_SECONDS = 5.0
    _TARGET_REQUIREMENTS = TargetRequirements(
        native_required=frozenset({CapabilityName.MULTI_TURN, CapabilityName.EDITABLE_HISTORY}),
        required=frozenset({CapabilityName.SYSTEM_PROMPT}),
        required_input_modalities=frozenset({frozenset({"text"})}),
        required_output_modalities=frozenset({frozenset({"text"})}),
    )
    GENERATION_ROLE: ClassVar[str] = "generation"
    RESEARCH_ROLE: ClassVar[str] = "research"
    _STAGE_ROLES: ClassVar[dict[AssertGenerationStage, str]] = {
        AssertGenerationStage.SYSTEMATIZATION: RESEARCH_ROLE,
        AssertGenerationStage.TAXONOMY: GENERATION_ROLE,
        AssertGenerationStage.DIMENSIONS: RESEARCH_ROLE,
        AssertGenerationStage.SEED_GROUPS: GENERATION_ROLE,
    }
    SYSTEMATIZATION_OUTPUT_TOKENS: ClassVar[int] = 4_000
    CATEGORY_OUTPUT_TOKENS: ClassVar[int] = 150
    LEVEL_OUTPUT_TOKENS: ClassVar[int] = 60
    GROUP_OUTPUT_TOKENS: ClassVar[int] = 300

    def __init__(self, *, target: PromptTarget, research_target: PromptTarget | None = None) -> None:
        """
        Configure the native targets for generation.

        Args:
            target (PromptTarget): Target for taxonomy and seed generation.
            research_target (PromptTarget | None): Optional separate target for behavior
                and dimension research. Explicit web search requires search support on it.

        Raises:
            ValueError: If a target lacks the required text or conversation capabilities.
        """
        super().__init__(context_type=AssertGeneratorContext)
        self._target = target
        self._research_target = research_target if research_target is not None else target
        for configured in (self._target, self._research_target):
            self._TARGET_REQUIREMENTS.validate(target=configured)
        self._normalizer = PromptNormalizer()
        self._prompts = _load_stage_prompts()
        self._adversarial_prompt = SeedPrompt.from_yaml_file(self._PROMPT_DIRECTORY / "adversarial_chat.yaml")

    async def execute_with_context_async(self, *, context: AssertGeneratorContext) -> AssertGeneratorResult:
        """
        Execute a request once through the standard strategy lifecycle: run every stage, then build the result.

        Returns:
            AssertGeneratorResult: The complete validated generation result.

        Raises:
            ValueError: If the context was already used, including after failure or cancellation.
        """
        if context._used:
            raise ValueError("AssertGeneratorContext is single-use; create a new context.")
        context._used = True
        return await super().execute_with_context_async(context=context)

    async def advance_async(
        self,
        *,
        checkpoint: AssertGenerationCheckpoint,
        stop_after: AssertGenerationStage | None = None,
        on_checkpoint: Callable[[AssertGenerationCheckpoint], Awaitable[None]] | None = None,
    ) -> AssertGenerationCheckpoint:
        """
        Run the remaining stages of a checkpoint, optionally stopping after one of them.

        Start from ``AssertGenerationCheckpoint(config=...)``. To review the policy, stop after
        ``AssertGenerationStage.DIMENSIONS``, apply ``with_reviewed_policy``, and call again.
        ``on_checkpoint`` receives the progress after each stage and each batch of seed groups,
        so a caller can store it and resume after an interruption.

        This returns progress rather than a result, so it runs setup and teardown but not the strategy
        events or retry collection of ``execute_async``. Before any model call, it checks the target of
        each role that has a stage left to run: the target must support the request's web search
        policy and, if the role already produced work, must be the target that produced it; only its rate
        limit may differ. ``build_result`` attributes work to the recorded targets.

        Args:
            checkpoint (AssertGenerationCheckpoint): The progress to continue from.
            stop_after (AssertGenerationStage | None): The last stage to run. Defaults to every stage.
            on_checkpoint (Callable[[AssertGenerationCheckpoint], Awaitable[None]] | None): Receives
                each new checkpoint.

        Returns:
            AssertGenerationCheckpoint: The progress after the last stage that ran.
        """
        checkpoint = AssertGenerationCheckpoint.model_validate(checkpoint)
        context = AssertGeneratorContext(config=checkpoint.config)
        context._used = True
        self._validate_web_search(checkpoint=checkpoint, stop_after=stop_after)
        for stage in _model_stages(checkpoint=checkpoint, stop_after=stop_after):
            self._require_producer(checkpoint=checkpoint, role=self._STAGE_ROLES[stage])
        async with self._execution_context_async(context):
            return await self._advance_async(
                context=context, checkpoint=checkpoint, stop_after=stop_after, on_checkpoint=on_checkpoint
            )

    def build_result(self, *, checkpoint: AssertGenerationCheckpoint) -> AssertGeneratorResult:
        """
        Package a complete checkpoint as native seed groups, without model calls.

        The result names the targets recorded in the checkpoint, which produced its work, rather than
        this generator's targets.

        Args:
            checkpoint (AssertGenerationCheckpoint): Progress with every requested seed group.

        Returns:
            AssertGeneratorResult: The complete validated generation result.

        Raises:
            ValueError: If stages remain.
        """
        checkpoint = AssertGenerationCheckpoint.model_validate(checkpoint)
        if checkpoint.next_stage is not None:
            raise ValueError(f"ASSERT generation is incomplete; the next stage is {checkpoint.next_stage.value!r}.")
        taxonomy, dimensions = _require_policy(checkpoint)
        return AssertGeneratorResult(
            config=checkpoint.config,
            systematization=checkpoint.systematization,
            taxonomy=taxonomy,
            dimensions=dimensions,
            seed_groups=tuple(
                self._build_seed_group(checkpoint=checkpoint, content=content) for content in checkpoint.groups
            ),
            coverage=AssertCoverage.from_assignments(
                levels=taxonomy.get_coverage_levels(dimensions),
                assignments=[content.assignment for content in checkpoint.groups],
            ),
            generation_conversation_ids=checkpoint.conversation_ids,
            generation_targets={
                role: identifier.model_dump(mode="json") for role, identifier in checkpoint.targets.items()
            },
        )

    @classmethod
    def estimate(cls, *, checkpoint: AssertGenerationCheckpoint) -> PromptGenerationEstimate:
        """
        Estimate the remaining model calls and tokens of a checkpoint without calling a target.

        Input tokens measure the request each stage sends, with typical output sizes in place of stage
        outputs that don't exist yet. Seed-group calls are exact once the taxonomy and dimension levels
        are known, generated or supplied in the request, because the batch plan is then known. Before
        that, they range from one call per five remaining groups (every group shares an assignment) to
        one call per remaining group (every group has its own assignment).

        Args:
            checkpoint (AssertGenerationCheckpoint): The progress to estimate from. Use
                ``AssertGenerationCheckpoint(config=...)`` for a new request.

        Returns:
            PromptGenerationEstimate: Remaining calls by stage and an approximate token range.

        Raises:
            ValueError: If the checkpoint's seed groups don't match the planned coverage assignments or use
                group IDs that the remaining batches assign.
        """
        checkpoint = AssertGenerationCheckpoint.model_validate(checkpoint)
        model_stages = _model_stages(checkpoint=checkpoint)
        stages = [
            cls._estimate_policy_stage(checkpoint=checkpoint, stage=stage)
            for stage in model_stages
            if stage != AssertGenerationStage.SEED_GROUPS
        ]
        exact = True
        if AssertGenerationStage.SEED_GROUPS in model_stages:
            group_stage, exact = cls._estimate_seed_groups(checkpoint=checkpoint)
            stages.append(group_stage)
        notes = ["JSON validation retries can add calls."]
        if checkpoint.config.web_search and any(cls._STAGE_ROLES[stage] == cls.RESEARCH_ROLE for stage in model_stages):
            notes.append("Web search adds provider-side usage to the research stages.")
        return PromptGenerationEstimate(stages=tuple(stages), exact_calls=exact, notes=tuple(notes))

    def validate_config(self, *, config: AssertGenerationConfig) -> None:
        """
        Check that the configured targets can enforce a request's web search policy, without model calls.

        The research target is checked only when the request runs a research stage, which a supplied
        taxonomy with explicit dimension levels skips.

        Args:
            config (AssertGenerationConfig): The request to check.

        Raises:
            ValueError: If the research target can't require or disable web search as requested,
                or the generation target can't disable it.
        """
        self._validate_web_search(checkpoint=AssertGenerationCheckpoint(config=config))

    def _validate_web_search(
        self, *, checkpoint: AssertGenerationCheckpoint, stop_after: AssertGenerationStage | None = None
    ) -> None:
        roles = {self._STAGE_ROLES[stage] for stage in _model_stages(checkpoint=checkpoint, stop_after=stop_after)}
        if self.GENERATION_ROLE in roles:
            self._target.validate_web_search_config(WebSearchConfig(mode=WebSearchMode.DISABLED))
        if self.RESEARCH_ROLE in roles:
            mode = WebSearchMode.REQUIRED if checkpoint.config.web_search else WebSearchMode.DISABLED
            self._research_target.validate_web_search_config(WebSearchConfig(mode=mode))

    def _validate_context(self, *, context: AssertGeneratorContext) -> None:
        context.config = AssertGenerationConfig.model_validate(context.config)
        self.validate_config(config=context.config)

    async def _setup_async(self, *, context: AssertGeneratorContext) -> None:
        pass

    async def _perform_async(self, *, context: AssertGeneratorContext) -> AssertGeneratorResult:
        checkpoint = await self._advance_async(
            context=context, checkpoint=AssertGenerationCheckpoint(config=context.config)
        )
        return self.build_result(checkpoint=checkpoint)

    async def _advance_async(
        self,
        *,
        context: AssertGeneratorContext,
        checkpoint: AssertGenerationCheckpoint,
        stop_after: AssertGenerationStage | None = None,
        on_checkpoint: Callable[[AssertGenerationCheckpoint], Awaitable[None]] | None = None,
    ) -> AssertGenerationCheckpoint:
        stages = list(AssertGenerationStage)
        while (stage := checkpoint.next_stage) is not None:
            if stop_after is not None and stages.index(stage) > stages.index(stop_after):
                break
            checkpoint = await self._run_stage_async(
                context=context, checkpoint=checkpoint, stage=stage, on_checkpoint=on_checkpoint
            )
        return checkpoint

    async def _run_stage_async(
        self,
        *,
        context: AssertGeneratorContext,
        checkpoint: AssertGenerationCheckpoint,
        stage: AssertGenerationStage,
        on_checkpoint: Callable[[AssertGenerationCheckpoint], Awaitable[None]] | None,
    ) -> AssertGenerationCheckpoint:
        if stage == AssertGenerationStage.SEED_GROUPS:
            return await self._generate_groups_async(
                context=context,
                checkpoint=checkpoint,
                producer=self._require_producer(checkpoint=checkpoint, role=self.GENERATION_ROLE),
                on_checkpoint=on_checkpoint,
            )
        if stage == AssertGenerationStage.SYSTEMATIZATION:
            producer = self._require_producer(checkpoint=checkpoint, role=self.RESEARCH_ROLE)
            systematization, conversation_id = await self._systematize_async(context=context)
            updated = _update_checkpoint(
                checkpoint,
                fields={"systematization": systematization},
                provenance={"systematization_conversation_id": conversation_id},
                producer=producer,
            )
        elif stage == AssertGenerationStage.TAXONOMY:
            producer = self._stage_producer(checkpoint=checkpoint, stage=stage)
            taxonomy, taxonomy_id = await self._resolve_taxonomy_async(context=context, checkpoint=checkpoint)
            updated = _update_checkpoint(
                checkpoint,
                fields={"taxonomy": taxonomy},
                provenance={"taxonomy_conversation_id": taxonomy_id},
                producer=producer,
            )
        else:
            if checkpoint.taxonomy is None:
                raise ValueError("Dimensions need a taxonomy.")
            producer = self._stage_producer(checkpoint=checkpoint, stage=stage)
            dimensions, dimensions_id = await self._resolve_dimensions_async(
                context=context, taxonomy=checkpoint.taxonomy
            )
            updated = _update_checkpoint(
                checkpoint,
                fields={"dimensions": dimensions},
                provenance={"dimensions_conversation_id": dimensions_id},
                producer=producer,
            )
        if on_checkpoint is not None:
            await on_checkpoint(updated)
        return updated

    def _stage_producer(
        self, *, checkpoint: AssertGenerationCheckpoint, stage: AssertGenerationStage
    ) -> tuple[str, TargetIdentifier] | None:
        if stage not in _model_stages(checkpoint=checkpoint):
            return None
        return self._require_producer(checkpoint=checkpoint, role=self._STAGE_ROLES[stage])

    def _require_producer(self, *, checkpoint: AssertGenerationCheckpoint, role: str) -> tuple[str, TargetIdentifier]:
        target = self._research_target if role == self.RESEARCH_ROLE else self._target
        identifier = TargetIdentifier.from_component_identifier(target.get_identifier())
        recorded = checkpoint.targets.get(role)
        if recorded is None:
            return role, identifier
        if _producer_hash(recorded) != _producer_hash(identifier):
            raise ValueError(
                f"This checkpoint's {role} work was produced by a different target "
                f"({recorded.class_name} {recorded.hash:.8}). Resume with the same {role} target, or start a new "
                "generation."
            )
        return role, recorded

    async def _teardown_async(self, *, context: AssertGeneratorContext) -> None:
        for target, conversation_id in context._conversations:
            try:
                async with asyncio.timeout(self._CLEANUP_TIMEOUT_SECONDS):
                    await target.reset_conversation_async(conversation_id=conversation_id)
            except Exception as error:  # cleanup must not replace a generation outcome
                self._logger.warning("Failed to reset ASSERT generation conversation %s: %s", conversation_id, error)

    async def _systematize_async(self, *, context: AssertGeneratorContext) -> tuple[AssertSystematization, str]:
        def parse(value: Any) -> AssertSystematization:
            result = AssertSystematization.model_validate(value)
            if result.behavior != context.config.behavior_name:
                raise ValueError("The systematization changed behavior_name.")
            return result

        return await self._generate_json_async(
            context=context,
            stage="systematization",
            target=self._research_target,
            payload=_systematization_payload(config=context.config),
            schema=AssertSystematization.model_json_schema(),
            parse=parse,
        )

    async def _resolve_taxonomy_async(
        self, *, context: AssertGeneratorContext, checkpoint: AssertGenerationCheckpoint
    ) -> tuple[AssertTaxonomy, str | None]:
        if context.config.taxonomy is not None:
            return context.config.taxonomy, None
        if checkpoint.systematization is None:
            raise ValueError("The taxonomy needs a systematization.")
        return await self._generate_taxonomy_async(context=context, systematization=checkpoint.systematization)

    async def _generate_taxonomy_async(
        self, *, context: AssertGeneratorContext, systematization: AssertSystematization
    ) -> tuple[AssertTaxonomy, str]:
        def parse(value: Any) -> AssertTaxonomy:
            result = AssertTaxonomy.model_validate(value)
            if result.behavior.name != context.config.behavior_name:
                raise ValueError("The taxonomy changed behavior_name.")
            return result

        return await self._generate_json_async(
            context=context,
            stage="taxonomy",
            target=self._target,
            payload=_taxonomy_payload(config=context.config, systematization=systematization.model_dump(mode="json")),
            schema=AssertTaxonomy.model_json_schema(),
            parse=parse,
        )

    async def _resolve_dimensions_async(
        self, *, context: AssertGeneratorContext, taxonomy: AssertTaxonomy
    ) -> tuple[tuple[AssertDimension, ...], str | None]:
        config = context.config
        if all(dimension.levels for dimension in config.dimensions):
            return config.dimensions, None

        def parse(value: Any) -> tuple[AssertDimension, ...]:
            dimensions = _DimensionsResponse.model_validate(value).dimensions
            if [item.name for item in dimensions] != [item.name for item in config.dimensions]:
                raise ValueError("Generated dimensions must match the requested names and order.")
            if any(len(item.levels) != config.dimension_level_count for item in dimensions):
                raise ValueError(f"Requested exactly {config.dimension_level_count} levels per dimension.")
            return dimensions

        return await self._generate_json_async(
            context=context,
            stage="dimensions",
            target=self._research_target,
            payload=_dimensions_payload(config=config, taxonomy=taxonomy.model_dump(mode="json")),
            schema=_DimensionsResponse.model_json_schema(),
            parse=parse,
        )

    async def _generate_groups_async(
        self,
        *,
        context: AssertGeneratorContext,
        checkpoint: AssertGenerationCheckpoint,
        producer: tuple[str, TargetIdentifier],
        on_checkpoint: Callable[[AssertGenerationCheckpoint], Awaitable[None]] | None,
    ) -> AssertGenerationCheckpoint:
        taxonomy, dimensions = _require_policy(checkpoint)
        batches = _plan_batches(
            config=checkpoint.config, taxonomy=taxonomy, dimensions=dimensions, batch_size=self._BATCH_SIZE
        )
        _require_planned_groups(checkpoint=checkpoint, batches=batches)
        group_ids = {group.group_id for group in checkpoint.groups}
        for assignment, count in batches[checkpoint.completed_batches :]:
            contents = await self._generate_batch_async(
                context=context,
                taxonomy=taxonomy,
                dimensions=dimensions,
                assignment=assignment,
                count=count,
                offset=len(checkpoint.groups),
            )
            checkpoint = _append_batch(checkpoint, contents=contents, group_ids=group_ids, producer=producer)
            if on_checkpoint is not None:
                await on_checkpoint(checkpoint)
        return checkpoint

    async def _generate_batch_async(
        self,
        *,
        context: AssertGeneratorContext,
        taxonomy: AssertTaxonomy,
        dimensions: tuple[AssertDimension, ...],
        assignment: dict[str, str],
        count: int,
        offset: int,
    ) -> list[AssertGeneratedGroupContent]:
        def parse(value: Any) -> tuple[_GeneratedGroup, ...]:
            contents = _GroupBatch.model_validate(value).seed_groups
            if len(contents) != count:
                raise ValueError(f"Requested {count} valid seed groups in this batch, received {len(contents)}.")
            for content in contents:
                if (content.user_prompt is not None) != context.config.include_user_prompt:
                    raise ValueError("Return exactly the requested user prompt input for every group.")
                if (content.adversarial_instructions is not None) != context.config.include_adversarial_prompt:
                    raise ValueError("Return exactly the requested adversarial instructions for every group.")
            return contents

        schema = _GroupBatch.model_json_schema()
        schema["properties"]["seed_groups"].update(minItems=count, maxItems=count)
        contents, conversation_id = await self._generate_json_async(
            context=context,
            stage="seed_groups",
            target=self._target,
            payload=_seed_groups_payload(
                config=context.config,
                taxonomy=taxonomy.model_dump(mode="json"),
                dimensions=[item.model_dump(mode="json") for item in dimensions],
                assignment=assignment,
                count=count,
            ),
            schema=schema,
            parse=parse,
        )
        return [
            AssertGeneratedGroupContent(
                group_id=_group_id(offset + index),
                assignment=assignment,
                conversation_id=conversation_id,
                **content.model_dump(),
            )
            for index, content in enumerate(contents)
        ]

    def _build_seed_group(
        self, *, checkpoint: AssertGenerationCheckpoint, content: AssertGeneratedGroupContent
    ) -> AttackSeedGroup:
        config = checkpoint.config
        taxonomy, dimensions = _require_policy(checkpoint)
        category = content.assignment["behavior"]
        metadata = AssertGroupMetadata(
            group_id=content.group_id,
            behavior_category=category,
            dimension_values={key: value for key, value in content.assignment.items() if key != "behavior"},
            tester_instructions=content.adversarial_instructions,
            config=config,
            taxonomy=taxonomy,
            dimensions=dimensions,
            provenance=checkpoint.provenance.model_copy(
                update={"group_generation_conversation_id": content.conversation_id}
            ),
        )
        common: dict[str, Any] = {
            "name": content.title,
            "data_type": "text",
            "origin": SeedOrigin.GENERATED,
            "source": AssertGroupMetadata.SOURCE_URL,
            "authors": ["Microsoft Responsible AI"],
            "harm_categories": [category],
            "is_jinja_template": False,
        }
        seeds: list[SeedUnion] = [SeedObjective(value=content.objective, metadata=metadata.to_metadata(), **common)]
        system_prompt = (
            config.target_system_prompt if config.target_system_prompt is not None else content.system_prompt
        )
        if config.target_owns_instructions:
            system_prompt = ""
        if system_prompt:
            seeds.append(SeedPrompt(value=system_prompt, role="system", sequence=0, **common))
        if content.user_prompt is not None:
            seeds.append(SeedPrompt(value=content.user_prompt, role="user", sequence=1, **common))
        adversarial_prompts = (
            AdversarialPrompts(
                system_prompt=self._adversarial_prompt.bind_template_parameters(
                    tester_instructions=content.adversarial_instructions
                ).model_copy(update={**common, "id": uuid.uuid4(), "description": content.adversarial_instructions})
            )
            if content.adversarial_instructions is not None
            else None
        )
        return AttackSeedGroup(seeds=seeds, adversarial_prompts=adversarial_prompts)

    async def _generate_json_async(
        self,
        *,
        context: AssertGeneratorContext,
        stage: str,
        target: PromptTarget,
        payload: dict[str, Any],
        schema: dict[str, Any],
        parse: Callable[[Any], T],
    ) -> tuple[T, str]:
        conversation_id = str(uuid.uuid4())
        context._conversations.append((target, conversation_id))
        await target.set_system_prompt_async(system_prompt=self._prompts[stage], conversation_id=conversation_id)
        message = Message.from_prompt(prompt=_request_text(config=context.config, payload=payload), role="user")
        message.message_pieces[0].prompt_metadata = {
            **JsonResponseConfig(json_schema=schema, schema_name=f"Assert_{stage}", strict=False).to_metadata(),
            **WebSearchConfig(
                mode=(
                    WebSearchMode.REQUIRED
                    if stage in {"systematization", "dimensions"} and context.config.web_search
                    else WebSearchMode.DISABLED
                )
            ).to_metadata(),
            "assert_stage": stage,
        }

        def parse_message(response: Message) -> T:
            return self._parse_json_response(response=response, stage=stage, parse=parse)

        result = await send_json_with_retry_async(
            normalizer=self._normalizer,
            target=target,
            message=message,
            conversation_id=conversation_id,
            parse=parse_message,
        )
        return result, conversation_id

    @staticmethod
    def _parse_json_response(*, response: Message, stage: str, parse: Callable[[Any], T]) -> T:
        errors = {piece.response_error for piece in response.message_pieces if piece.response_error != "none"}
        if errors:
            raise BadRequestException(message=f"ASSERT {stage}: target response error(s): {sorted(errors)}.")
        pieces = [piece for piece in response.message_pieces if piece.converted_value_data_type == "text"]
        if len(pieces) != 1:
            raise InvalidJsonException(message=f"ASSERT {stage}: expected one JSON text response.")
        try:
            return parse(json.loads(pieces[0].converted_value))
        except ValueError as error:
            raise InvalidJsonException(message=f"ASSERT {stage} validation failed: {error}") from error

    @classmethod
    def _estimate_policy_stage(
        cls, *, checkpoint: AssertGenerationCheckpoint, stage: AssertGenerationStage
    ) -> PromptGenerationStageEstimate:
        config = checkpoint.config
        if stage == AssertGenerationStage.SYSTEMATIZATION:
            payload = _systematization_payload(config=config)
            output_tokens = cls.SYSTEMATIZATION_OUTPUT_TOKENS
        elif stage == AssertGenerationStage.TAXONOMY:
            systematization = (
                checkpoint.systematization.model_dump(mode="json")
                if checkpoint.systematization is not None
                else _placeholder(tokens=cls.SYSTEMATIZATION_OUTPUT_TOKENS)
            )
            payload = _taxonomy_payload(config=config, systematization=systematization)
            output_tokens = config.behavior_category_count * cls.CATEGORY_OUTPUT_TOKENS + 200
        else:
            payload = _dimensions_payload(config=config, taxonomy=cls._estimated_taxonomy(checkpoint=checkpoint))
            output_tokens = len(config.dimensions) * config.dimension_level_count * cls.LEVEL_OUTPUT_TOKENS
        tokens = _input_tokens(config=config, stage=stage, payload=payload) + output_tokens
        return PromptGenerationStageEstimate(
            stage=stage.value,
            target_role=cls._STAGE_ROLES[stage],
            min_calls=1,
            max_calls=1,
            min_tokens=tokens,
            max_tokens=tokens,
        )

    @classmethod
    def _estimate_seed_groups(
        cls, *, checkpoint: AssertGenerationCheckpoint
    ) -> tuple[PromptGenerationStageEstimate, bool]:
        config = checkpoint.config
        remaining = config.group_count - len(checkpoint.groups)
        batches = cls._remaining_batches(checkpoint=checkpoint)
        if batches is not None:
            # Batches send the same request apart from their assignment and count.
            min_calls = max_calls = len(batches)
            assignment, count = batches[0]
        else:
            min_calls, max_calls = math.ceil(remaining / cls._BATCH_SIZE), remaining
            assignment = dict.fromkeys(("behavior", *(item.name for item in config.dimensions)), "")
            count = cls._BATCH_SIZE
        payload = _seed_groups_payload(
            config=config,
            taxonomy=cls._estimated_taxonomy(checkpoint=checkpoint),
            dimensions=cls._estimated_dimensions(checkpoint=checkpoint),
            assignment=assignment,
            count=count,
        )
        input_tokens = _input_tokens(config=config, stage=AssertGenerationStage.SEED_GROUPS, payload=payload)
        output_tokens = remaining * cls.GROUP_OUTPUT_TOKENS
        stage = PromptGenerationStageEstimate(
            stage=AssertGenerationStage.SEED_GROUPS.value,
            target_role=cls.GENERATION_ROLE,
            min_calls=min_calls,
            max_calls=max_calls,
            min_tokens=min_calls * input_tokens + output_tokens,
            max_tokens=max_calls * input_tokens + output_tokens,
        )
        return stage, batches is not None

    @classmethod
    def _remaining_batches(cls, *, checkpoint: AssertGenerationCheckpoint) -> list[tuple[dict[str, str], int]] | None:
        taxonomy, dimensions = _known_policy(checkpoint)
        if taxonomy is None or dimensions is None:
            return None
        batches = _plan_batches(
            config=checkpoint.config, taxonomy=taxonomy, dimensions=dimensions, batch_size=cls._BATCH_SIZE
        )
        _require_planned_groups(checkpoint=checkpoint, batches=batches)
        return batches[checkpoint.completed_batches :]

    @classmethod
    def _estimated_taxonomy(cls, *, checkpoint: AssertGenerationCheckpoint) -> Any:
        taxonomy, _ = _known_policy(checkpoint)
        if taxonomy is not None:
            return taxonomy.model_dump(mode="json")
        return _placeholder(tokens=checkpoint.config.behavior_category_count * cls.CATEGORY_OUTPUT_TOKENS)

    @classmethod
    def _estimated_dimensions(cls, *, checkpoint: AssertGenerationCheckpoint) -> Any:
        _, dimensions = _known_policy(checkpoint)
        if dimensions is not None:
            return [item.model_dump(mode="json") for item in dimensions]
        levels = _placeholder(tokens=checkpoint.config.dimension_level_count * cls.LEVEL_OUTPUT_TOKENS)
        return [{**item.model_dump(mode="json"), "levels": levels} for item in checkpoint.config.dimensions]


@functools.cache
def _load_stage_prompts() -> dict[str, str]:
    return {
        stage.value: SeedPrompt.from_yaml_file(AssertGenerator._PROMPT_DIRECTORY / f"{stage.value}.yaml").value
        for stage in AssertGenerationStage
    }


def _request_text(*, config: AssertGenerationConfig, payload: Mapping[str, Any]) -> str:
    """
    Serialize the user message of a stage: the request's behavior and context, then the stage's inputs.

    Returns:
        str: The JSON text sent to the target.
    """
    request = {
        "behavior_name": config.behavior_name,
        "behavior_description": config.behavior_description,
        "context": config.context,
        **payload,
    }
    return json.dumps(request, ensure_ascii=True)


def _systematization_payload(*, config: AssertGenerationConfig) -> dict[str, Any]:
    return {"web_search": config.web_search}


def _taxonomy_payload(*, config: AssertGenerationConfig, systematization: Any) -> dict[str, Any]:
    return {"systematization": systematization, "behavior_category_count": config.behavior_category_count}


def _dimensions_payload(*, config: AssertGenerationConfig, taxonomy: Any) -> dict[str, Any]:
    return {
        "taxonomy": taxonomy,
        "dimensions": [item.model_dump(mode="json") for item in config.dimensions],
        "level_count": config.dimension_level_count,
        "web_search": config.web_search,
    }


def _seed_groups_payload(
    *, config: AssertGenerationConfig, taxonomy: Any, dimensions: Any, assignment: Mapping[str, str], count: int
) -> dict[str, Any]:
    return {
        "taxonomy": taxonomy,
        "dimensions": dimensions,
        "assignment": dict(assignment),
        "count": count,
        "include_user_prompt": config.include_user_prompt,
        "include_adversarial_prompt": config.include_adversarial_prompt,
        "target_system_prompt": config.target_system_prompt,
        "target_owns_instructions": config.target_owns_instructions,
    }


def _input_tokens(*, config: AssertGenerationConfig, stage: AssertGenerationStage, payload: Mapping[str, Any]) -> int:
    characters = len(_load_stage_prompts()[stage.value]) + len(_request_text(config=config, payload=payload))
    return PromptGenerationEstimate.tokens_from_characters(characters)


def _placeholder(*, tokens: int) -> str:
    return "x" * (tokens * PromptGenerationEstimate.CHARACTERS_PER_TOKEN)


def _known_policy(
    checkpoint: AssertGenerationCheckpoint,
) -> tuple[AssertTaxonomy | None, tuple[AssertDimension, ...] | None]:
    """
    Find the taxonomy and dimension levels that exist so far, generated or supplied in the request.

    Returns:
        tuple[AssertTaxonomy | None, tuple[AssertDimension, ...] | None]: Each part, or None until it's generated.
    """
    config = checkpoint.config
    taxonomy = checkpoint.taxonomy if checkpoint.taxonomy is not None else config.taxonomy
    dimensions = checkpoint.dimensions
    if dimensions is None and all(dimension.levels for dimension in config.dimensions):
        dimensions = config.dimensions
    return taxonomy, dimensions


def _model_stages(
    *, checkpoint: AssertGenerationCheckpoint, stop_after: AssertGenerationStage | None = None
) -> list[AssertGenerationStage]:
    """
    List the remaining stages that call a model, in order, through stop_after.

    Returns:
        list[AssertGenerationStage]: Stages whose output is neither stored nor supplied in the request.
    """
    taxonomy, dimensions = _known_policy(checkpoint)
    calls_model = {
        AssertGenerationStage.SYSTEMATIZATION: taxonomy is None and checkpoint.systematization is None,
        AssertGenerationStage.TAXONOMY: taxonomy is None,
        AssertGenerationStage.DIMENSIONS: dimensions is None,
        AssertGenerationStage.SEED_GROUPS: len(checkpoint.groups) < checkpoint.config.group_count,
    }
    stages = list(AssertGenerationStage)
    last = stages.index(stop_after) if stop_after is not None else len(stages) - 1
    return [stage for stage in stages[: last + 1] if calls_model[stage]]


def _producer_hash(identifier: TargetIdentifier) -> str:
    """
    Hash a producer's identity without rate limits, a runtime setting that doesn't shape output.

    Returns:
        str: The content hash of the identifier with ``max_requests_per_minute`` removed from it and
            from every inner target.
    """
    return _without_rate_limits(identifier).hash


def _without_rate_limits(identifier: TargetIdentifier) -> TargetIdentifier:
    data = {**identifier.model_dump(), "max_requests_per_minute": None}
    if identifier.targets:
        data["targets"] = [_without_rate_limits(inner) for inner in identifier.targets]
    return TargetIdentifier.model_validate(data)


def _require_policy(checkpoint: AssertGenerationCheckpoint) -> tuple[AssertTaxonomy, tuple[AssertDimension, ...]]:
    if checkpoint.taxonomy is None or checkpoint.dimensions is None:
        raise ValueError("Seed groups need a taxonomy and dimensions.")
    return checkpoint.taxonomy, checkpoint.dimensions


def _require_planned_groups(
    *, checkpoint: AssertGenerationCheckpoint, batches: Sequence[tuple[dict[str, str], int]]
) -> None:
    done = checkpoint.completed_batches
    planned = [assignment for assignment, count in batches[:done] for _ in range(count)]
    if done > len(batches) or [group.assignment for group in checkpoint.groups] != planned:
        raise ValueError("The checkpoint's seed groups don't match the planned coverage assignments.")
    remaining_ids = {_group_id(index) for index in range(len(checkpoint.groups), checkpoint.config.group_count)}
    reserved = sorted(remaining_ids.intersection(group.group_id for group in checkpoint.groups))
    if reserved:
        raise ValueError(f"The checkpoint's seed groups use IDs {reserved} that the remaining batches assign.")


def _group_id(index: int) -> str:
    return f"group_{index:06d}"


def _update_checkpoint(
    checkpoint: AssertGenerationCheckpoint,
    *,
    fields: dict[str, Any],
    provenance: dict[str, str | None] | None = None,
    producer: tuple[str, TargetIdentifier] | None = None,
) -> AssertGenerationCheckpoint:
    data = {**checkpoint.model_dump(), **fields}
    if provenance:
        data["provenance"] = {**checkpoint.provenance.model_dump(), **provenance}
    if producer is not None:
        role, identifier = producer
        data["targets"] = {**checkpoint.targets, role: identifier}
    return AssertGenerationCheckpoint.model_validate(data)


def _append_batch(
    checkpoint: AssertGenerationCheckpoint,
    *,
    contents: Sequence[AssertGeneratedGroupContent],
    group_ids: set[str],
    producer: tuple[str, TargetIdentifier],
) -> AssertGenerationCheckpoint:
    """
    Append one planned batch without revalidating the stored groups.

    The plan supplies each assignment and the batch parser checks the prompt options, so only the new
    group IDs need a check. Entry points revalidate any checkpoint they receive.

    Returns:
        AssertGenerationCheckpoint: The progress with the batch, and ``group_ids`` with its IDs added.

    Raises:
        ValueError: If a new group ID repeats a stored one.
    """
    new_ids = {content.group_id for content in contents}
    if len(new_ids) != len(contents) or not group_ids.isdisjoint(new_ids):
        raise ValueError("Generated group IDs must be unique.")
    group_ids.update(new_ids)
    role, identifier = producer
    return checkpoint.model_copy(
        update={
            "groups": (*checkpoint.groups, *contents),
            "completed_batches": checkpoint.completed_batches + 1,
            "targets": {**checkpoint.targets, role: identifier},
        }
    )


def _plan_batches(
    *,
    config: AssertGenerationConfig,
    taxonomy: AssertTaxonomy,
    dimensions: tuple[AssertDimension, ...],
    batch_size: int,
) -> list[tuple[dict[str, str], int]]:
    assignments = _plan_assignments(
        levels=taxonomy.get_coverage_levels(dimensions),
        count=config.group_count,
        sampling=config.sampling,
        random_seed=config.random_seed,
    )
    return list(_iter_assignment_batches(assignments=assignments, batch_size=batch_size))


def _plan_assignments(
    *, levels: dict[str, list[str]], count: int, sampling: AssertSamplingConfig, random_seed: int
) -> list[dict[str, str]]:
    """
    Plan category and dimension assignments with a local random state.

    Returns:
        list[dict[str, str]]: Exactly count assignments.
    """
    rng = random.Random(random_seed)
    if sampling.method == AssertSamplingMethod.PAIRWISE:
        return _pairwise_assignments(levels=levels, count=count, rng=rng)
    if sampling.method == AssertSamplingMethod.STRATIFIED:
        return _stratified_assignments(levels=levels, count=count, stratify_by=sampling.stratify_by, rng=rng)
    if sampling.method == AssertSamplingMethod.RANDOM:
        return _random_assignments(levels=levels, count=count, with_replacement=sampling.with_replacement, rng=rng)
    return _full_factorial_assignments(levels=levels, count=count, replication=sampling.replication, rng=rng)


def _iter_assignment_batches(
    *, assignments: list[dict[str, str]], batch_size: int
) -> Iterator[tuple[dict[str, str], int]]:
    """
    Batch repeated assignments into bounded model requests.

    Yields:
        One assignment and the number of seed groups requested for that batch.
    """
    counts = Counter(tuple(assignment.items()) for assignment in assignments)
    for assignment, count in counts.items():
        for start in range(0, count, batch_size):
            yield dict(assignment), min(batch_size, count - start)


def _pairwise_assignments(*, levels: dict[str, list[str]], count: int, rng: random.Random) -> list[dict[str, str]]:
    covering_array = _build_pairwise_covering_array(levels=levels, rng=rng)
    rng.shuffle(covering_array)
    repetitions, remainder = divmod(count, len(covering_array))
    return [dict(row) for index, row in enumerate(covering_array) for _ in range(repetitions + (index < remainder))]


def _build_pairwise_covering_array(*, levels: dict[str, list[str]], rng: random.Random) -> list[dict[str, str]]:
    axes = tuple(levels)
    values = list(levels.values())
    if len(axes) == 1:
        return [{axes[0]: value} for value in values[0]]
    rows = [[left, right, *([""] * (len(axes) - 2))] for left in values[0] for right in values[1]]
    for column in range(2, len(axes)):
        uncovered = {(prior, old, new) for prior in range(column) for old in values[prior] for new in values[column]}
        # Extend existing rows with the next axis before adding rows for missing pairs.
        for row in rows:
            scores = {
                new: sum((prior, row[prior], new) in uncovered for prior in range(column)) for new in values[column]
            }
            best = max(scores.values())
            chosen = rng.choice([new for new, score in scores.items() if score == best])
            row[column] = chosen
            uncovered.difference_update((prior, row[prior], chosen) for prior in range(column))
        rows.extend(_rows_for_uncovered_pairs(uncovered=uncovered, values=values, column=column, rng=rng))
    return [dict(zip(axes, row, strict=True)) for row in rows]


def _rows_for_uncovered_pairs(
    *, uncovered: set[tuple[int, str, str]], values: list[list[str]], column: int, rng: random.Random
) -> list[list[str]]:
    rows: list[list[str]] = []
    for prior, old, new in sorted(uncovered):
        compatible = next((row for row in rows if row[column] == new and row[prior] in {"", old}), None)
        if compatible is not None:
            compatible[prior] = old
        else:
            row = [""] * len(values)
            row[column], row[prior] = new, old
            rows.append(row)
    for row in rows:
        for index in range(column + 1):
            if not row[index]:
                row[index] = rng.choice(values[index])
    return rows


def _stratified_assignments(
    *, levels: dict[str, list[str]], count: int, stratify_by: tuple[str, ...], rng: random.Random
) -> list[dict[str, str]]:
    strata = {axis: levels[axis] for axis in stratify_by}
    population = math.prod(map(len, strata.values()))
    if count < population:
        allocations = [(index, 1) for index in _sample_indices(population=population, count=count, rng=rng)]
    else:
        repetitions, remainder = divmod(count, population)
        extras = set(_sample_indices(population=population, count=remainder, rng=rng))
        allocations = [(index, repetitions + (index in extras)) for index in range(population)]
    other_axes = [axis for axis in levels if axis not in strata]
    rows = []
    for index, allocation in allocations:
        stratum = _decode_assignment(levels=strata, index=index)
        rows.extend({**stratum, **{axis: rng.choice(levels[axis]) for axis in other_axes}} for _ in range(allocation))
    rng.shuffle(rows)
    return rows


def _random_assignments(
    *, levels: dict[str, list[str]], count: int, with_replacement: bool, rng: random.Random
) -> list[dict[str, str]]:
    if with_replacement:
        return [{axis: rng.choice(values) for axis, values in levels.items()} for _ in range(count)]
    population = math.prod(map(len, levels.values()))
    if count > population:
        raise ValueError(f"Random sampling without replacement needs count <= factorial size ({population}).")
    return [
        _decode_assignment(levels=levels, index=index)
        for index in _sample_indices(population=population, count=count, rng=rng)
    ]


def _full_factorial_assignments(
    *, levels: dict[str, list[str]], count: int, replication: AssertReplication, rng: random.Random
) -> list[dict[str, str]]:
    population = math.prod(map(len, levels.values()))
    if replication == AssertReplication.NONE and count != population:
        raise ValueError(f"Full factorial without replication needs count == factorial size ({population}).")
    if count < population:
        raise ValueError(f"Full factorial needs count >= factorial size ({population}).")
    full = [dict(zip(levels, values, strict=True)) for values in product(*levels.values())]
    repetitions, remainder = divmod(count, population)
    rows = [dict(row) for row in full for _ in range(repetitions)]
    rows.extend(dict(full[index]) for index in _sample_indices(population=population, count=remainder, rng=rng))
    rng.shuffle(rows)
    return rows


def _decode_assignment(*, levels: dict[str, list[str]], index: int) -> dict[str, str]:
    row = {}
    for axis, values in reversed(tuple(levels.items())):
        index, level = divmod(index, len(values))
        row[axis] = values[level]
    return {axis: row[axis] for axis in levels}


def _sample_indices(*, population: int, count: int, rng: random.Random) -> list[int]:
    # Floyd's algorithm avoids allocating the full factorial population.
    selected: set[int] = set()
    for upper in range(population - count, population):
        candidate = rng.randrange(upper + 1)
        selected.add(upper if candidate in selected else candidate)
    result = sorted(selected)
    rng.shuffle(result)
    return result
