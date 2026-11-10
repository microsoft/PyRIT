# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
# Adapted from responsibleai/ASSERT v0.3.0 (2026-09-04).
# Source revision: 04b713fe73879208369750b3bd5ad53544f3eecc.

"""Typed inputs and generation data for ASSERT-derived seed groups."""

from __future__ import annotations

from enum import Enum, StrEnum
from itertools import combinations
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
_Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_Count = Annotated[int, Field(strict=True, ge=0, le=100_000)]


class _AssertModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class AssertSamplingMethod(str, Enum):
    """Assignment-selection methods supported by ASSERT."""

    PAIRWISE = "pairwise"
    STRATIFIED = "stratified"
    FULL_FACTORIAL = "full_factorial"
    RANDOM = "random"


class AssertReplication(str, Enum):
    """Replication policy for full-factorial assignments."""

    BALANCED = "balanced"
    NONE = "none"


class AssertSamplingConfig(_AssertModel):
    """Configuration for deterministic assignment selection."""

    method: AssertSamplingMethod = AssertSamplingMethod.PAIRWISE
    stratify_by: tuple[_Name, ...] = ("behavior",)
    replication: AssertReplication = AssertReplication.BALANCED
    with_replacement: StrictBool = True

    @model_validator(mode="after")
    def _validate_options(self) -> Self:
        if not self.stratify_by or len(set(self.stratify_by)) != len(self.stratify_by):
            raise ValueError("stratify_by must contain distinct, non-empty dimension names.")
        if self.method != AssertSamplingMethod.STRATIFIED and self.stratify_by != ("behavior",):
            raise ValueError("stratify_by is only supported for stratified sampling.")
        if self.method != AssertSamplingMethod.FULL_FACTORIAL and self.replication != AssertReplication.BALANCED:
            raise ValueError("replication is only supported for full_factorial sampling.")
        if self.method != AssertSamplingMethod.RANDOM and not self.with_replacement:
            raise ValueError("with_replacement is only supported for random sampling.")
        return self


class AssertDimensionLevel(_AssertModel):
    """One named level of a coverage dimension."""

    name: _Name
    definition: _Text


class AssertDimension(_AssertModel):
    """Explicit levels or an English description from which to generate levels."""

    name: _Name
    description: str = ""
    levels: tuple[AssertDimensionLevel, ...] = ()

    @model_validator(mode="after")
    def _validate_levels(self) -> Self:
        if self.name == "behavior":
            raise ValueError("The dimension name 'behavior' is reserved for the taxonomy axis.")
        if not self.levels and not self.description.strip():
            raise ValueError(f"Dimension {self.name!r} needs levels or a description.")
        if len(self.levels) == 1:
            raise ValueError(f"Dimension {self.name!r} must have at least two levels.")
        if len({level.name for level in self.levels}) != len(self.levels):
            raise ValueError(f"Dimension {self.name!r} contains duplicate level names.")
        return self


class AssertGenerationConfig(_AssertModel):
    """A serializable generation request, without targets or credentials."""

    behavior_name: _Name
    behavior_description: _Text
    context: str = ""
    group_count: Annotated[int, Field(strict=True, ge=1, le=100_000)]
    include_user_prompt: StrictBool = True
    include_adversarial_prompt: StrictBool = False
    behavior_category_count: Annotated[int, Field(strict=True, ge=1, le=1000)] = Field(
        default=25, description="Suggested taxonomy size, not a reason to add unsupported policy."
    )
    dimensions: tuple[AssertDimension, ...] = ()
    dimension_level_count: Annotated[int, Field(strict=True, ge=2, le=100)] = 3
    sampling: AssertSamplingConfig = Field(default_factory=AssertSamplingConfig)
    random_seed: Annotated[int, Field(strict=True)] = 0
    web_search: StrictBool = False
    target_system_prompt: str | None = None
    target_owns_instructions: StrictBool = False

    @model_validator(mode="after")
    def _validate_request(self) -> Self:
        if not self.include_user_prompt and not self.include_adversarial_prompt:
            raise ValueError("Request a user prompt, an adversarial prompt, or both.")
        names = [dimension.name for dimension in self.dimensions]
        if len(set(names)) != len(names):
            raise ValueError("Coverage dimension names must be unique.")
        explicit = [bool(dimension.levels) for dimension in self.dimensions]
        if any(explicit) and not all(explicit):
            raise ValueError("Use explicit levels for every dimension, or descriptions for every dimension.")
        if self.target_system_prompt is not None and not self.target_system_prompt.strip():
            raise ValueError("target_system_prompt must be non-empty when supplied.")
        if self.target_owns_instructions and self.target_system_prompt is not None:
            raise ValueError("A target-owned instruction mode cannot also supply target_system_prompt.")
        unknown = set(self.sampling.stratify_by) - {"behavior", *names}
        if self.sampling.method == AssertSamplingMethod.STRATIFIED and unknown:
            raise ValueError(f"Unknown stratify_by dimensions: {sorted(unknown)}.")
        return self


class AssertTerm(_AssertModel):
    """An operational definition used by a behavior taxonomy."""

    term: _Name
    definition: _Text
    examples: tuple[_Text, ...] = ()


class AssertBehaviorDefinition(_AssertModel):
    """The root behavior and its operational scope."""

    name: _Name
    definition: _Text


class AssertBehaviorCategory(_AssertModel):
    """An observable behavior with an explicit policy stance."""

    name: _Name
    definition: _Text
    examples: tuple[_Text, ...] = Field(min_length=1)
    permissible: StrictBool


class AssertTaxonomy(_AssertModel):
    """The complete policy used to generate seed groups."""

    behavior: AssertBehaviorDefinition
    definition_of_terms: tuple[AssertTerm, ...] = ()
    behavior_categories: tuple[AssertBehaviorCategory, ...] = Field(min_length=1)

    def get_coverage_levels(self, dimensions: tuple[AssertDimension, ...]) -> dict[str, list[str]]:
        """
        Describe the category and dimension levels available for generation assignments.

        Args:
            dimensions (tuple[AssertDimension, ...]): Resolved coverage dimensions.

        Returns:
            dict[str, list[str]]: Level names, with the taxonomy's behavior axis first.
        """
        return {
            "behavior": [category.name for category in self.behavior_categories],
            **{dimension.name: [level.name for level in dimension.levels] for dimension in dimensions},
        }

    @model_validator(mode="after")
    def _validate_categories(self) -> Self:
        names = [category.name for category in self.behavior_categories]
        if len(set(names)) != len(names):
            raise ValueError("Taxonomy behavior names must be unique.")
        return self


class AssertSlotValue(_AssertModel):
    """One concrete value of a concept-pattern slot."""

    slot_value: _Text
    definition: _Text
    example_phrase: _Text


class AssertSlot(_AssertModel):
    """A pattern component and its conditional refinements."""

    component: _Name
    slot_values: tuple[AssertSlotValue, ...] = Field(min_length=1)
    nested_slot_components: tuple[AssertSlot, ...] = ()


class AssertPatternRole(str, Enum):
    """The policy stance of an observable concept pattern."""

    ACCEPTABLE = "acceptable"
    PROBLEMATIC = "problematic"


class AssertConceptPattern(_AssertModel):
    """An observable pattern with theory, terms, and variable components."""

    pattern: _Text
    pattern_role: AssertPatternRole
    primary_theory: _Text
    related_theory: str
    key_terms: tuple[AssertTerm, ...]
    slot_components: tuple[AssertSlot, ...]


class AssertConceptSpecification(_AssertModel):
    """The root behavior and its acceptable and problematic patterns."""

    behavior: _Name
    patterns: tuple[AssertConceptPattern, ...] = Field(min_length=1)


class AssertStakeholder(_AssertModel):
    """One distinct perspective used in behavior research."""

    label: _Name
    expertise: _Text


class AssertSystematization(_AssertModel):
    """Research and concept specification preceding taxonomy conversion."""

    behavior: _Name
    scope: _Text
    impact_analysis: _Text
    alternative_systematizations: _Text
    references: tuple[_Text, ...]
    stakeholder_lenses: tuple[AssertStakeholder, ...]
    reasoning_summary: _Text
    concept_spec: AssertConceptSpecification

    @model_validator(mode="after")
    def _validate_patterns(self) -> Self:
        if self.concept_spec.behavior != self.behavior:
            raise ValueError("concept_spec.behavior must match the requested behavior.")
        return self


class AssertGenerationStage(StrEnum):
    """ASSERT generation stages, in execution order."""

    SYSTEMATIZATION = "systematization"
    TAXONOMY = "taxonomy"
    DIMENSIONS = "dimensions"
    SEED_GROUPS = "seed_groups"


class AssertGenerationProvenance(_AssertModel):
    """Research stages, the generation batch, and any human edits behind one seed group."""

    systematization_conversation_id: _Name | None = None
    taxonomy_conversation_id: _Name | None = None
    dimensions_conversation_id: _Name | None = None
    group_generation_conversation_id: _Name | None = None
    edited_stages: tuple[AssertGenerationStage, ...] = Field(
        default=(), description="Stages whose generated output a person revised before seed groups were generated."
    )

    @model_validator(mode="after")
    def _validate_edits(self) -> Self:
        if len(set(self.edited_stages)) != len(self.edited_stages):
            raise ValueError("edited_stages must not repeat a stage.")
        return self

    @property
    def conversation_ids(self) -> tuple[str, ...]:
        """At most four evidence references, independent of the group count."""
        return tuple(
            value
            for value in (
                self.systematization_conversation_id,
                self.taxonomy_conversation_id,
                self.dimensions_conversation_id,
                self.group_generation_conversation_id,
            )
            if value is not None
        )


class AssertCoverage(_AssertModel):
    """Measured level and pair coverage for one generated population."""

    sample_size: _Count
    unique_assignments: _Count
    total_levels: Annotated[int, Field(strict=True, ge=0)]
    covered_levels: Annotated[int, Field(strict=True, ge=0)]
    total_pairs: Annotated[int, Field(strict=True, ge=0)]
    covered_pairs: Annotated[int, Field(strict=True, ge=0)]

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        if (
            self.unique_assignments > self.sample_size
            or self.covered_levels > self.total_levels
            or self.covered_pairs > self.total_pairs
        ):
            raise ValueError("Coverage counts cannot exceed their populations.")
        return self

    @property
    def pairwise_complete(self) -> bool:
        """Whether all declared levels and pairs occur in the selected assignments."""
        return self.covered_levels == self.total_levels and self.covered_pairs == self.total_pairs

    @classmethod
    def from_assignments(cls, *, levels: Mapping[str, Sequence[str]], assignments: Sequence[Mapping[str, str]]) -> Self:
        """
        Measure actual assignments, including the behavior axis.

        Returns:
            Actual level and pair coverage.

        Raises:
            ValueError: If an assignment contains unknown dimensions or levels.
        """
        axes = tuple(levels)
        observed_levels: set[tuple[str, str]] = set()
        observed_pairs: set[tuple[str, str, str, str]] = set()
        unique: set[tuple[str, ...]] = set()
        for assignment in assignments:
            if set(assignment) != set(axes) or any(value not in levels[axis] for axis, value in assignment.items()):
                raise ValueError("An assignment contains an unknown dimension or level.")
            unique.add(tuple(assignment[axis] for axis in axes))
            observed_levels.update(assignment.items())
            observed_pairs.update(
                (left, assignment[left], right, assignment[right]) for left, right in combinations(axes, 2)
            )
        return cls(
            sample_size=len(assignments),
            unique_assignments=len(unique),
            total_levels=sum(map(len, levels.values())),
            covered_levels=len(observed_levels),
            total_pairs=sum(len(levels[left]) * len(levels[right]) for left, right in combinations(axes, 2)),
            covered_pairs=len(observed_pairs),
        )


class AssertGroupMetadata(_AssertModel):
    """Policy, coverage assignment, and bounded provenance for one generated group."""

    METADATA_KEY: ClassVar[str] = "assert_generation"
    SOURCE_REVISION: ClassVar[str] = "04b713fe73879208369750b3bd5ad53544f3eecc"
    SOURCE_REVISION_DATE: ClassVar[str] = "2026-09-04"
    SOURCE_URL: ClassVar[str] = "https://github.com/responsibleai/ASSERT"

    group_id: _Text
    behavior_category: _Name
    dimension_values: dict[_Name, _Name] = Field(default_factory=dict)
    config: AssertGenerationConfig
    taxonomy: AssertTaxonomy
    dimensions: tuple[AssertDimension, ...]
    provenance: AssertGenerationProvenance = Field(default_factory=AssertGenerationProvenance)
    source_revision: _Name = SOURCE_REVISION

    @model_validator(mode="after")
    def _validate_context(self) -> Self:
        if self.taxonomy.behavior.name != self.config.behavior_name:
            raise ValueError("The taxonomy behavior must match the generation request.")
        _validate_resolved_dimensions(config=self.config, dimensions=self.dimensions)
        levels = self.taxonomy.get_coverage_levels(self.dimensions)
        if set(self.dimension_values) != {dimension.name for dimension in self.dimensions} or any(
            value not in levels[name] for name, value in self.assignment.items()
        ):
            raise ValueError(f"Invalid coverage assignment for group {self.group_id!r}.")
        return self

    @property
    def assignment(self) -> dict[str, str]:
        """The taxonomy category and dimension levels assigned to this group."""
        return {"behavior": self.behavior_category, **self.dimension_values}

    def to_metadata(self) -> dict[str, Any]:
        """
        Serialize this value for seed metadata.

        Returns:
            The canonical metadata mapping containing this value.
        """
        return {self.METADATA_KEY: self.model_dump(mode="json")}

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> Self:
        """
        Validate a group's retained ASSERT generation context.

        Returns:
            The validated group metadata.

        Raises:
            ValueError: If the seed has no ASSERT metadata.
        """
        if metadata is None or cls.METADATA_KEY not in metadata:
            raise ValueError("The seed has no metadata for ASSERT generation.")
        return cls.model_validate(metadata[cls.METADATA_KEY])


class AssertGeneratedGroupContent(_AssertModel):
    """Model-written content and coverage assignment for one seed group, before it's packaged as seeds."""

    group_id: _Text
    assignment: dict[_Name, _Name]
    title: _Text
    objective: _Text
    user_prompt: _Text | None = None
    adversarial_instructions: _Text | None = None
    system_prompt: str = ""
    conversation_id: _Name


class AssertGenerationCheckpoint(_AssertModel):
    """
    The completed stages of one ASSERT generation request.

    A checkpoint lets generation stop after any stage for review, accept a revised taxonomy and
    dimensions, and resume after an interruption without repeating finished model calls. It records
    the target that produced each role's work, so packaging attributes it correctly and resuming
    can't silently mix models.
    """

    SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: Annotated[int, Field(strict=True)] = SCHEMA_VERSION
    config: AssertGenerationConfig
    targets: dict[_Name, dict[str, JsonValue]] = Field(
        default_factory=dict, description="Identifier of the target that produced each role's completed work"
    )
    systematization: AssertSystematization | None = None
    taxonomy: AssertTaxonomy | None = None
    dimensions: tuple[AssertDimension, ...] | None = None
    provenance: AssertGenerationProvenance = Field(default_factory=AssertGenerationProvenance)
    completed_batches: Annotated[int, Field(strict=True, ge=0)] = 0
    groups: tuple[AssertGeneratedGroupContent, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: int) -> int:
        if value != cls.SCHEMA_VERSION:
            raise ValueError(
                f"This checkpoint was saved in format version {value}, but this version of PyRIT reads version "
                f"{cls.SCHEMA_VERSION}. Start a new generation instead of resuming it."
            )
        return value

    @model_validator(mode="after")
    def _validate_progress(self) -> Self:
        behavior_name = self.config.behavior_name
        if self.systematization is not None and self.systematization.behavior != behavior_name:
            raise ValueError("The systematization behavior must match the generation request.")
        if self.taxonomy is not None and self.taxonomy.behavior.name != behavior_name:
            raise ValueError("The taxonomy behavior must match the generation request.")
        if self.taxonomy is not None and self.systematization is None:
            raise ValueError("A taxonomy needs the systematization it was converted from.")
        if self.dimensions is not None and self.taxonomy is None:
            raise ValueError("Dimensions need the taxonomy they describe.")
        if (self.groups or self.completed_batches) and (self.taxonomy is None or self.dimensions is None):
            raise ValueError("Seed groups need a taxonomy and dimensions.")
        if self.dimensions is not None:
            _validate_resolved_dimensions(config=self.config, dimensions=self.dimensions)
        self._validate_groups()
        return self

    def _validate_groups(self) -> None:
        if len(self.groups) > self.config.group_count:
            raise ValueError(f"Requested {self.config.group_count} seed groups, the checkpoint has {len(self.groups)}.")
        if len({group.group_id for group in self.groups}) != len(self.groups):
            raise ValueError("Generated group IDs must be unique.")
        if not self.groups or self.taxonomy is None or self.dimensions is None:
            return
        levels = self.taxonomy.get_coverage_levels(self.dimensions)
        for group in self.groups:
            if set(group.assignment) != set(levels) or any(
                value not in levels[name] for name, value in group.assignment.items()
            ):
                raise ValueError(f"Invalid coverage assignment for group {group.group_id!r}.")

    @property
    def next_stage(self) -> AssertGenerationStage | None:
        """The next stage to run, or None when every requested seed group exists."""
        if self.systematization is None:
            return AssertGenerationStage.SYSTEMATIZATION
        if self.taxonomy is None:
            return AssertGenerationStage.TAXONOMY
        if self.dimensions is None:
            return AssertGenerationStage.DIMENSIONS
        if len(self.groups) < self.config.group_count:
            return AssertGenerationStage.SEED_GROUPS
        return None

    @property
    def conversation_ids(self) -> tuple[str, ...]:
        """Every generation conversation the checkpoint refers to, in creation order."""
        ids = list(self.provenance.conversation_ids)
        ids.extend(group.conversation_id for group in self.groups)
        return tuple(dict.fromkeys(ids))

    def with_reviewed_policy(self, *, taxonomy: AssertTaxonomy, dimensions: Sequence[AssertDimension]) -> Self:
        """
        Replace the generated taxonomy and dimensions with reviewed versions.

        Edited dimensions become the request's explicit levels, so stored groups describe the
        dimensions they were generated from. The provenance records which stages a person edited.

        Args:
            taxonomy (AssertTaxonomy): The reviewed taxonomy. Its behavior name can't change.
            dimensions (Sequence[AssertDimension]): The reviewed dimensions, each with explicit levels.

        Returns:
            Self: A checkpoint ready to generate seed groups from the reviewed policy.

        Raises:
            ValueError: If the policy isn't generated yet, seed groups already exist, or the review is invalid.
        """
        if self.taxonomy is None or self.dimensions is None:
            raise ValueError("Review needs a generated taxonomy and dimensions.")
        if self.groups or self.completed_batches:
            raise ValueError("The policy can't change after seed groups have been generated.")
        reviewed_dimensions = tuple(dimensions)
        edited = set(self.provenance.edited_stages)
        config = self.config
        if taxonomy != self.taxonomy:
            edited.add(AssertGenerationStage.TAXONOMY)
        if reviewed_dimensions != self.dimensions:
            edited.add(AssertGenerationStage.DIMENSIONS)
            config = AssertGenerationConfig.model_validate(
                {**config.model_dump(), "dimensions": [item.model_dump() for item in reviewed_dimensions]}
            )
        provenance = self.provenance.model_copy(
            update={"edited_stages": tuple(stage for stage in AssertGenerationStage if stage in edited)}
        )
        return type(self).model_validate(
            {
                **self.model_dump(),
                "config": config,
                "taxonomy": taxonomy,
                "dimensions": reviewed_dimensions,
                "provenance": provenance,
            }
        )


class AssertBehaviorPreset(_AssertModel):
    """A ready-made behavior description from the bundled ASSERT behavior library."""

    name: _Name
    version: _Name
    tags: tuple[_Name, ...] = ()
    applicable_to: tuple[_Name, ...] = ()
    summary: _Text
    description: _Text
    suggested_judge_presets: tuple[_Name, ...] = ()


class AssertSuitePreset(_AssertModel):
    """An application context and the library behaviors to evaluate in it, one dataset per behavior."""

    name: _Name
    version: _Name
    tags: tuple[_Name, ...] = ()
    applicable_to: tuple[_Name, ...] = ()
    summary: _Text
    context: _Text
    behaviors: tuple[_Name, ...] = Field(min_length=1)
    suggested_judge_presets: tuple[_Name, ...] = ()

    @model_validator(mode="after")
    def _validate_behaviors(self) -> Self:
        if len(set(self.behaviors)) != len(self.behaviors):
            raise ValueError(f"Suite {self.name!r} lists a behavior more than once.")
        return self


def _validate_resolved_dimensions(*, config: AssertGenerationConfig, dimensions: tuple[AssertDimension, ...]) -> None:
    if [item.name for item in dimensions] != [item.name for item in config.dimensions]:
        raise ValueError("Resolved dimensions must match the requested dimensions in order.")
    for requested, resolved in zip(config.dimensions, dimensions, strict=True):
        if not resolved.levels:
            raise ValueError("Every resolved dimension needs explicit levels.")
        if requested.levels and requested.levels != resolved.levels:
            raise ValueError("Resolved levels must match explicitly configured dimension levels.")
        if not requested.levels and len(resolved.levels) != config.dimension_level_count:
            raise ValueError("Generated dimension levels must match dimension_level_count.")
