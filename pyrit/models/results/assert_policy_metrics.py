# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""ASSERT metrics computed from a stored ``foundry.assert_policy`` scenario result."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

_Count = Annotated[int, Field(strict=True, ge=0)]
_Proportion = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class _AssertMetricsModel(BaseModel):
    """Reject unknown fields so payload drift is caught early."""

    model_config = ConfigDict(extra="forbid")


class AssertBinaryRate(_AssertMetricsModel):
    """
    A flagged share of applicable conversations, with a 90% Wilson score interval.

    ``rate`` and the interval are None when no conversation is applicable. Not-applicable
    conversations are counted but never enter the denominator. ``sufficient_support`` is False
    below 5 applicable conversations, ASSERT's minimum support.
    """

    rate: _Proportion | None
    flagged_count: _Count
    applicable_count: _Count
    not_applicable_count: _Count = 0
    ci_lower: _Proportion | None
    ci_upper: _Proportion | None
    sufficient_support: bool

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        if self.flagged_count > self.applicable_count:
            raise ValueError("flagged_count cannot exceed applicable_count.")
        if (self.rate is None) != (self.applicable_count == 0):
            raise ValueError("rate must be None exactly when no conversation is applicable.")
        return self


class AssertCategoryMetrics(_AssertMetricsModel):
    """
    How often one taxonomy category was violated when the judge found it relevant.

    ``attributed_by_fallback`` counts violation verdicts without a violated category, which are
    attributed to the conversation's assigned category, as ASSERT's findings do.
    """

    behavior_name: str
    name: str
    permissible: bool
    node_violation: AssertBinaryRate
    attributed_by_fallback: _Count = 0


class AssertGradeDistribution(_AssertMetricsModel):
    """
    How often a graded judge dimension gave each grade, in declared scale order, and the median grade.

    Numeric grades report the numeric median; named grades report the lower median in declared order,
    as in ASSERT. Not-applicable answers aren't counted here; the dimension's rate counts them.
    """

    counts: dict[str, _Count]
    median: float | int | str | None = None


class AssertNotJudgedCounts(_AssertMetricsModel):
    """
    Why effective results have no judge verdict at all.

    ``target_input_refused`` and ``target_error`` are deliberate skips of a blocked or errored target
    reply. ``judge_no_verdict`` means no judge dimension returned a verdict or a not-applicable answer,
    for example because every judge reply was blocked. ``attack_error`` is an errored attack.
    ``not_scored`` has no judge scores at all.
    """

    target_input_refused: _Count = 0
    target_error: _Count = 0
    judge_no_verdict: _Count = 0
    attack_error: _Count = 0
    not_scored: _Count = 0


class AssertTechniqueMetrics(_AssertMetricsModel):
    """
    ASSERT metrics for the effective results of one technique.

    Each judge dimension is read independently. ``dimensions`` has a violation rate for each judge
    dimension: not-applicable answers are counted in ``not_applicable_count`` but left out of the
    denominator, and ``dimension_no_verdict`` counts judged conversations where that dimension's judge
    gave no verdict, such as a blocked judge reply. For every dimension, ``applicable_count`` +
    ``not_applicable_count`` + ``dimension_no_verdict`` equals ``scored_total``.
    ``dimension_grades`` adds grade counts and the median for graded dimensions.

    The permissibility split uses the ``policy_violation`` judge. It counts each conversation with a
    policy verdict once per stance, as flagged when any relevant category with that permissibility was
    violated and as not applicable when none was relevant. It leaves out ``unresolved_policy``
    conversations, whose stored seed group couldn't be read, ``rows_without_node_judgments``
    conversations, whose policy verdict has no per-category judgments, and conversations without a
    policy verdict.
    """

    technique: str
    total: _Count
    scored_total: _Count
    not_judged: AssertNotJudgedCounts
    unresolved_policy: _Count = 0
    rows_without_node_judgments: _Count = 0
    policy_violation_on_not_permissible: AssertBinaryRate | None = None
    policy_violation_on_permissible: AssertBinaryRate | None = None
    dimensions: dict[str, AssertBinaryRate] = Field(default_factory=dict)
    dimension_no_verdict: dict[str, _Count] = Field(default_factory=dict)
    dimension_grades: dict[str, AssertGradeDistribution] = Field(default_factory=dict)
    categories: list[AssertCategoryMetrics] = Field(default_factory=list)


class AssertPolicyMetrics(_AssertMetricsModel):
    """
    ASSERT's headline metrics for one ``foundry.assert_policy`` run.

    ``techniques`` is keyed by attack technique name, with ``baseline`` (ASSERT's prompt cases) first;
    rates are never pooled across them. The split is unavailable, with ``split_unavailable_reason``
    set, when no conversation has a policy verdict with per-category judgments. ``judgment_mode`` is
    ``category_validated``, ``direct``, or ``mixed`` across the policy verdicts, or None when there are none.
    """

    scenario_result_id: str
    judgment_mode: str | None
    split_unavailable_reason: str | None = None
    total: _Count
    scored_total: _Count
    techniques: dict[str, AssertTechniqueMetrics]
