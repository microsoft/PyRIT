# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Select a compatible objective scorer from an ordered pool, before any prompt is sent.

A scenario states what it needs (the evaluation it promises, the evidence it supplies,
and the output guarantees its attack relies on). Each input carries its own criteria
as a ``ScoringExpectation``. ``CompatibleScorers.select`` returns the first candidate
that satisfies the combined requirements and accepts every prepared expectation.

Selection never constructs candidates, acquires evidence, or calls a judge model. It
chooses between alternatives; it does not combine their coverage, and it is not an
execution fallback. Execution outcomes never advance the pool.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from pyrit.models import (
    ScenarioRunPlanScorerBinding,
    Scorable,
    ScorerCapabilityName,
    ScorerIdentifier,
    ScoringExpectation,
    scoring_expectation_fingerprint,
)
from pyrit.score.expectation_errors import AmbiguousConditionError, ExpectationMismatchError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Self

    from pyrit.score.scorer import Scorer
    from pyrit.score.true_false.true_false_scorer import TrueFalseScorer

__all__ = [
    "CompatibleScorers",
    "ConflictingScorerRequirementsError",
    "NoCompatibleScorerError",
    "ScorerCandidate",
    "ScorerMismatch",
    "ScorerMismatchKind",
    "ScorerRequirements",
    "ScorerSelection",
]

#: Key used to deduplicate inputs that carry no expectation.
NO_EXPECTATION_FINGERPRINT = "none"


def _validate_evaluation_key(value: object, *, owner: str) -> None:
    """
    Require an evaluation key to be absent or a non-blank, trimmed string.

    Raises:
        TypeError: If the key is not a string.
        ValueError: If the key is blank or has surrounding whitespace.
    """
    if value is None:
        return
    if not isinstance(value, str):
        raise TypeError(f"{owner}.evaluation must be a string or None.")
    if not value.strip():
        raise ValueError(f"{owner}.evaluation must be non-empty when present.")
    if value != value.strip():
        raise ValueError(f"{owner}.evaluation {value!r} has surrounding whitespace.")


class ConflictingScorerRequirementsError(ValueError):
    """Two requirement owners ask for different evaluations."""


#: How a scorer that lacks each capability falls short, and how to provide it.
_MISSING_CAPABILITY: dict[ScorerCapabilityName, str] = {
    ScorerCapabilityName.TRUE_FALSE: "does not return a single true/false verdict per scoring call",
    ScorerCapabilityName.FLOAT_SCALE: "does not return float-scale results",
    ScorerCapabilityName.PRESERVES_FLOAT_SCALE_VALUE: (
        "does not keep a float value on each true/false verdict. Use a FloatScaleThresholdScorer, "
        "or a TrueFalseFallbackScorer whose scorers both keep one"
    ),
}


@dataclass(frozen=True, kw_only=True)
class ScorerRequirements:
    """
    What a run needs from its objective scorer.

    Requirements come from the scenario and its resolved run configuration, the attack
    and technique, and the dataset or prepared population. ``combine`` merges them.

    Attributes:
        required (frozenset[ScorerCapabilityName]): The output guarantees the scorer must
            make. Most attacks require ``TRUE_FALSE``; an attack that ranks by float feedback
            also requires ``PRESERVES_FLOAT_SCALE_VALUE``. Any iterable is accepted.
        evaluation (str | None): The question the run promises to answer, such as
            ``"psychosocial.imminent_crisis"``. ``None`` adds no constraint. A key names a
            question; it does not claim two judges are calibrated alike.
    """

    required: frozenset[ScorerCapabilityName] = field(default_factory=frozenset)
    evaluation: str | None = None

    def __post_init__(self) -> None:
        """
        Validate the declared values and freeze ``required``.

        Raises:
            TypeError: If a field has the wrong type.
        """
        if isinstance(self.required, (str, bytes)) or not isinstance(self.required, Iterable):
            raise TypeError("ScorerRequirements.required must be an iterable of ScorerCapabilityName.")
        required = frozenset(self.required)
        if not all(isinstance(item, ScorerCapabilityName) for item in required):
            raise TypeError("ScorerRequirements.required must contain only ScorerCapabilityName values.")
        object.__setattr__(self, "required", required)
        _validate_evaluation_key(self.evaluation, owner="ScorerRequirements")

    @classmethod
    def combine(cls, *requirements: ScorerRequirements | None) -> Self:
        """
        Merge requirements from several owners.

        Evaluation keys must agree: absence adds no constraint and different values
        conflict. Required capabilities are the union of every owner's.

        Args:
            *requirements (ScorerRequirements | None): The owners' requirements; ``None`` is skipped.

        Returns:
            ScorerRequirements: The effective requirements.

        Raises:
            TypeError: If an item is not ``ScorerRequirements`` or None.
            ConflictingScorerRequirementsError: If two owners require different evaluations.
        """
        evaluation: str | None = None
        required: set[ScorerCapabilityName] = set()
        for item in requirements:
            if item is None:
                continue
            if not isinstance(item, ScorerRequirements):
                raise TypeError("Scorer requirements must be ScorerRequirements or None.")
            required.update(item.required)
            if item.evaluation is None:
                continue
            if evaluation is not None and evaluation != item.evaluation:
                raise ConflictingScorerRequirementsError(
                    f"Scorer requirements conflict: evaluation {evaluation!r} and {item.evaluation!r} "
                    "cannot both decide success for the same run."
                )
            evaluation = item.evaluation
        return cls(required=frozenset(required), evaluation=evaluation)

    def check(self, *, scorer: Scorer) -> list[str]:
        """
        Report every required capability the scorer lacks, without scoring.

        The evaluation key is not checked here: it binds a pool candidate, not a scorer.

        Args:
            scorer (Scorer): The configured scorer.

        Returns:
            list[str]: One message per missing capability, in a stable order; empty when all are met.
        """
        capabilities = scorer.capabilities
        name = type(scorer).__name__
        return [
            f"{name} {_MISSING_CAPABILITY[capability]} (requires '{capability.value}')."
            for capability in sorted(self.required, key=lambda item: item.value)
            if not capabilities.includes(capability=capability)
        ]

    def validate(self, *, scorer: Scorer) -> None:
        """
        Require the scorer to make every required capability.

        Args:
            scorer (Scorer): The configured scorer.

        Raises:
            ValueError: Naming every missing capability.
        """
        errors = self.check(scorer=scorer)
        if errors:
            raise ValueError("\n".join(errors))


@dataclass(frozen=True, kw_only=True)
class ScorerCandidate:
    """
    One configured objective scorer and the evaluation it is bound to.

    Bind the key to the configured outer scorer explicitly. It is never inferred from
    children, registry tags, or labels.

    Attributes:
        scorer (TrueFalseScorer): The configured outer scorer.
        evaluation (str | None): The question this scorer answers, or ``None`` when untagged.
    """

    scorer: TrueFalseScorer
    evaluation: str | None = None

    def __post_init__(self) -> None:
        """
        Validate the candidate.

        Raises:
            TypeError: If the scorer does not return a single true/false verdict.
        """
        from pyrit.score.scorer import Scorer

        if not isinstance(self.scorer, Scorer) or not self.scorer.capabilities.includes(
            capability=ScorerCapabilityName.TRUE_FALSE
        ):
            raise TypeError("ScorerCandidate.scorer must be a true/false scorer.")
        _validate_evaluation_key(self.evaluation, owner="ScorerCandidate")

    @property
    def label(self) -> str:
        """A short description for diagnostics."""
        tag = f" [{self.evaluation}]" if self.evaluation else ""
        return f"{type(self.scorer).__name__}{tag}"


class ScorerMismatchKind(str, Enum):
    """Why a candidate cannot judge a population."""

    #: The scorer does not accept the evidence family the attack supplies.
    EVIDENCE = "evidence"
    #: The candidate's evaluation binding differs from what the population requires.
    EVALUATION = "evaluation"
    #: The scorer lacks a required capability, such as float feedback for ranking.
    CAPABILITY = "capability"
    #: The scorer cannot evaluate a condition or criterion an input carries.
    CONDITION = "condition"


@dataclass(frozen=True, kw_only=True)
class ScorerMismatch:
    """
    One candidate's typed reason for rejection.

    Attributes:
        candidate_index (int): The candidate's position in the pool.
        candidate_label (str): The scorer class and evaluation key.
        kind (ScorerMismatchKind): The reason category.
        detail (str): The specific reason, naming the rejecting scorer or condition.
    """

    candidate_index: int
    candidate_label: str
    kind: ScorerMismatchKind
    detail: str

    def describe(self) -> str:
        """
        Format the mismatch for a diagnostic message.

        Returns:
            str: One line naming the candidate and its reason.
        """
        return f"#{self.candidate_index} {self.candidate_label} ({self.kind.value}): {self.detail}"


@dataclass(frozen=True, kw_only=True)
class ScorerSelection:
    """
    The chosen candidate.

    Attributes:
        candidate (ScorerCandidate): The selected candidate.
        candidate_index (int): Its position in the pool.
    """

    candidate: ScorerCandidate
    candidate_index: int

    @property
    def scorer(self) -> TrueFalseScorer:
        """The selected objective scorer."""
        return self.candidate.scorer

    @property
    def evaluation(self) -> str | None:
        """The selected candidate's evaluation key."""
        return self.candidate.evaluation

    def to_run_plan_binding(self) -> ScenarioRunPlanScorerBinding:
        """
        Record this selection for a scenario run plan, so resume can verify it.

        Returns:
            ScenarioRunPlanScorerBinding: The selected candidate's index, evaluation, and scorer hash.
        """
        return ScenarioRunPlanScorerBinding(
            candidate_index=self.candidate_index,
            evaluation=self.evaluation,
            scorer_hash=self.scorer.get_identifier().hash,
        )


class NoCompatibleScorerError(ValueError):
    """No candidate satisfies the requirements and accepts the whole population."""

    def __init__(
        self,
        *,
        requirements: ScorerRequirements,
        mismatches: Sequence[ScorerMismatch],
        available_evaluations: Sequence[str | None],
    ) -> None:
        """
        Build a message that names every candidate's reason.

        Args:
            requirements (ScorerRequirements): The effective requirements.
            mismatches (Sequence[ScorerMismatch]): One reason per candidate, in pool order.
            available_evaluations (Sequence[str | None]): Each candidate's evaluation key.
        """
        self.requirements = requirements
        self.mismatches = tuple(mismatches)
        lines = ["No objective scorer candidate is compatible with this population."]
        tagged = sorted({key for key in available_evaluations if key is not None})
        if requirements.evaluation is not None and requirements.evaluation not in tagged:
            known = ", ".join(repr(key) for key in tagged) if tagged else "none"
            lines.append(
                f"No candidate is bound to the required evaluation {requirements.evaluation!r}; "
                f"evaluations in the pool: {known}. Check the key for a typo, or add a candidate for it."
            )
        if requirements.evaluation is None and None not in available_evaluations:
            lines.append(
                "This population requires no evaluation, and every candidate is bound to one. Declare "
                "scorer_requirements on its dataset, or add a candidate without an evaluation."
            )
        lines.extend(mismatch.describe() for mismatch in self.mismatches)
        super().__init__("\n".join(lines))


def _check_input_expectations(
    expectations: Sequence[ScoringExpectation | None],
) -> dict[str, ScoringExpectation | None]:
    """
    Validate inputs once, independent of any candidate, and remove duplicates.

    A malformed input is the caller's error, so it raises here instead of making every
    candidate look incompatible.

    Returns:
        dict[str, ScoringExpectation | None]: Unique expectations keyed by fingerprint, in input order.

    Raises:
        TypeError: If an item is not a ``ScoringExpectation`` or None.
        AmbiguousConditionError: If an input carries two conditions of the same type.
    """
    unique: dict[str, ScoringExpectation | None] = {}
    for index, expectation in enumerate(expectations):
        ScoringExpectation.validate_type(expectation)
        if expectation is None:
            unique.setdefault(NO_EXPECTATION_FINGERPRINT, None)
            continue
        seen: set[type] = set()
        for condition in expectation.conditions:
            condition_type = type(condition)
            if condition_type in seen:
                raise AmbiguousConditionError(
                    f"Input #{index} carries more than one {condition_type.__name__} condition. "
                    "Each input supplies at most one condition of each type."
                )
            seen.add(condition_type)
        unique.setdefault(scoring_expectation_fingerprint(expectation), expectation)
    return unique


class CompatibleScorers:
    """
    An immutable, ordered pool of objective scorer candidates.

    ``select`` returns the first candidate that meets the effective requirements and
    accepts every prepared expectation of a population. A candidate bound to an evaluation
    judges only populations that require that evaluation, and a population that requires
    none uses a candidate without one. Order therefore breaks ties between candidates that
    answer the same question; it never changes which question is answered.

    Example:
        >>> pool = CompatibleScorers(
        ...     candidates=(
        ...         ScorerCandidate(scorer=therapist_scorer, evaluation="psychosocial.licensed_therapist"),
        ...         ScorerCandidate(scorer=crisis_scorer, evaluation="psychosocial.imminent_crisis"),
        ...     ),
        ... )
        >>> selected = pool.select(
        ...     requirements=ScorerRequirements(evaluation="psychosocial.imminent_crisis"),
        ...     scorable_type=MessageScorable,
        ...     expectations=prepared_expectations,
        ... )
    """

    def __init__(self, *, candidates: Sequence[ScorerCandidate | TrueFalseScorer]) -> None:
        """
        Create a pool. A bare scorer becomes one untagged candidate.

        Args:
            candidates (Sequence[ScorerCandidate | TrueFalseScorer]): Candidates in preference order.

        Raises:
            TypeError: If ``candidates`` is a string or not a sequence.
            ValueError: If the pool is empty or repeats a scorer and evaluation pair.
        """
        if isinstance(candidates, (str, bytes)) or not hasattr(candidates, "__iter__"):
            raise TypeError("candidates must be a sequence of ScorerCandidate or TrueFalseScorer.")
        normalized = tuple(
            candidate if isinstance(candidate, ScorerCandidate) else ScorerCandidate(scorer=candidate)
            for candidate in candidates
        )
        if not normalized:
            raise ValueError("CompatibleScorers requires at least one candidate.")
        seen: set[tuple[int, str | None]] = set()
        for candidate in normalized:
            key = (id(candidate.scorer), candidate.evaluation)
            if key in seen:
                raise ValueError(f"Candidate {candidate.label} appears more than once in the pool.")
            seen.add(key)
        self._candidates = normalized

    @classmethod
    def from_scorer(cls, scorer: TrueFalseScorer) -> Self:
        """
        Wrap one scorer as a single untagged candidate.

        Args:
            scorer (TrueFalseScorer): The scorer.

        Returns:
            CompatibleScorers: A one-candidate pool.
        """
        return cls(candidates=(ScorerCandidate(scorer=scorer),))

    @property
    def candidates(self) -> tuple[ScorerCandidate, ...]:
        """The candidates in preference order."""
        return self._candidates

    @property
    def evaluations(self) -> tuple[str | None, ...]:
        """Each candidate's evaluation key, in pool order."""
        return tuple(candidate.evaluation for candidate in self._candidates)

    def select(
        self,
        *,
        requirements: ScorerRequirements,
        scorable_type: type[Scorable],
        expectations: Sequence[ScoringExpectation | None],
    ) -> ScorerSelection:
        """
        Choose the first candidate that can judge the whole population.

        Inputs are checked once on their own before any candidate is tried, so a
        malformed input raises instead of looking like an incompatible pool. Each
        candidate is then checked for evidence, evaluation, output guarantee, and
        every unique prepared expectation, in that order. Known incompatibilities
        record a typed reason and move to the next candidate; any other error propagates.

        Args:
            requirements (ScorerRequirements): The combined, effective requirements.
            scorable_type (type[Scorable]): The evidence family the caller supplies.
            expectations (Sequence[ScoringExpectation | None]): The prepared population's
                expectations. Empty populations accept any candidate that meets the requirements.

        Returns:
            ScorerSelection: The chosen candidate and its index.

        Raises:
            TypeError: If an argument has the wrong type.
            AmbiguousConditionError: If an input is malformed.
            NoCompatibleScorerError: If no candidate is compatible.
        """
        if not isinstance(requirements, ScorerRequirements):
            raise TypeError("requirements must be ScorerRequirements.")
        if not isinstance(scorable_type, type) or not issubclass(scorable_type, Scorable):
            raise TypeError("scorable_type must be a Scorable subclass.")
        unique = _check_input_expectations(expectations)
        mismatches: list[ScorerMismatch] = []
        for index, candidate in enumerate(self._candidates):
            mismatch = self._check_candidate(
                index=index,
                candidate=candidate,
                requirements=requirements,
                scorable_type=scorable_type,
                expectations=tuple(unique.values()),
            )
            if mismatch is not None:
                mismatches.append(mismatch)
                continue
            return ScorerSelection(candidate=candidate, candidate_index=index)
        raise NoCompatibleScorerError(
            requirements=requirements, mismatches=mismatches, available_evaluations=self.evaluations
        )

    @staticmethod
    def _check_candidate(
        *,
        index: int,
        candidate: ScorerCandidate,
        requirements: ScorerRequirements,
        scorable_type: type[Scorable],
        expectations: tuple[ScoringExpectation | None, ...],
    ) -> ScorerMismatch | None:
        """
        Return the first known reason a candidate cannot judge the population.

        Returns:
            ScorerMismatch | None: The typed reason, or None when the candidate is compatible.
        """

        def mismatch(*, kind: ScorerMismatchKind, detail: str) -> ScorerMismatch:
            return ScorerMismatch(candidate_index=index, candidate_label=candidate.label, kind=kind, detail=detail)

        capabilities = candidate.scorer.capabilities
        if not capabilities.accepts_scorable_type(scorable_type):
            accepted = ", ".join(sorted(t.__name__ for t in capabilities.scorable_types)) or "none"
            return mismatch(
                kind=ScorerMismatchKind.EVIDENCE,
                detail=f"does not accept {scorable_type.__name__} evidence (accepts: {accepted}).",
            )
        if candidate.evaluation != requirements.evaluation:
            bound = repr(candidate.evaluation) if candidate.evaluation is not None else "no evaluation"
            required = repr(requirements.evaluation) if requirements.evaluation is not None else "none"
            return mismatch(
                kind=ScorerMismatchKind.EVALUATION,
                detail=f"is bound to {bound}; the population requires {required}.",
            )
        missing = requirements.check(scorer=candidate.scorer)
        if missing:
            return mismatch(kind=ScorerMismatchKind.CAPABILITY, detail=" ".join(missing))
        for expectation in expectations:
            try:
                candidate.scorer.prepare_expectation(expectation=expectation)
            except ExpectationMismatchError as exc:
                return mismatch(kind=ScorerMismatchKind.CONDITION, detail=f"{exc} (rejected by {exc.scorer_name}).")
        return None

    def get_identifier(self) -> ScorerIdentifier:
        """
        Identify the ordered pool.

        The identity includes each candidate's full scorer identity and evaluation key in
        pool order, so a different pool, order, or binding means a different run.

        Returns:
            ScorerIdentifier: The pool's identity.
        """
        return ScorerIdentifier.of(
            self,
            params={"evaluations": [candidate.evaluation for candidate in self._candidates]},
            scorer_type="true_false",
            sub_scorers=[candidate.scorer.get_identifier() for candidate in self._candidates],
        )

    def __len__(self) -> int:
        """
        Return the number of candidates.

        Returns:
            int: The pool size.
        """
        return len(self._candidates)

    def __repr__(self) -> str:
        """
        Describe the pool.

        Returns:
            str: The candidate labels in order.
        """
        return f"CompatibleScorers({', '.join(candidate.label for candidate in self._candidates)})"
