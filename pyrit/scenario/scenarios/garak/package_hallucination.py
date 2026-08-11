# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, cast

from pyrit.common import apply_defaults
from pyrit.executor.attack.core.attack_config import AttackScoringConfig
from pyrit.executor.attack.single_turn.prompt_sending import PromptSendingAttack
from pyrit.memory import CentralMemory
from pyrit.models import AttackSeedGroup, SeedObjective, SeedPrompt
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration
from pyrit.scenario.core.scenario import BaselineAttackPolicy, Scenario
from pyrit.scenario.core.scenario_technique import ScenarioTechnique
from pyrit.score.true_false.regex.package_hallucination_scorer import (
    PackageEcosystem,
    PackageHallucinationScorer,
)

if TYPE_CHECKING:
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt corpus datasets (local ``.prompt`` files under datasets/seed_datasets/local/garak).
# Ported verbatim from garak ``probes/packagehallucination.py``. Each rendered prompt is
# ``stub.replace("<language>", ...).replace("<task>", ...)``. The stub templates and the
# real/unreal code tasks live in datasets (owned by the loaders), not in scenario code.
# ---------------------------------------------------------------------------
DATASET_STUBS = "garak_package_hallucination_stubs"
DATASET_REAL_TASKS = "garak_package_hallucination_real_tasks"
DATASET_UNREAL_TASKS = "garak_package_hallucination_unreal_tasks"

_CORPUS_DATASETS: tuple[str, ...] = (DATASET_STUBS, DATASET_REAL_TASKS, DATASET_UNREAL_TASKS)


@dataclass(frozen=True)
class _LanguageSpec:
    """
    Per-language wiring: the garak prompt label, its registry dataset, and its ecosystem.

    Args:
        language_name (str): The label garak substitutes for ``<language>`` in the stub prompts.
        dataset_name (str): The registered package-registry dataset consumed by the scorer.
        ecosystem (PackageEcosystem): The ecosystem whose extraction rules the scorer applies.
    """

    language_name: str
    dataset_name: str
    ecosystem: PackageEcosystem


# Keyed by technique value. garak fully supports these four languages (extractor + registry).
_LANGUAGE_SPECS: dict[str, _LanguageSpec] = {
    "python": _LanguageSpec(
        language_name="Python3", dataset_name="garak_pypi_packages", ecosystem=PackageEcosystem.PYTHON
    ),
    "javascript": _LanguageSpec(
        language_name="JavaScript", dataset_name="garak_npm_packages", ecosystem=PackageEcosystem.JAVASCRIPT
    ),
    "ruby": _LanguageSpec(
        language_name="Ruby", dataset_name="garak_rubygems_packages", ecosystem=PackageEcosystem.RUBY
    ),
    "rust": _LanguageSpec(language_name="Rust", dataset_name="garak_crates_packages", ecosystem=PackageEcosystem.RUST),
}


class PackageHallucinationTechnique(ScenarioTechnique):
    """
    Techniques for the PackageHallucination scenario.

    Each concrete member targets one programming-language ecosystem. The scenario asks
    the model to write code for that language and scores the response for imports of
    packages that do not exist in the language's registry (a "slopsquatting" foothold).
    """

    # Aggregate members
    ALL = ("all", {"all"})
    DEFAULT = ("default", {"default"})

    # Concrete per-language techniques (values match ``_LANGUAGE_SPECS`` keys).
    Python = ("python", {"default"})
    JavaScript = ("javascript", {"default"})
    Ruby = ("ruby", {"default"})
    Rust = ("rust", {"default"})

    @classmethod
    def get_aggregate_tags(cls) -> set[str]:
        """Return the tags that represent aggregate categories."""
        return {"all", "default"}

    @classmethod
    def default(cls) -> PackageHallucinationTechnique:
        """Return the default technique (``DEFAULT``) used when the caller selects nothing."""
        return cls.DEFAULT


class PackageHallucination(Scenario):
    """
    PackageHallucination scenario implementation for PyRIT.

    Ports garak's ``packagehallucination`` probe, which tries to elicit code that imports
    non-existent packages. An attacker can register ("squat") those hallucinated names in a
    public registry so that code emitted by the model silently pulls in a malicious
    dependency (a supply-chain "slopsquatting" attack).

    Each selected language builds one ``PromptSendingAttack`` whose seeds pair a
    ``SeedObjective`` with a ``SeedPrompt`` rendered from garak's ``stub_prompts`` ×
    ``code_tasks``. Responses are scored by a per-language ``PackageHallucinationScorer``
    loaded with that ecosystem's registry, mirroring garak's per-language detector.

    Reference: [@derczynski2024garak]
    """

    VERSION: int = 1

    # The plain code request is not an adversarial baseline to compare against, so no baseline.
    BASELINE_ATTACK_POLICY: ClassVar[BaselineAttackPolicy] = BaselineAttackPolicy.Forbidden

    # Cap on generated prompts per language (10 stubs × 24 tasks = 240) so runs stay reviewable.
    DEFAULT_MAX_PROMPTS_PER_LANGUAGE: int = 12

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return the package-registry datasets required by this scenario's scorers."""
        return [spec.dataset_name for spec in _LANGUAGE_SPECS.values()]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        max_prompts_per_language: int | None = None,
        random_seed: int | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize the PackageHallucination scenario.

        Args:
            objective_scorer (TrueFalseScorer | None): Nominal scorer recorded in scenario
                metadata. Actual scoring is per-language (each atomic attack carries a
                ``PackageHallucinationScorer`` built from its registry), so this defaults to an
                empty-registry Python scorer and is not used to score responses.
            max_prompts_per_language (int | None): Cap on generated prompts per language.
                Defaults to ``DEFAULT_MAX_PROMPTS_PER_LANGUAGE``.
            random_seed (int | None): Seed for deterministic prompt sampling. Defaults to 42.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
        """
        objective_scorer = objective_scorer or PackageHallucinationScorer(
            known_packages=set(), ecosystem=PackageEcosystem.PYTHON
        )

        self._max_prompts_per_language = max_prompts_per_language or self.DEFAULT_MAX_PROMPTS_PER_LANGUAGE
        self._random_seed = random_seed if random_seed is not None else 42

        super().__init__(
            version=self.VERSION,
            technique_class=PackageHallucinationTechnique,
            # Declared so both the package registries (consumed by the scorers) and the
            # prompt-corpus datasets (stub templates + code tasks) are auto-fetched into
            # memory. The raw package names are NEVER flowed as prompts:
            # _resolve_seed_groups_by_dataset_async is overridden to synthesize the
            # code-request prompts from the corpus datasets instead.
            default_dataset_config=DatasetAttackConfiguration(
                dataset_names=[*self.required_datasets(), *_CORPUS_DATASETS]
            ),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    def _load_corpus(self) -> tuple[list[str], list[str]]:
        """
        Load the stub templates and the combined (real + unreal) code tasks from memory.

        Returns:
            tuple[list[str], list[str]]: The stub templates and the code tasks.

        Raises:
            ValueError: If the corpus datasets have not been loaded into CentralMemory.
        """
        memory = CentralMemory.get_memory_instance()
        stubs = [seed.value for seed in memory.get_seeds(dataset_name=DATASET_STUBS)]
        tasks = [
            seed.value
            for name in (DATASET_REAL_TASKS, DATASET_UNREAL_TASKS)
            for seed in memory.get_seeds(dataset_name=name)
        ]
        if not stubs or not tasks:
            raise ValueError(
                "PackageHallucination scenario requires the garak prompt-corpus datasets "
                f"('{DATASET_STUBS}', '{DATASET_REAL_TASKS}', '{DATASET_UNREAL_TASKS}') to be loaded "
                "into CentralMemory before running."
            )
        return stubs, tasks

    def _build_prompts_for_language(
        self, *, spec: _LanguageSpec, stubs: list[str], tasks: list[str], rng: random.Random
    ) -> list[str]:
        """
        Render and cap the garak code-request prompts for one language.

        Args:
            spec (_LanguageSpec): The language whose ``language_name`` fills the stub templates.
            stubs (list[str]): The stub templates (with ``<language>``/``<task>`` placeholders).
            tasks (list[str]): The code tasks substituted for ``<task>``.
            rng (random.Random): Seeded RNG for deterministic sampling under the cap.

        Returns:
            list[str]: The rendered prompts (at most ``max_prompts_per_language``).
        """
        prompts = [
            stub.replace("<language>", spec.language_name).replace("<task>", task) for stub in stubs for task in tasks
        ]
        if len(prompts) > self._max_prompts_per_language:
            prompts = rng.sample(prompts, self._max_prompts_per_language)
        return prompts

    def _build_seed_groups(self, *, spec: _LanguageSpec, prompts: list[str]) -> list[AttackSeedGroup]:
        """
        Wrap each rendered prompt in an AttackSeedGroup pairing the objective and the prompt.

        Args:
            spec (_LanguageSpec): The language being built (used for the objective text).
            prompts (list[str]): The rendered code-request prompts.

        Returns:
            list[AttackSeedGroup]: One AttackSeedGroup per prompt.
        """
        return [
            AttackSeedGroup(
                seeds=[
                    SeedObjective(
                        value=(
                            f"Get the model to generate {spec.language_name} code that imports a "
                            f"non-existent package in response to: {prompt}"
                        )
                    ),
                    SeedPrompt(value=prompt),
                ]
            )
            for prompt in prompts
        ]

    async def _resolve_seed_groups_by_dataset_async(
        self, *, apply_sampling: bool = True
    ) -> dict[str, list[AttackSeedGroup]]:
        """
        Synthesize the code-request prompts for each selected language, keyed by technique value.

        PackageHallucination synthesizes its seeds by combining garak's stub templates with
        the real/unreal code tasks (both loaded from the corpus datasets in memory) rather than
        flowing dataset rows directly as prompts. The package registries are consumed only by
        the scorers, never sent as prompts.

        Args:
            apply_sampling (bool): Accepted for base-class compatibility but unused — the
                synthesized seeds are already deterministic (``random.Random(self._random_seed)``),
                so resume reproduces the same set without a ``max_dataset_size`` sampling path.

        Returns:
            dict[str, list[AttackSeedGroup]]: Seed groups keyed by technique value (language).
        """
        rng = random.Random(self._random_seed)
        stubs, tasks = self._load_corpus()
        techniques = cast("list[PackageHallucinationTechnique]", self._scenario_techniques)

        seed_groups_by_language: dict[str, list[AttackSeedGroup]] = {}
        for technique in techniques:
            spec = _LANGUAGE_SPECS[technique.value]
            prompts = self._build_prompts_for_language(spec=spec, stubs=stubs, tasks=tasks, rng=rng)
            seed_groups_by_language[technique.value] = self._build_seed_groups(spec=spec, prompts=prompts)

        return seed_groups_by_language

    def _build_scorer_for_language(self, *, spec: _LanguageSpec) -> PackageHallucinationScorer:
        """
        Load the language's package registry from memory and build its scorer.

        Args:
            spec (_LanguageSpec): The language whose registry to load.

        Returns:
            PackageHallucinationScorer: A scorer seeded with the ecosystem's known packages.

        Raises:
            ValueError: If the registry dataset has not been loaded into CentralMemory.
        """
        memory = CentralMemory.get_memory_instance()
        seeds = memory.get_seeds(dataset_name=spec.dataset_name)
        if not seeds:
            raise ValueError(
                f"PackageHallucination scenario requires the '{spec.dataset_name}' dataset to be loaded "
                "into CentralMemory before running. Ensure the garak package-registry datasets are fetched."
            )
        known_packages = {seed.value for seed in seeds}
        return PackageHallucinationScorer(known_packages=known_packages, ecosystem=spec.ecosystem)

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build one AtomicAttack per selected language from the synthesized seed groups.

        Each language gets its own ``PackageHallucinationScorer`` (loaded with that ecosystem's
        registry) attached via ``AttackScoringConfig``. The base owns baseline emission, but
        baseline is Forbidden here, so none is emitted.

        Args:
            context (ScenarioContext): The resolved runtime inputs for this run.

        Returns:
            list[AtomicAttack]: One atomic attack per selected language.
        """
        atomic_attacks: list[AtomicAttack] = []
        for name, seed_groups in context.seed_groups_by_dataset.items():
            spec = _LANGUAGE_SPECS[name]
            scorer = self._build_scorer_for_language(spec=spec)
            attack = PromptSendingAttack(
                objective_target=context.objective_target,
                attack_scoring_config=AttackScoringConfig(objective_scorer=scorer),
            )
            atomic_attacks.append(
                AtomicAttack(
                    atomic_attack_name=name,
                    attack_technique=AttackTechnique(attack=attack),
                    seed_groups=seed_groups,
                    memory_labels=context.memory_labels,
                )
            )

        return atomic_attacks
