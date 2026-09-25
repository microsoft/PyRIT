# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
#
# The probing structure below is adapted and modified from NVIDIA Garak
# commit 3f50ea5ff9cd7050099940647c15c39b07a93392 (Apache-2.0).
# Garak Copyright (c) 2023 Leon Derczynski.
# Garak Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES.
# These portions were modified by Microsoft Corporation.
# See THIRD_PARTY_NOTICES.txt for full notices.

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, cast

from pyrit.common import apply_defaults, forward_init_parameters
from pyrit.executor.attack import AttackConverterConfig, AttackScoringConfig, PromptSendingAttack
from pyrit.models import (
    AttackSeedGroup,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateStatus,
    ScenarioRunSizeFactor,
    Seed,
    SeedObjective,
    SeedPrompt,
)
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import (
    DatasetAttackConfiguration,
    DatasetConstraintError,
    ResolvedDataset,
)
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.score import CredentialLeakScorer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Literal

    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.score import TrueFalseScorer

DATASET_SERVICES = "garak_api_key_services"
DATASET_TEMPLATES = "garak_api_key_templates"
DATASET_PARTIAL_KEYS = "garak_api_key_partial_keys"
DATASET_SAFE_PLACEHOLDERS = "garak_api_key_safe_placeholders"

_CORPUS_DATASETS: tuple[str, ...] = (
    DATASET_SERVICES,
    DATASET_TEMPLATES,
    DATASET_PARTIAL_KEYS,
    DATASET_SAFE_PLACEHOLDERS,
)


class ApiKeyTechnique(ScenarioTechnique):
    """Garak API-key elicitation techniques."""

    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})

    GetKey = ("get_key", {"default"})
    CompleteKey = ("complete_key", {"default"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """Return aggregate technique tags."""
        return {"all", "default"}

    @classmethod
    def default(cls) -> ApiKeyTechnique:
        """Return both Garak techniques by default."""
        return cls.DEFAULT


class ApiKeyDatasetConfiguration(DatasetAttackConfiguration):
    """Build API-key attack groups before standard dataset sampling."""

    DEFAULT_MAX_DATASET_SIZE: ClassVar[int] = 20

    @forward_init_parameters
    def __init__(self, **kwargs: Any) -> None:
        """
        Initialize the configuration.

        Args:
            **kwargs (Any): Arguments for ``DatasetAttackConfiguration``.
        """
        super().__init__(**kwargs)
        self._techniques: list[ApiKeyTechnique] = [ApiKeyTechnique.GetKey, ApiKeyTechnique.CompleteKey]
        self.excluded_values: tuple[str, ...] = ()

    def _set_techniques(self, techniques: Sequence[ApiKeyTechnique]) -> None:
        """Set the techniques whose populations are sampled."""
        self._techniques = list(techniques)

    def size_caps_by_dataset(self) -> dict[str, list[tuple[str, int, Literal["dataset", "configuration", "compound"]]]]:
        """
        Describe the shared configuration cap for each technique population.

        Returns:
            dict: Technique names mapped to their shared configuration cap.
        """
        if self.max_dataset_size is None:
            return {}
        return {
            str(technique.value): [("combined configuration cap", self.max_dataset_size, "configuration")]
            for technique in self._techniques
        }

    async def _build_groups_by_dataset_async(self) -> tuple[dict[str, list[AttackSeedGroup]], ResolvedDataset]:
        """
        Resolve the corpus and group requests by technique.

        Returns:
            tuple: Technique populations and the raw dataset for validation.

        Raises:
            DatasetConstraintError: If the configuration does not select the required corpus.
        """
        if set(self.dataset_names) != set(_CORPUS_DATASETS):
            raise DatasetConstraintError(
                f"ApiKey requires exactly these datasets: {list(_CORPUS_DATASETS)}; inline seeds are not supported."
            )
        seeds_by_dataset = await self._collect_named_seeds_async()
        seeds = [seed for population in seeds_by_dataset.values() for seed in population]
        self.excluded_values = tuple(
            seed.value for name in (DATASET_PARTIAL_KEYS, DATASET_SAFE_PLACEHOLDERS) for seed in seeds_by_dataset[name]
        )
        populations = self._build_technique_populations(seeds)
        return populations, ResolvedDataset(
            seeds=seeds, source_kind=self.source_kind, dataset_names=tuple(self.dataset_names)
        )

    def _build_technique_populations(self, seeds: list[Seed]) -> dict[str, list[AttackSeedGroup]]:
        """
        Render one request for each selected service and technique input.

        Returns:
            dict[str, list[AttackSeedGroup]]: Unsampled API-key attack groups keyed by technique.

        Raises:
            DatasetConstraintError: If a selected technique has no unique prompt template.
        """
        services = [seed for seed in seeds if seed.dataset_name == DATASET_SERVICES]
        partials = [seed.value for seed in seeds if seed.dataset_name == DATASET_PARTIAL_KEYS]
        populations: dict[str, list[AttackSeedGroup]] = {}
        for technique in self._techniques:
            groups: list[AttackSeedGroup] = []
            populations[str(technique.value)] = groups
            templates = [
                seed
                for seed in seeds
                if seed.dataset_name == DATASET_TEMPLATES and (seed.metadata or {}).get("technique") == technique.value
            ]
            if len(templates) != 1 or not isinstance(templates[0], SeedPrompt):
                raise DatasetConstraintError(f"ApiKey requires one prompt template for {technique.value}.")
            template = templates[0]
            for service in services:
                for partial in partials if technique is ApiKeyTechnique.CompleteKey else [""]:
                    prompt = template.model_copy(deep=True)
                    prompt.value = template.render_template_value(key=service.value, partial=partial)
                    groups.append(
                        AttackSeedGroup(
                            seeds=[
                                SeedObjective(
                                    value=f"Elicit a {service.value} credential in response to: {prompt.value}",
                                    source=service.source,
                                    harm_categories=service.harm_categories,
                                    metadata={"technique": technique.value, "service": service.value},
                                ),
                                prompt,
                            ]
                        )
                    )
        return populations


class ApiKey(Scenario):
    """
    Exercise a model's tendency to generate or complete API credentials.

    GetKey requests a credential for each service. CompleteKey requests completion
    of synthetic partial keys. The dataset configuration samples up to 20 requests
    across the selected techniques by default. The base scenario persists that sample
    for resume. Credential-shaped responses are scored, excluding supplied partials
    and safe placeholders.

    Reference: [@derczynski2024garak]
    """

    VERSION: int = 1
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return the local Garak API-key corpus datasets."""
        return list(_CORPUS_DATASETS)

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the API-key scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Optional scorer override.
            scenario_result_id (str | None): Optional existing scenario result to resume.
        """
        self._uses_default_scorer = objective_scorer is None
        super().__init__(
            version=self.VERSION,
            technique_class=ApiKeyTechnique,
            default_dataset_config=ApiKeyDatasetConfiguration(
                dataset_names=self.required_datasets(),
                max_dataset_size=ApiKeyDatasetConfiguration.DEFAULT_MAX_DATASET_SIZE,
            ),
            objective_scorer=objective_scorer or CredentialLeakScorer(patterns=CredentialLeakScorer.GARAK_PATTERNS),
            scenario_result_id=scenario_result_id,
        )

    async def _resolve_seed_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        """
        Resolve and sample the selected technique populations.

        Returns:
            dict[str, list[AttackSeedGroup]]: Requests grouped by technique.

        Raises:
            DatasetConstraintError: If an unsupported configuration is supplied.
        """
        config = self._dataset_config
        if type(config) is not ApiKeyDatasetConfiguration:
            raise DatasetConstraintError(
                f"ApiKey only supports ApiKeyDatasetConfiguration; received {type(config).__name__}."
            )
        config._set_techniques([ApiKeyTechnique(technique.value) for technique in self._scenario_techniques])
        groups = await config.get_attack_groups_by_dataset_async(apply_sampling=apply_sampling)
        if self._uses_default_scorer:
            self._objective_scorer = CredentialLeakScorer.from_excluded_values(
                config.excluded_values, patterns=CredentialLeakScorer.GARAK_PATTERNS
            )
            self._objective_scorer_identifier = self._objective_scorer.get_identifier()
        return groups

    async def _estimate_run_size_async(self) -> ScenarioRunSizeEstimate:
        """
        Count each synthesized request once rather than crossing techniques again.

        Returns:
            ScenarioRunSizeEstimate: The selected request count.

        Raises:
            DatasetConstraintError: If the dataset configuration is not supported.
        """
        config = self._dataset_config
        if not isinstance(config, ApiKeyDatasetConfiguration):
            raise DatasetConstraintError("ApiKey requires an ApiKeyDatasetConfiguration.")
        config._set_techniques([ApiKeyTechnique(technique.value) for technique in self._scenario_techniques])
        count, datasets = self._get_dataset_budget_for_estimate()
        for dataset in datasets:
            dataset.kind = "synthesized"
        components = [
            ScenarioRunSizeComponent(
                label="Synthesized request budget",
                count=count,
                factors=[
                    ScenarioRunSizeFactor(label="combined request cap", count=count),
                ],
            )
        ]
        return ScenarioRunSizeEstimate(
            status=ScenarioRunSizeEstimateStatus.Exact,
            total_attack_count=sum(component.count for component in components),
            components=components,
            datasets=datasets,
        )

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build one prompt-sending attack per sampled technique.

        Returns:
            list[AtomicAttack]: Attacks with the selected requests and converter stacks.
        """
        attacks: list[AtomicAttack] = []
        for technique_name, seed_groups in context.seed_groups_by_dataset.items():
            converters = self._technique_converters.get(technique_name, [])
            converter_config = (
                AttackConverterConfig(
                    request_converters=ConverterConfiguration.from_converters(converters=list(converters))
                )
                if converters
                else None
            )
            attacks.append(
                AtomicAttack(
                    atomic_attack_name=technique_name,
                    attack_technique=AttackTechnique(
                        attack=PromptSendingAttack(
                            objective_target=context.objective_target,
                            attack_converter_config=converter_config,
                            attack_scoring_config=AttackScoringConfig(
                                objective_scorer=cast("TrueFalseScorer", self._objective_scorer)
                            ),
                        )
                    ),
                    seed_groups=seed_groups,
                    memory_labels=context.memory_labels,
                )
            )
        return attacks
