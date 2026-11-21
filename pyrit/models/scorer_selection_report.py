# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A record of which objective scorer each planned atomic group will use, made before a run."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.scenario_progress import ScenarioRunPlanGroupKind


class ScorerVerdictStatus(str, Enum):
    """What scorer selection concluded about one candidate."""

    #: The candidate judges the group.
    SELECTED = "selected"

    #: The candidate was checked before the selected one and cannot judge the group.
    REJECTED = "rejected"


class ScorerCandidateVerdict(BaseModel):
    """
    What selection concluded about one candidate in the pool.

    Candidates after the selected one are not checked, so they have no verdict.

    Attributes:
        candidate_index (int): The candidate's position in the pool.
        candidate_label (str): The scorer class and evaluation key, for display.
        status (ScorerVerdictStatus): Whether the candidate was selected or rejected.
        reason_kind (str | None): For a rejection, the kind of mismatch, such as ``evaluation``.
        reason (str | None): For a rejection, why the candidate cannot judge the group.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_index: int = Field(ge=0)
    candidate_label: str
    status: ScorerVerdictStatus
    reason_kind: str | None = None
    reason: str | None = None


class ScorerSelectionEntry(BaseModel):
    """
    The objective scorer selected for one planned atomic group, and the candidates passed over.

    Attributes:
        atomic_attack_name (str): The planned atomic group.
        display_group (str): The group's report grouping.
        technique (str | None): The technique that built the group, when known.
        group_kind (ScenarioRunPlanGroupKind): What the group runs.
        required_capabilities (list[str]): Every output guarantee the selection required, sorted.
        required_evaluation (str | None): The evaluation the selection required, if any.
        candidates (list[ScorerCandidateVerdict]): The rejected candidates and the selected one,
            in pool order.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    atomic_attack_name: str
    display_group: str
    technique: str | None = None
    group_kind: ScenarioRunPlanGroupKind = ScenarioRunPlanGroupKind.ATTACK
    required_capabilities: list[str] = Field(default_factory=list)
    required_evaluation: str | None = None
    candidates: list[ScorerCandidateVerdict]

    @property
    def selected(self) -> ScorerCandidateVerdict:
        """The selected candidate."""
        return next(verdict for verdict in self.candidates if verdict.status is ScorerVerdictStatus.SELECTED)

    @property
    def rejected(self) -> list[ScorerCandidateVerdict]:
        """The candidates checked before the selected one, which cannot judge the group."""
        return [verdict for verdict in self.candidates if verdict.status is ScorerVerdictStatus.REJECTED]


class ScorerSelectionReport(BaseModel):
    """
    Which objective scorer each planned atomic group will use, and why earlier candidates were passed over.

    ``Scenario.get_scorer_selection_report`` builds it from the planned atomic groups after
    ``initialize_async``, before any prompt is sent and without calling a judge model. Only
    groups whose scorer was selected from a ``CompatibleScorers`` pool have an entry.

    Attributes:
        entries (list[ScorerSelectionEntry]): One entry per planned atomic group, in plan order.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: list[ScorerSelectionEntry] = Field(default_factory=list)
