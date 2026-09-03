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
from pyrit.models import AttackSeedGroup, SeedObjective, SeedPrompt
from pyrit.scenario.core.atomic_attack import AtomicAttack
from pyrit.scenario.core.attack_technique import AttackTechnique
from pyrit.scenario.core.dataset_configuration import DatasetAttackConfiguration, DatasetConfiguration
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


# Keyed by technique value. Rust is the default because its registry is substantially smaller
# than the Python, JavaScript, and Ruby registries.
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
    "dart": _LanguageSpec(language_name="Dart", dataset_name="garak_dart_packages", ecosystem=PackageEcosystem.DART),
    "perl": _LanguageSpec(language_name="Perl", dataset_name="garak_perl_packages", ecosystem=PackageEcosystem.PERL),
    "raku": _LanguageSpec(language_name="Raku", dataset_name="garak_raku_packages", ecosystem=PackageEcosystem.RAKU),
}


class _PackageHallucinationDatasetConfiguration(DatasetConfiguration):
    """Dataset configuration that exposes raw values for prompt and registry datasets."""

    async def get_values_by_dataset_async(self) -> dict[str, list[str]]:
        """
        Resolve configured datasets, fetching missing datasets from their providers.

        Returns:
            dict[str, list[str]]: Seed values keyed by dataset name.
        """
        seeds_by_dataset = await self._collect_named_seeds_async()
        return {name: [seed.value for seed in seeds] for name, seeds in seeds_by_dataset.items()}


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

    # Concrete per-language techniques (values match the ``_LANGUAGE_SPECS`` keys).
    Python = ("python", set())
    JavaScript = ("javascript", set())
    Ruby = ("ruby", set())
    Rust = ("rust", {"default"})
    Dart = ("dart", set())
    Perl = ("perl", set())
    Raku = ("raku", set())

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

    VERSION: int = 3

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
                empty-registry scorer for the default technique and is not used to score responses.
            max_prompts_per_language (int | None): Cap on generated prompts per language.
                Defaults to ``DEFAULT_MAX_PROMPTS_PER_LANGUAGE``.
            random_seed (int | None): Seed for deterministic prompt sampling. Defaults to 42.
            scenario_result_id (str | None): Optional ID of an existing scenario result to resume.
        """
        default_technique = PackageHallucinationTechnique.expand({PackageHallucinationTechnique.default()})[0]
        default_spec = _LANGUAGE_SPECS[default_technique.value]
        objective_scorer = objective_scorer or PackageHallucinationScorer(
            known_packages=set(), ecosystem=default_spec.ecosystem
        )

        self._max_prompts_per_language = max_prompts_per_language or self.DEFAULT_MAX_PROMPTS_PER_LANGUAGE
        self._random_seed = random_seed if random_seed is not None else 42
        self._known_packages_by_technique: dict[str, set[str]] = {}

        super().__init__(
            version=self.VERSION,
            technique_class=PackageHallucinationTechnique,
            # Preload only the Rust registry and prompt corpus. Other registries are fetched
            # on demand when their techniques are selected.
            default_dataset_config=DatasetAttackConfiguration(
                dataset_names=[_LANGUAGE_SPECS["rust"].dataset_name, *_CORPUS_DATASETS]
            ),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    @staticmethod
    def _load_corpus(*, dataset_values: dict[str, list[str]]) -> tuple[list[str], list[str]]:
        """
        Load the stub templates and combined code tasks from resolved dataset values.

        Args:
            dataset_values (dict[str, list[str]]): Seed values keyed by dataset name.

        Returns:
            tuple[list[str], list[str]]: The stub templates and the code tasks.

        Raises:
            ValueError: If the resolved corpus datasets are empty.
        """
        stubs = dataset_values[DATASET_STUBS]
        tasks = [task for name in (DATASET_REAL_TASKS, DATASET_UNREAL_TASKS) for task in dataset_values[name]]
        if not stubs or not tasks:
            raise ValueError(
                "PackageHallucination scenario requires the garak prompt-corpus datasets "
                f"('{DATASET_STUBS}', '{DATASET_REAL_TASKS}', '{DATASET_UNREAL_TASKS}') to contain seeds."
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
        techniques = cast("list[PackageHallucinationTechnique]", self._scenario_techniques)
        specs_by_technique = {technique.value: _LANGUAGE_SPECS[technique.value] for technique in techniques}
        dataset_names = [
            *_CORPUS_DATASETS,
            *(spec.dataset_name for spec in specs_by_technique.values()),
        ]
        dataset_values = await _PackageHallucinationDatasetConfiguration(
            dataset_names=list(dict.fromkeys(dataset_names))
        ).get_values_by_dataset_async()

        rng = random.Random(self._random_seed)
        stubs, tasks = self._load_corpus(dataset_values=dataset_values)
        self._known_packages_by_technique = {
            name: set(dataset_values[spec.dataset_name]) for name, spec in specs_by_technique.items()
        }

        seed_groups_by_language: dict[str, list[AttackSeedGroup]] = {}
        for technique_name, spec in specs_by_technique.items():
            prompts = self._build_prompts_for_language(spec=spec, stubs=stubs, tasks=tasks, rng=rng)
            seed_groups_by_language[technique_name] = self._build_seed_groups(spec=spec, prompts=prompts)

        return seed_groups_by_language

    def _build_scorer_for_technique(self, *, technique: PackageHallucinationTechnique) -> PackageHallucinationScorer:
        """
        Build the selected technique's scorer from its resolved package registry.

        Args:
            technique (PackageHallucinationTechnique): The language technique to score.

        Returns:
            PackageHallucinationScorer: A scorer seeded with the ecosystem's known packages.
        """
        spec = _LANGUAGE_SPECS[technique.value]
        known_packages = self._known_packages_by_technique[technique.value]
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
        techniques_by_value = {
            technique.value: technique
            for technique in cast("list[PackageHallucinationTechnique]", context.scenario_techniques)
        }
        for name, seed_groups in context.seed_groups_by_dataset.items():
            scorer = self._build_scorer_for_technique(technique=techniques_by_value[name])
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
