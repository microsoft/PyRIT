# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
``ImageTechniqueAdaptive`` — image-rendering adaptive scenario.

Picks an image-rendering attack technique per-objective using an epsilon-greedy
selector informed by observed success rates. Each technique renders the text
objective into an image payload (blank carrier, QR code, grid composite,
typographic list, etc.) and sends it to a vision-capable target whose text
response is scored. Runs up to ``max_attempts_per_objective`` techniques per
objective and stops early on success.

The image techniques are source-owned (``techniques/image.py``) and never enter
the global registry, so they cannot be selected by text-only scenarios and fail
at runtime against a text-only target.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from pyrit.common import apply_defaults
from pyrit.models.parameter import Parameter
from pyrit.prompt_target import TargetRequirements
from pyrit.registry.components.attack_technique_registry import AttackTechniqueRegistry
from pyrit.scenario.core.dataset_configuration import CompoundDatasetAttackConfiguration, DatasetAttackConfiguration
from pyrit.scenario.scenarios.adaptive.adaptive_scenario import AdaptiveScenario

if TYPE_CHECKING:
    from pyrit.scenario.core.attack_technique_factory import AttackTechniqueFactory
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.scenario.scenarios.adaptive.selectors import TechniqueSelector
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


def _image_technique_factories() -> list[AttackTechniqueFactory]:
    """
    Return the source-owned image-rendering technique factories.

    Imported lazily because ``techniques.image`` transitively re-imports
    ``pyrit.scenario``, so a top-level import would form a cycle during
    ``pyrit.scenario`` package initialization.

    Returns:
        list[AttackTechniqueFactory]: The image-rendering technique factories.
    """
    from pyrit.setup.initializers.techniques.image import get_technique_factories

    return get_technique_factories()


def _build_image_technique_adaptive_technique() -> type[ScenarioTechnique]:
    """
    Build the technique enum from the source-owned image catalog.

    Returns:
        type[ScenarioTechnique]: The dynamically-built technique enum class.
    """
    return AttackTechniqueRegistry.build_technique_class_from_factories(  # type: ignore[return-value, ty:invalid-return-type]
        class_name="ImageTechniqueAdaptiveTechnique",
        factories=_image_technique_factories(),
        default_names={"blank_canvas", "grid_composite", "comic_panel"},
    )


class ImageTechniqueAdaptive(AdaptiveScenario):
    """
    Selects an image-rendering attack technique for each objective using an
    epsilon-greedy strategy informed by prior success rates. Each technique
    renders the text objective as an image for a vision-capable target, and the
    target's text response is scored. The scenario stops after a successful
    attack or after ``max_attempts_per_objective`` attempts.
    """

    _cached_technique_class: ClassVar[type[ScenarioTechnique] | None] = None

    VERSION: ClassVar[int] = 1

    # Each image technique sends an image-only payload, while the adaptive text baseline
    # (``BASELINE_ATTACK_POLICY`` is Enabled) sends a text-only message. The target must therefore
    # accept both text and image input (as separate messages) and return text so the existing text
    # scorer can judge the response.
    TARGET_REQUIREMENTS: ClassVar[TargetRequirements] = TargetRequirements(
        required_input_modalities=frozenset({frozenset({"text"}), frozenset({"image_path"})}),
        required_output_modalities=frozenset({frozenset({"text"})}),
    )

    @classmethod
    def _atomic_attack_prefix(cls) -> str:
        """Return the prefix for per-objective atomic-attack names."""
        return "adaptive_image"

    @classmethod
    def get_technique_class(cls) -> type[ScenarioTechnique]:
        """Return the technique enum for this scenario, building it once on first access."""
        if cls._cached_technique_class is None:
            cls._cached_technique_class = _build_image_technique_adaptive_technique()
        return cls._cached_technique_class

    def _get_attack_technique_factories(self) -> dict[str, AttackTechniqueFactory]:
        """
        Return only the source-owned image factories.

        Overrides the base (which merges the global catalog) so the adaptive
        dispatcher resolves the image technique enum against the image pool, and
        no image technique ever leaks into another scenario's pool.

        Returns:
            dict[str, AttackTechniqueFactory]: Mapping of technique name to factory.
        """
        return {factory.name: factory for factory in _image_technique_factories()}

    @classmethod
    def required_datasets(cls) -> list[str]:
        """Return the dataset names this scenario expects when no override is provided."""
        return [
            "airt_hate",
            "airt_fairness",
            "airt_violence",
            "airt_sexual",
            "airt_harassment",
            "airt_misinformation",
            "airt_leakage",
        ]

    @classmethod
    def default_dataset_config(cls) -> DatasetAttackConfiguration:
        """Return the default dataset config (required datasets, capped at 4 per dataset)."""
        return CompoundDatasetAttackConfiguration.per_dataset(dataset_names=cls.required_datasets(), max_dataset_size=4)

    @classmethod
    def additional_parameters(cls) -> list[Parameter]:
        """
        Declare custom parameters this scenario accepts from the CLI / config file.

        Returns:
            list[Parameter]: Parameters configurable per-run.
        """
        return [
            Parameter(
                name="max_attempts_per_objective",
                description="Max techniques tried per objective. Defaults to 3.",
                param_type=int,
                default=3,
            ),
        ]

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        selector: TechniqueSelector | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Args:
            objective_scorer (TrueFalseScorer | None): Scorer used to judge each
                response. Defaults to the composite scorer from the base class.
            selector (TechniqueSelector | None): Pre-built selector. When ``None``
                (default) an ``EpsilonGreedyTechniqueSelector`` is created
                with default settings. Pass a custom instance to tune
                ``epsilon`` or ``random_seed``.
            scenario_result_id (str | None): ID of an existing ``ScenarioResult`` to resume.
        """
        super().__init__(
            objective_scorer=objective_scorer,
            selector=selector,
            scenario_result_id=scenario_result_id,
        )
