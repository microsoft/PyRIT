# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Run selected techniques independently across supplied datasets or inline seed groups."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyrit.common import apply_defaults
from pyrit.common.async_compatibility import run_legacy_sync_async
from pyrit.models import (
    IndeterminateDatasetSize,
    ScenarioDatasetSizeEstimate,
)
from pyrit.models.scenario_readiness import ScenarioConfigurationIssue, ScenarioReadiness
from pyrit.registry import AttackTechniqueRegistry, ScorerRegistry
from pyrit.scenario.core.dataset_configuration import (
    DatasetAttackConfiguration,
    DatasetSourceKind,
)
from pyrit.scenario.core.matrix_atomic_attack_builder import build_matrix_atomic_attacks
from pyrit.scenario.core.scenario import Scenario

if TYPE_CHECKING:
    from pyrit.scenario.core.atomic_attack import AtomicAttack
    from pyrit.scenario.core.scenario_context import ScenarioContext
    from pyrit.scenario.core.scenario_technique import ScenarioTechnique
    from pyrit.score import TrueFalseScorer


@AttackTechniqueRegistry.cache_scenario_technique_class
def _build_sweep_technique() -> type[ScenarioTechnique]:
    """Return the registered technique pool, defaulting to the light aggregate."""
    return AttackTechniqueRegistry.build_technique_class_from_factories(
        class_name="SweepTechnique",
        factories=list(AttackTechniqueRegistry.get_registry_singleton().get_factories().values()),
        default_tags={"light"},
    )


class Sweep(Scenario):
    """
    Run selected techniques independently against supplied objective populations.

    Select named datasets or provide inline seed groups, and supply an objective scorer
    or register a default one. There is no built-in harm corpus or refusal-scoring fallback.
    All registered techniques are available, with ``light`` selected by default when present.
    Results group by dataset; the baseline, enabled by default, sends the same resolved
    population directly for comparison.
    """

    VERSION: int = 1

    @apply_defaults
    def __init__(
        self,
        *,
        objective_scorer: TrueFalseScorer | None = None,
        scenario_result_id: str | None = None,
    ) -> None:
        """
        Initialize a sweep without resolving execution dependencies.

        Args:
            objective_scorer (TrueFalseScorer | None): Scorer for the supplied objectives.
                If omitted, execution requires a registered default objective scorer.
            scenario_result_id (str | None): Existing sweep result to resume.
        """
        super().__init__(
            version=self.VERSION,
            technique_class=_build_sweep_technique(),
            default_dataset_config=DatasetAttackConfiguration(),
            objective_scorer=objective_scorer,
            scenario_result_id=scenario_result_id,
        )

    def _validate_runtime_configuration(self) -> None:
        """
        Require registered techniques before estimating, checking readiness, or constructing attacks.

        Raises:
            ValueError: If no attack techniques are registered.
        """
        super()._validate_runtime_configuration()
        if not AttackTechniqueRegistry.get_registry_singleton().get_factories():
            raise ValueError(
                "technique.sweep requires registered attack techniques. Run the 'technique' initializer "
                "or register technique factories before launching."
            )

    def _has_population(self) -> bool:
        """Return whether named datasets or inline inputs were supplied."""
        return bool(self._dataset_config.dataset_names) or self._dataset_config.source_kind is DatasetSourceKind.INLINE

    def _get_run_size_budget(self) -> ScenarioDatasetSizeEstimate:
        """Return an indeterminate budget until a population is selected, so catalog previews stay available."""
        if not self._has_population():
            return IndeterminateDatasetSize(
                detail="Select named datasets or inline seed groups to estimate this sweep."
            )
        return super()._get_run_size_budget()

    def get_readiness(self) -> ScenarioReadiness:
        """
        Report a missing population, which blocks launch before any dataset is prepared.

        Returns:
            ScenarioReadiness: Execution-role bindings and configuration issues.
        """
        readiness = super().get_readiness()
        if not self._has_population():
            readiness.issues.append(
                ScenarioConfigurationIssue(
                    role="datasets",
                    component=self.name,
                    reason="technique.sweep has no default datasets.",
                    remediation="Select one or more datasets, or supply inline seed groups.",
                )
            )
        return readiness

    def _get_default_objective_scorer(self) -> TrueFalseScorer:
        """
        Resolve the configured registry scorer without constructing an implicit refusal scorer.

        Returns:
            TrueFalseScorer: The configured default objective scorer.

        Raises:
            ValueError: If no default scorer is registered, or its configuration is invalid.
        """
        from pyrit.setup.initializers import ScorerInitializerTags

        entries = ScorerRegistry.get_registry_singleton().instances.get_by_tag(
            tag=ScorerInitializerTags.DEFAULT_OBJECTIVE_SCORER
        )
        if not entries:
            raise ValueError(
                "technique.sweep requires an explicit objective_scorer or a registered default objective scorer."
            )
        return super()._get_default_objective_scorer()

    async def _build_atomic_attacks_async(self, *, context: ScenarioContext) -> list[AtomicAttack]:
        """
        Build the technique-by-dataset matrix off the event loop.

        Returns:
            list[AtomicAttack]: Selected technique attacks and the optional shared baseline.
        """
        return await run_legacy_sync_async(
            build_matrix_atomic_attacks,
            context=context,
            objective_scorer=self._objective_scorer,
            display_group_fn=lambda combo: combo.dataset_name,
        )
