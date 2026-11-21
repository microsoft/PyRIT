# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""A record of which objective scorer each technique and dataset would use, made before a run."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScorerVerdictStatus = Literal["selected", "rejected", "not_checked"]


class ScorerCandidateVerdict(BaseModel):
    """
    What selection concluded about one candidate in the pool.

    Attributes:
        candidate_index (int): The candidate's position in the pool.
        candidate_label (str): The scorer class and evaluation key, for display.
        evaluation (str | None): The candidate's evaluation key.
        status (ScorerVerdictStatus): ``selected``; ``rejected`` with a reason; or ``not_checked``
            because an earlier candidate was selected.
        reason_kind (str | None): For a rejection, the kind of mismatch, such as ``evaluation``.
        reason (str | None): For a rejection, why the candidate cannot judge this population.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_index: int = Field(ge=0)
    candidate_label: str
    evaluation: str | None = None
    status: ScorerVerdictStatus
    reason_kind: str | None = None
    reason: str | None = None


class ScorerSelectionEntry(BaseModel):
    """
    The selection for one technique and dataset, with every candidate's verdict.

    Attributes:
        technique (str): The technique name.
        dataset (str): The dataset name.
        required_evaluation (str | None): The evaluation the combined requirements named.
        requires_float_scale_value (bool): Whether the technique needs float feedback.
        candidates (list[ScorerCandidateVerdict]): One verdict per candidate, in pool order.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    technique: str
    dataset: str
    required_evaluation: str | None = None
    requires_float_scale_value: bool = False
    candidates: list[ScorerCandidateVerdict]

    @property
    def selected(self) -> ScorerCandidateVerdict | None:
        """The selected candidate, or None when no candidate fits and the cell is skipped."""
        return next((verdict for verdict in self.candidates if verdict.status == "selected"), None)

    def describe(self) -> list[str]:
        """
        Render this entry as readable lines.

        Returns:
            list[str]: A heading line for the cell, then one line per candidate that was checked.
        """
        needs = []
        if self.required_evaluation is not None:
            needs.append(f"evaluation {self.required_evaluation!r}")
        if self.requires_float_scale_value:
            needs.append("float feedback")
        suffix = f" (requires {' and '.join(needs)})" if needs else ""
        chosen = self.selected
        outcome = chosen.candidate_label if chosen is not None else "no compatible scorer, skipped"
        lines = [f"{self.technique} / {self.dataset}: {outcome}{suffix}"]
        lines.extend(
            f"  #{verdict.candidate_index} {verdict.candidate_label}: {verdict.reason_kind}, {verdict.reason}"
            for verdict in self.candidates
            if verdict.status == "rejected"
        )
        return lines


class ScorerSelectionReport(BaseModel):
    """
    Which objective scorer each technique and dataset will use, and why the others were passed over.

    Scenarios that select from a ``CompatibleScorers`` pool build this while they initialize,
    before any prompt is sent and without calling a judge model.

    Attributes:
        entries (list[ScorerSelectionEntry]): One entry per technique and dataset, in build order.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: list[ScorerSelectionEntry] = Field(default_factory=list)

    @property
    def skipped(self) -> list[ScorerSelectionEntry]:
        """The technique and dataset cells no candidate could judge."""
        return [entry for entry in self.entries if entry.selected is None]

    def to_text(self) -> str:
        """
        Render the report as plain text, one block per technique and dataset.

        Returns:
            str: The rendered report, or a note when nothing was selected from a pool.
        """
        if not self.entries:
            return "No scorer selection was made from a pool."
        return "\n".join(line for entry in self.entries for line in entry.describe())
