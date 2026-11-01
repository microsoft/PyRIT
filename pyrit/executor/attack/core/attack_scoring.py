# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Attack scoring policy for objective and auxiliary scorer roles."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pyrit.models import ScoringExpectation, SurfaceScorable, TraceScorable
from pyrit.score import MessageScorer

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.models import Message, Score
    from pyrit.score import Scorer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedAttackScoring:
    """The objective input and the auxiliary scorers that apply, with their inputs."""

    objective_expectation: ScoringExpectation | None
    auxiliary_scorers: list[Scorer] = field(default_factory=list)
    auxiliary_expectations: list[ScoringExpectation | None] = field(default_factory=list)


def prepare_attack_scoring(
    *,
    objective_scorer: Scorer | None,
    auxiliary_scorers: Sequence[Scorer] | None,
    expectation: ScoringExpectation | None,
) -> PreparedAttackScoring:
    """
    Apply the attack's scorer-role policy to one scoring input.

    The objective scorer must cover every supplied condition. Each auxiliary scorer receives
    the conditions that it supports. The attack skips an auxiliary scorer when a condition
    that the auxiliary scorer requires is absent.

    Returns:
        PreparedAttackScoring: The validated inputs for each scorer that runs.

    Raises:
        ValueError: If conditions are present without an objective scorer, or a scorer
            rejects its input.
    """
    ScoringExpectation.validate_type(expectation)
    if objective_scorer is None and expectation is not None and expectation.conditions:
        raise ValueError("An objective scorer is required to evaluate the supplied conditions.")
    objective_expectation = (
        objective_scorer.prepare_expectation(expectation=expectation) if objective_scorer is not None else expectation
    )
    prepared = PreparedAttackScoring(objective_expectation=objective_expectation)
    for scorer in auxiliary_scorers or []:
        selected = scorer.select_expectation(expectation=expectation)
        conditions = selected.conditions if selected is not None else ()
        if not all(
            any(isinstance(condition, condition_type) for condition in conditions)
            for condition_type in scorer.get_condition_types()
        ):
            logger.debug("Skipping auxiliary scorer %s: a required condition is absent.", type(scorer).__name__)
            continue
        prepared.auxiliary_scorers.append(scorer)
        prepared.auxiliary_expectations.append(scorer.prepare_expectation(expectation=selected))
    return prepared


# Evidence of what the target did (written files, invoked tools), acquired from a source rather
# than read from message text.
_SIDE_EFFECT_SCORABLE_TYPES = frozenset({SurfaceScorable, TraceScorable})


def _judges_side_effects(scorer: Scorer) -> bool:
    # Every leaf must read side effects; leaves of a composite may read different kinds.
    return all(not _SIDE_EFFECT_SCORABLE_TYPES.isdisjoint(types) for types in scorer.get_leaf_scorable_types())


async def score_attack_response_async(
    *,
    response: Message,
    objective_scorer: Scorer | None,
    auxiliary_scorers: Sequence[Scorer] | None,
    expectation: ScoringExpectation | None,
    side_effect_evidence_only: bool = False,
) -> dict[str, list[Score]]:
    """
    Score an attack response with the objective scorer and the auxiliary scorers that apply.

    Args:
        response (Message): The message that anchors scoring for the turn.
        objective_scorer (Scorer | None): The scorer that decides the attack outcome.
        auxiliary_scorers (Sequence[Scorer] | None): Additional scorers whose results are stored.
        expectation (ScoringExpectation | None): The scoring question.
        side_effect_evidence_only (bool): Run only scorers that judge side-effect evidence (written
            files or invoked tools) rather than message text. Use this when ``response`` is a
            delivered request with no target response, so the request is never judged as response text.

    Returns:
        dict[str, list[Score]]: The ``objective_scores`` and ``auxiliary_scores``.
    """
    prepared = prepare_attack_scoring(
        objective_scorer=objective_scorer, auxiliary_scorers=auxiliary_scorers, expectation=expectation
    )
    auxiliary = list(zip(prepared.auxiliary_scorers, prepared.auxiliary_expectations, strict=True))
    if side_effect_evidence_only:
        if objective_scorer is not None and not _judges_side_effects(objective_scorer):
            objective_scorer = None
        auxiliary = [(scorer, selected) for scorer, selected in auxiliary if _judges_side_effects(scorer)]
        if objective_scorer is None and not auxiliary:
            return {"objective_scores": [], "auxiliary_scores": []}
    return await MessageScorer.score_response_async(
        response=response,
        objective_scorer=objective_scorer,
        auxiliary_scorers=[scorer for scorer, _ in auxiliary],
        expectation=prepared.objective_expectation,
        auxiliary_expectations=[selected for _, selected in auxiliary],
    )
