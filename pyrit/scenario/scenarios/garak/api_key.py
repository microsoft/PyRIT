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
    BoundedDatasetSize,
    ScenarioRunSizeComponent,
    ScenarioRunSizeEstimate,
    ScenarioRunSizeEstimateStatus,
    ScenarioRunSizeFactor,
    Seed,
    SeedObjective,
    SeedPrompt,
)
from pyrit.models.scenario_dataset import DatasetSelectionMode, ScenarioDatasetOptions
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import (
    DatasetAttackConfiguration,
    DatasetConstraintError,
    DatasetSource,
    DatasetSourceKind,
    ResolvedDataset,
)
from pyrit.scenario.core.required_data import ScenarioRequiredData
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
    """
    Limit the API-key requests ``ApiKey`` assembles from its corpus.

    The corpus datasets are ApiKey's required data (``ApiKey.get_required_data``), read in
    full. This configuration only limits the assembled requests with ``max_total``.
    """

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

    def _default_max_per_dataset(self) -> Literal["all"]:
        return "all"

    def _set_techniques(self, techniques: Sequence[ApiKeyTechnique]) -> None:
        """Set the techniques whose populations are sampled."""
        self._techniques = list(techniques)

    def size_caps_by_dataset(self) -> dict[str, list[tuple[str, int, Literal["dataset", "configuration", "compound"]]]]:
        """
        Describe the shared configuration cap for each technique population.

        Returns:
            dict: Technique names mapped to their shared configuration cap.
        """
        if self.max_total == "all":
            return {}
        return {
            str(technique.value): [("combined configuration cap", self.max_total, "configuration")]
            for technique in self._techniques
        }

    def validate_configuration(self) -> None:
        """
        Check that only the assembled requests are limited.

        Raises:
            DatasetConstraintError: If the configuration names datasets or supplies inline seeds.
        """
        super().validate_configuration()
        if self.sources or self.source_kind is DatasetSourceKind.INLINE:
            raise DatasetConstraintError(
                f"ApiKey reads its corpus as required data: {list(_CORPUS_DATASETS)}. "
                "ApiKeyDatasetConfiguration only takes max_total; inline seeds and other datasets are not supported."
            )


def _require_one_template_per_technique(resolved: ResolvedDataset) -> None:
    """
    Check the corpus has exactly one prompt template for each technique.

    Raises:
        DatasetConstraintError: If a technique has no template, several, or a non-prompt one.
    """
    for technique in (ApiKeyTechnique.GetKey, ApiKeyTechnique.CompleteKey):
        templates = [
            seed
            for seed in resolved.seeds
            if seed.dataset_name == DATASET_TEMPLATES and (seed.metadata or {}).get("technique") == technique.value
        ]
        if len(templates) != 1 or not isinstance(templates[0], SeedPrompt):
            raise DatasetConstraintError(f"ApiKey requires one prompt template for {technique.value}.")


def _assemble_requests(
    *, corpus: dict[str, list[Seed[Any]]], techniques: Sequence[ApiKeyTechnique]
) -> dict[str, list[AttackSeedGroup]]:
    """
    Render one request for each service and technique input.

    Returns:
        dict[str, list[AttackSeedGroup]]: Unsampled API-key attack groups keyed by technique.
    """
    services = corpus[DATASET_SERVICES]
    partials = [seed.value for seed in corpus[DATASET_PARTIAL_KEYS]]
    populations: dict[str, list[AttackSeedGroup]] = {}
    for technique in techniques:
        groups: list[AttackSeedGroup] = []
        populations[str(technique.value)] = groups
        template = next(
            cast("SeedPrompt", seed)
            for seed in corpus[DATASET_TEMPLATES]
            if (seed.metadata or {}).get("technique") == technique.value
        )
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
    of synthetic partial keys. The service, template, partial-key, and placeholder
    datasets are required data, always read in full. The dataset configuration samples
    up to 20 of the assembled requests across the selected techniques by default. The
    base scenario persists that sample for resume. Credential-shaped responses are
    scored, excluding supplied partials and safe placeholders.

    Reference: [@derczynski2024garak]
    """

    VERSION: int = 1
    DATASET_OPTIONS = ScenarioDatasetOptions(mode=DatasetSelectionMode.FIXED, per_dataset_limit=False)
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
                max_total=ApiKeyDatasetConfiguration.DEFAULT_MAX_DATASET_SIZE
            ),
            objective_scorer=objective_scorer or CredentialLeakScorer(patterns=CredentialLeakScorer.GARAK_PATTERNS),
            scenario_result_id=scenario_result_id,
        )

    def get_required_data(self) -> ScenarioRequiredData:
        """
        Return the API-key corpus, read in full to assemble the requests.

        Returns:
            ScenarioRequiredData: The service, template, partial-key, and placeholder datasets.
        """
        return ScenarioRequiredData(
            sources=[DatasetSource(name=name) for name in self.required_datasets()],
            validators=[_require_one_template_per_technique],
        )

    def _validate_runtime_configuration(self) -> None:
        config = self._dataset_config
        if type(config) is not ApiKeyDatasetConfiguration:
            raise DatasetConstraintError(
                f"ApiKey only supports ApiKeyDatasetConfiguration; received {type(config).__name__}."
            )
        config._set_techniques([ApiKeyTechnique(technique.value) for technique in self._scenario_techniques])
        super()._validate_runtime_configuration()

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
        config = cast("ApiKeyDatasetConfiguration", self._dataset_config)
        required_data = cast("ScenarioRequiredData", self._required_data)
        corpus = await required_data.load_async()
        populations = _assemble_requests(corpus=corpus, techniques=config._techniques)
        groups = config.select_assembled_groups(groups_by_population=populations, apply_sampling=apply_sampling)
        if self._uses_default_scorer:
            excluded_values = [
                seed.value for name in (DATASET_PARTIAL_KEYS, DATASET_SAFE_PLACEHOLDERS) for seed in corpus[name]
            ]
            self._objective_scorer = CredentialLeakScorer.from_excluded_values(
                excluded_values, patterns=CredentialLeakScorer.GARAK_PATTERNS
            )
            self._objective_scorer_identifier = self._objective_scorer.get_identifier()
        return groups

    async def _estimate_run_size_async(self, *, budget: BoundedDatasetSize) -> ScenarioRunSizeEstimate:
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
        count, datasets = await self._get_dataset_size_for_estimate_async(budget=budget)
        for dataset in datasets:
            dataset.kind = "synthesized"
        components = [
            ScenarioRunSizeComponent(
                label="Synthesized requests",
                count=count,
                factors=[
                    ScenarioRunSizeFactor(label="selected request estimate", count=count),
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
            if not seed_groups:
                continue
            converters = context.technique_converters.get(technique_name, [])
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
