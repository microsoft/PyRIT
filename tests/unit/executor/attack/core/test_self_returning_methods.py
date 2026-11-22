# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Attack-core factories and context managers annotated ``-> Self`` keep the subclass type."""

import logging
from typing import assert_type
from unittest.mock import MagicMock

import pytest

from pyrit.executor.attack.core.attack_preparation import AttackPreparationFailure, AttackPreparationFailureKind
from pyrit.executor.attack.core.attack_strategy import _ObjectiveTargetConversationLifecycle
from pyrit.models import AttackOutcome, AttackResult
from pyrit.prompt_target import PromptTarget


class _DerivedAttackPreparationFailure(AttackPreparationFailure):
    pass


class _DerivedLifecycle(_ObjectiveTargetConversationLifecycle):
    pass


def _result(*, metadata: dict[str, str]) -> AttackResult:
    return AttackResult(
        conversation_id="conversation",
        objective="objective",
        outcome=AttackOutcome.UNDETERMINED,
        outcome_reason="blocked",
        metadata=metadata,
    )


@pytest.mark.parametrize("failure_cls", [AttackPreparationFailure, _DerivedAttackPreparationFailure])
def test_attack_preparation_failure_from_result_returns_calling_class(
    failure_cls: type[AttackPreparationFailure],
) -> None:
    signal = AttackPreparationFailure(kind=AttackPreparationFailureKind.ADVERSARIAL_CHAT_BLOCKED, reason="blocked")

    failure = failure_cls.from_result(result=_result(metadata=signal.to_metadata()))

    assert type(failure) is failure_cls
    assert failure_cls.from_result(result=_result(metadata={})) is None


def test_attack_preparation_failure_from_result_preserves_subclass_type() -> None:
    signal = AttackPreparationFailure(kind=AttackPreparationFailureKind.ADVERSARIAL_CHAT_BLOCKED, reason="blocked")

    failure = _DerivedAttackPreparationFailure.from_result(result=_result(metadata=signal.to_metadata()))

    assert_type(failure, _DerivedAttackPreparationFailure | None)
    assert isinstance(failure, _DerivedAttackPreparationFailure)


async def test_objective_target_lifecycle_aenter_preserves_subclass_type() -> None:
    lifecycle = _DerivedLifecycle(objective_target=MagicMock(spec=PromptTarget), logger=logging.getLogger(__name__))

    async with lifecycle as entered:
        assert_type(entered, _DerivedLifecycle)
        assert entered is lifecycle
