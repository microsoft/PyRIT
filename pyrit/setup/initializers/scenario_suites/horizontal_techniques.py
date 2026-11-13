# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Configurable horizontal techniques for text evaluations."""

from dataclasses import dataclass, fields
from typing import ClassVar

from pyrit.common.random_context import get_random_generator
from pyrit.models import DatasetFetchPolicy, DatasetSourceInput, ScenarioDatasetRequest, ScenarioPreset
from pyrit.setup.initializers.scenario_suites._suite_recipe import _resolve_suite_arguments


@dataclass(kw_only=True)
class HorizontalTechniquesSuite:
    """
    Build jailbreak, multilingual, encoding, and obfuscation presets over shared objectives.

    Each family accepts explicit names, ``["all"]``, or a seeded sample count. The
    baseline runs once with the jailbreak family. Datasets are resolved at launch,
    and scoring follows the existing scenario defaults.
    """

    DESCRIPTION: ClassVar[str] = (
        "Runs configurable jailbreak templates, multilingual translation, encodings, and character "
        "obfuscation against the same objectives."
    )
    DATASET_NAMES: ClassVar[tuple[str, ...]] = ("airt_harms",)
    SAMPLING_NAMESPACE: ClassVar[str] = "horizontal_techniques"
    _SELECTIONS: ClassVar[dict[str, tuple[str, int]]] = {
        "jailbreak_names": ("num_jailbreaks", 5),
        "languages": ("num_languages", 4),
        "encoding_techniques": ("num_encodings", 4),
        "obfuscation_techniques": ("num_obfuscations", 4),
    }

    jailbreak_names: list[str] | None = None
    num_jailbreaks: int | None = None
    languages: list[str] | None = None
    num_languages: int | None = None
    encoding_techniques: list[str] | None = None
    num_encodings: int | None = None
    obfuscation_techniques: list[str] | None = None
    num_obfuscations: int | None = None
    dataset_names: list[str] | None = None
    dataset_fetch: DatasetFetchPolicy = DatasetFetchPolicy.IF_MISSING
    selection_seed: int = 0

    def __post_init__(self) -> None:
        """
        Validate constructor types and domain constraints without loading catalogs.

        Raises:
            ValueError: If selectors conflict, counts are invalid, or dataset settings are invalid.
        """
        resolved = _resolve_suite_arguments(
            suite_class=type(self), args={field.name: getattr(self, field.name) for field in fields(self)}
        )
        for name, value in resolved.items():
            setattr(self, name, value)
        for name, (count_name, _) in self._SELECTIONS.items():
            names, count = getattr(self, name), getattr(self, count_name)
            if names is not None and count is not None:
                raise ValueError(f"Provide only one of {name} and {count_name}.")
            if count is not None and count < 1:
                raise ValueError(f"{count_name} must be positive.")
            if names is not None:
                self._validate_selection_shape(name=name, names=names)
        self._validate_datasets()

    def validate_catalogs(self) -> None:
        """
        Check selections against packaged catalogs without building presets or changing live state.

        Raises:
            ValueError: If a name is incompatible or a requested sample exceeds its catalog.
        """
        self._resolve_selections()

    def build_presets(self, *, suite_name: str) -> list[ScenarioPreset]:
        """
        Resolve family selections and build one preset per family.

        Args:
            suite_name (str): Registry name used to prefix each preset name.

        Returns:
            list[ScenarioPreset]: Ordered presets with shared text objectives.
        """
        selections = self._resolve_selections()
        return [
            ScenarioPreset(
                name=f"{suite_name}_jailbreaks",
                scenario_name="airt.jailbreak",
                show_in_scanner=False,
                techniques=["prompt_sending"],
                datasets=self._shared_datasets(),
                include_baseline=True,
                scenario_params={"jailbreak_names": selections["jailbreak_names"]},
            ),
            ScenarioPreset(
                name=f"{suite_name}_multilingual",
                scenario_name="airt.multilingual",
                show_in_scanner=False,
                techniques=["prompt_sending"],
                datasets=self._shared_datasets(),
                include_baseline=False,
                scenario_params={
                    "languages": selections["languages"],
                    "translation_strategies": ["translation"],
                },
            ),
            ScenarioPreset(
                name=f"{suite_name}_encoding",
                scenario_name="foundry.red_team_agent",
                show_in_scanner=False,
                techniques=selections["encoding_techniques"],
                datasets=self._shared_datasets(),
                include_baseline=False,
            ),
            ScenarioPreset(
                name=f"{suite_name}_obfuscation",
                scenario_name="foundry.red_team_agent",
                show_in_scanner=False,
                techniques=selections["obfuscation_techniques"],
                datasets=self._shared_datasets(),
                include_baseline=False,
            ),
        ]

    def _resolve_selections(self) -> dict[str, list[str]]:
        """
        Resolve family selections using the same catalogs for preflight and initialization.

        Returns:
            dict[str, list[str]]: Concrete selections keyed by family parameter.

        Raises:
            ValueError: If a name is incompatible or the requested sample cannot be drawn.
        """
        from pyrit.datasets import TextJailBreak
        from pyrit.scenario.airt import Multilingual  # type: ignore[ty:unresolved-import]
        from pyrit.scenario.foundry import FoundryTechnique  # type: ignore[ty:unresolved-import]

        return {
            "jailbreak_names": self._select(name="jailbreak_names", candidates=TextJailBreak.get_jailbreak_templates()),
            "languages": self._select(
                name="languages",
                candidates=[name for name in Multilingual.get_default_languages() if name.casefold() != "english"],
            ),
            "encoding_techniques": self._select(
                name="encoding_techniques",
                candidates=[technique.value for technique in FoundryTechnique.get_techniques_by_tag("encoding")],
            ),
            "obfuscation_techniques": self._select(
                name="obfuscation_techniques",
                candidates=[technique.value for technique in FoundryTechnique.get_techniques_by_tag("obfuscation")],
            ),
        }

    def _select(self, *, name: str, candidates: list[str]) -> list[str]:
        """
        Select explicit names or a stable sample without sharing mutable random state.

        Returns:
            list[str]: Concrete technique or language names.

        Raises:
            ValueError: If a name is incompatible or the requested sample cannot be drawn.
        """
        from pyrit.scenario.scenarios.airt.multilingual import normalize_languages

        count_name, default_count = self._SELECTIONS[name]
        pool = sorted(set(candidates))
        names: list[str] | None = getattr(self, name)
        if names is not None:
            selected = (
                normalize_languages(names)
                if name == "languages"
                else list(dict.fromkeys(item.strip() for item in names))
            )
            if selected[0].casefold() == "all":
                if not pool:
                    raise ValueError(f"{name} has no eligible catalog members.")
                return pool
            unknown = set(selected) - set(pool)
            if name != "languages" and unknown:
                raise ValueError(f"Unknown or incompatible {name}: {', '.join(sorted(unknown))}.")
            return selected
        if not pool:
            raise ValueError(f"{name} has no eligible catalog members.")
        size = getattr(self, count_name)
        size = default_count if size is None else size
        if size > len(pool):
            raise ValueError(f"{count_name} cannot exceed {len(pool)} eligible members.")
        generator = get_random_generator(stream=name, namespace=self.SAMPLING_NAMESPACE, seed=self.selection_seed)
        return sorted(generator.sample(pool, k=size))

    def _validate_datasets(self) -> None:
        """
        Validate the shared dataset request without loading datasets.

        Every preset prepares the shared datasets independently, so ``always`` would
        replace them once per preset and the families could run different objectives.

        Raises:
            ValueError: If dataset names are empty or the fetch policy is ``always``.
        """
        if self.dataset_names is not None and (
            not self.dataset_names or any(not name.strip() for name in self.dataset_names)
        ):
            raise ValueError("dataset_names must contain at least one non-empty name.")
        if self.dataset_fetch == DatasetFetchPolicy.ALWAYS:
            raise ValueError(
                "dataset_fetch 'always' would refresh the shared datasets once per preset. Refresh them once "
                "before launch, for example with the refresh_datasets initializer, and use 'if_missing' or 'never'."
            )

    def _shared_datasets(self) -> ScenarioDatasetRequest:
        """Return an independent dataset request over the full common objective population."""
        return ScenarioDatasetRequest(
            sources=[DatasetSourceInput(name=name) for name in self.dataset_names or self.DATASET_NAMES],
            fetch=self.dataset_fetch,
            max_per_dataset="all",
            max_total="all",
        )

    @staticmethod
    def _validate_selection_shape(*, name: str, names: list[str]) -> None:
        """
        Validate selectors before accessing local catalogs.

        Raises:
            ValueError: If names are empty or mix ``all`` with explicit members.
        """
        normalized = {
            " ".join(item.replace("_", " ").split()).casefold() if name == "languages" else item.strip().casefold()
            for item in names
        }
        if not normalized or "" in normalized:
            raise ValueError(f"{name} must contain at least one non-empty name.")
        if "all" in normalized and len(normalized) != 1:
            raise ValueError(f"{name}: 'all' must be the only selection.")
