# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Backend factories annotated ``-> Self`` build the subclass they are called on."""

import uuid
from datetime import UTC, datetime
from typing import assert_type

import pytest

from pyrit.backend.models import MessagePieceView, ScoreView
from pyrit.backend.models.datasets import DatasetUploadLimits
from pyrit.backend.services.live_attack_reader import LiveAttack
from pyrit.executor.attack.core.attack_executor import ActiveAttackExecution
from pyrit.executor.attack.core.attack_result_attribution import AttackResultAttribution
from pyrit.models import (
    AtomicAttackIdentifier,
    ComponentIdentifier,
    MessagePiece,
    Score,
)


class _DerivedScoreView(ScoreView):
    pass


class _DerivedMessagePieceView(MessagePieceView):
    pass


class _DerivedDatasetUploadLimits(DatasetUploadLimits):
    pass


class _DerivedLiveAttack(LiveAttack):
    pass


def _score() -> Score:
    return Score(score_value="0.5", score_type="float_scale", message_piece_id=str(uuid.uuid4()))


def _execution() -> ActiveAttackExecution:
    return ActiveAttackExecution(
        attack_result_id=str(uuid.UUID(int=200)),
        objective="objective",
        conversation_ids=("first",),
        current_conversation_id="first",
        started_at=datetime(2026, 11, 6, 12, tzinfo=UTC),
        attribution=AttackResultAttribution(parent_id=str(uuid.UUID(int=100)), parent_collection="live"),
        atomic_attack_identifier=AtomicAttackIdentifier.build(
            attack_identifier=ComponentIdentifier(class_name="CrescendoAttack", class_module="pyrit.executor.attack")
        ),
    )


@pytest.mark.parametrize("view_cls", [ScoreView, _DerivedScoreView])
def test_score_view_from_domain_returns_calling_class(view_cls: type[ScoreView]) -> None:
    view = view_cls.from_domain(_score(), is_objective_score=True)

    assert type(view) is view_cls
    assert view.is_objective_score


@pytest.mark.parametrize("view_cls", [MessagePieceView, _DerivedMessagePieceView])
def test_message_piece_view_from_domain_returns_calling_class(view_cls: type[MessagePieceView]) -> None:
    piece = MessagePiece(role="user", original_value="hello")

    view = view_cls.from_domain(piece)

    assert type(view) is view_cls
    assert view.original_value == "hello"


def test_attack_view_factories_preserve_subclass_types() -> None:
    score_view = _DerivedScoreView.from_domain(_score())
    piece_view = _DerivedMessagePieceView.from_domain(MessagePiece(role="user", original_value="hello"))

    assert_type(score_view, _DerivedScoreView)
    assert_type(piece_view, _DerivedMessagePieceView)


@pytest.mark.parametrize("limits_cls", [DatasetUploadLimits, _DerivedDatasetUploadLimits])
def test_dataset_upload_limits_from_environment_returns_calling_class(
    limits_cls: type[DatasetUploadLimits], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYRIT_DATASET_MAX_PROMPTS", "7")

    limits = limits_cls.from_environment()

    assert type(limits) is limits_cls
    assert limits.prompts == 7


def test_dataset_upload_limits_from_environment_preserves_subclass_type() -> None:
    limits = _DerivedDatasetUploadLimits.from_environment()

    assert_type(limits, _DerivedDatasetUploadLimits)
    assert isinstance(limits, _DerivedDatasetUploadLimits)


@pytest.mark.parametrize("live_cls", [LiveAttack, _DerivedLiveAttack])
def test_live_attack_from_execution_returns_calling_class(live_cls: type[LiveAttack]) -> None:
    live = live_cls.from_execution(scenario_result_id="run", execution=_execution())

    assert type(live) is live_cls


def test_live_attack_from_execution_preserves_subclass_type() -> None:
    live = _DerivedLiveAttack.from_execution(scenario_result_id="run", execution=_execution())

    assert_type(live, _DerivedLiveAttack | None)
    assert isinstance(live, _DerivedLiveAttack)
