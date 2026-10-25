# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Adapted from responsibleai/ASSERT v0.3.0 (2026-09-04).
# Source revision: 04b713fe73879208369750b3bd5ad53544f3eecc.

"""Generate native attack seed groups with ASSERT's taxonomy and coverage method."""

from __future__ import annotations

import asyncio
import json
import math
import random
import uuid
from collections import Counter
from dataclasses import dataclass, field
from itertools import product
from typing import TYPE_CHECKING, Annotated, Any, Self, TypeVar

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
    AssertGenerationConfig,
    AssertGenerationProvenance,
    AssertGroupMetadata,
    AssertReplication,
    AssertSamplingConfig,
    AssertSamplingMethod,
    AssertSystematization,
    AssertTaxonomy,
    AttackSeedGroup,
    JsonResponseConfig,
    Message,
    SeedObjective,
    SeedOrigin,
    SeedPrompt,
    WebSearchConfig,
    WebSearchMode,
)
from pyrit.prompt_normalizer import PromptNormalizer, send_json_with_retry_async
from pyrit.prompt_target import CapabilityName
from pyrit.prompt_target.common.target_requirements import TargetRequirements

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

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
    _shared_provenance: AssertGenerationProvenance = field(
        default_factory=AssertGenerationProvenance, init=False, repr=False
    )


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
        self._prompts = {
            stage: SeedPrompt.from_yaml_file(self._PROMPT_DIRECTORY / f"{stage}.yaml").value
            for stage in ("systematization", "taxonomy", "dimensions", "seed_groups")
        }
        self._adversarial_prompt = SeedPrompt.from_yaml_file(self._PROMPT_DIRECTORY / "adversarial_chat.yaml")

    async def execute_with_context_async(self, *, context: AssertGeneratorContext) -> AssertGeneratorResult:
        """
        Execute a request once through the standard strategy lifecycle.

        Returns:
            AssertGeneratorResult: The complete validated generation result.

        Raises:
            ValueError: If the context was already used, including after failure or cancellation.
        """
        if context._used:
            raise ValueError("AssertGeneratorContext is single-use; create a new context.")
        context._used = True
        return await super().execute_with_context_async(context=context)

    def _validate_context(self, *, context: AssertGeneratorContext) -> None:
        context.config = AssertGenerationConfig.model_validate(context.config)
        self._target.validate_web_search_config(WebSearchConfig(mode=WebSearchMode.DISABLED))
        mode = WebSearchMode.REQUIRED if context.config.web_search else WebSearchMode.DISABLED
        self._research_target.validate_web_search_config(WebSearchConfig(mode=mode))

    async def _setup_async(self, *, context: AssertGeneratorContext) -> None:
        pass

    async def _perform_async(self, *, context: AssertGeneratorContext) -> AssertGeneratorResult:
        systematization, research_id = await self._systematize_async(context=context)
        taxonomy, taxonomy_id = await self._generate_taxonomy_async(context=context, systematization=systematization)
        dimensions, dimensions_id = await self._resolve_dimensions_async(context=context, taxonomy=taxonomy)
        context._shared_provenance = AssertGenerationProvenance(
            systematization_conversation_id=research_id,
            taxonomy_conversation_id=taxonomy_id,
            dimensions_conversation_id=dimensions_id,
        )
        groups = await self._generate_groups_async(context=context, taxonomy=taxonomy, dimensions=dimensions)
        levels = taxonomy.get_coverage_levels(dimensions)
        return AssertGeneratorResult(
            config=context.config,
            systematization=systematization,
            taxonomy=taxonomy,
            dimensions=dimensions,
            seed_groups=groups,
            coverage=AssertCoverage.from_assignments(
                levels=levels,
                assignments=[
                    AssertGroupMetadata.from_metadata(group.objective.metadata).assignment for group in groups
                ],
            ),
            generation_conversation_ids=tuple(conversation_id for _, conversation_id in context._conversations),
            generation_targets={
                "generation": self._target.get_identifier().model_dump(mode="json"),
                "research": self._research_target.get_identifier().model_dump(mode="json"),
            },
        )

    async def _teardown_async(self, *, context: AssertGeneratorContext) -> None:
        for target, conversation_id in context._conversations:
            try:
                async with asyncio.timeout(self._CLEANUP_TIMEOUT_SECONDS):
                    await target.reset_conversation_async(conversation_id=conversation_id)
            except Exception as error:  # noqa: BLE001 - cleanup must not replace a generation outcome
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
            payload={"web_search": context.config.web_search},
            schema=AssertSystematization.model_json_schema(),
            parse=parse,
        )

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
            payload={
                "systematization": systematization.model_dump(mode="json"),
                "behavior_category_count": context.config.behavior_category_count,
            },
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
            payload={
                "taxonomy": taxonomy.model_dump(mode="json"),
                "dimensions": [item.model_dump(mode="json") for item in config.dimensions],
                "level_count": config.dimension_level_count,
                "web_search": config.web_search,
            },
            schema=_DimensionsResponse.model_json_schema(),
            parse=parse,
        )

    async def _generate_groups_async(
        self, *, context: AssertGeneratorContext, taxonomy: AssertTaxonomy, dimensions: tuple[AssertDimension, ...]
    ) -> tuple[AttackSeedGroup, ...]:
        config = context.config
        assignments = _plan_assignments(
            levels=taxonomy.get_coverage_levels(dimensions),
            count=config.group_count,
            sampling=config.sampling,
            random_seed=config.random_seed,
        )
        groups: list[AttackSeedGroup] = []
        for assignment, count in _iter_assignment_batches(assignments=assignments, batch_size=self._BATCH_SIZE):
            groups.extend(
                await self._generate_batch_async(
                    context=context,
                    taxonomy=taxonomy,
                    dimensions=dimensions,
                    assignment=assignment,
                    count=count,
                    offset=len(groups),
                )
            )
        return tuple(groups)

    async def _generate_batch_async(
        self,
        *,
        context: AssertGeneratorContext,
        taxonomy: AssertTaxonomy,
        dimensions: tuple[AssertDimension, ...],
        assignment: dict[str, str],
        count: int,
        offset: int,
    ) -> list[AttackSeedGroup]:
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
            payload={
                "taxonomy": taxonomy.model_dump(mode="json"),
                "dimensions": [item.model_dump(mode="json") for item in dimensions],
                "assignment": assignment,
                "count": count,
                "include_user_prompt": context.config.include_user_prompt,
                "include_adversarial_prompt": context.config.include_adversarial_prompt,
                "target_system_prompt": context.config.target_system_prompt,
                "target_owns_instructions": context.config.target_owns_instructions,
            },
            schema=schema,
            parse=parse,
        )
        return [
            self._build_seed_group(
                context=context,
                taxonomy=taxonomy,
                dimensions=dimensions,
                content=content,
                assignment=assignment,
                group_id=f"group_{offset + index:06d}",
                conversation_id=conversation_id,
            )
            for index, content in enumerate(contents)
        ]

    def _build_seed_group(
        self,
        *,
        context: AssertGeneratorContext,
        taxonomy: AssertTaxonomy,
        dimensions: tuple[AssertDimension, ...],
        content: _GeneratedGroup,
        assignment: dict[str, str],
        group_id: str,
        conversation_id: str,
    ) -> AttackSeedGroup:
        config = context.config
        metadata = AssertGroupMetadata(
            group_id=group_id,
            behavior_category=assignment["behavior"],
            dimension_values={key: value for key, value in assignment.items() if key != "behavior"},
            config=config,
            taxonomy=taxonomy,
            dimensions=dimensions,
            provenance=context._shared_provenance.model_copy(
                update={"group_generation_conversation_id": conversation_id}
            ),
        )
        common: dict[str, Any] = {
            "name": content.title,
            "data_type": "text",
            "origin": SeedOrigin.GENERATED,
            "source": AssertGroupMetadata.SOURCE_URL,
            "authors": ["Microsoft Responsible AI"],
            "harm_categories": [assignment["behavior"]],
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
        request = {
            "behavior_name": context.config.behavior_name,
            "behavior_description": context.config.behavior_description,
            "context": context.config.context,
            **payload,
        }
        message = Message.from_prompt(prompt=json.dumps(request, ensure_ascii=True), role="user")
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
