# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from types import SimpleNamespace

from unit.mocks import make_scenario_result

from pyrit.models import AttackOutcome, AttackResult, ComponentIdentifier, MessagePiece, Score, ScoreStatus
from pyrit.output._derivation import (
    attack_score_display,
    group_success_rate,
    resolve_scorer_name,
    resolve_target_info,
    select_attacks,
    select_objective_scores,
)


def _target(**params) -> ComponentIdentifier:
    return ComponentIdentifier(class_name="MockTarget", class_module="tests", params=params)


def _attack(*, outcome: AttackOutcome = AttackOutcome.SUCCESS, conversation_id: str = "c") -> AttackResult:
    return AttackResult(conversation_id=conversation_id, objective="obj", outcome=outcome)


# --- resolve_target_info ---


def test_resolve_target_info_none():
    assert resolve_target_info(None) == (None, None, None)


def test_resolve_target_info_prefers_underlying_model_name():
    info = resolve_target_info(_target(underlying_model_name="gpt-x", model_name="fallback", endpoint="https://e"))
    assert info.type == "MockTarget"
    assert info.model == "gpt-x"
    assert info.endpoint == "https://e"


def test_resolve_target_info_falls_back_to_model_name():
    assert resolve_target_info(_target(model_name="gpt-y")).model == "gpt-y"


def test_resolve_target_info_missing_fields_are_none():
    info = resolve_target_info(_target())
    assert info.model is None
    assert info.endpoint is None


# --- group_success_rate ---


def test_group_success_rate_empty_is_zero():
    assert group_success_rate([]) == 0


def test_group_success_rate_counts_success():
    attacks = [
        _attack(outcome=AttackOutcome.SUCCESS),
        _attack(outcome=AttackOutcome.FAILURE),
        _attack(outcome=AttackOutcome.SUCCESS),
        _attack(outcome=AttackOutcome.UNDETERMINED),
    ]
    assert group_success_rate(attacks) == 50


# --- attack_score_display ---


def test_attack_score_display_no_score_returns_none_value():
    attack = SimpleNamespace(last_score=None)
    assert attack_score_display(attack) is None
    assert attack_score_display(attack, none_value="-") == "-"


def test_attack_score_display_returns_value():
    attack = SimpleNamespace(last_score=Score(score_type="float_scale", score_value="0.42"))
    assert attack_score_display(attack) == "0.42"


def test_attack_score_display_falls_back_to_status():
    score = Score(score_type="true_false", score_value=None, status=ScoreStatus.UNDETERMINED)
    attack = SimpleNamespace(last_score=score)
    assert attack_score_display(attack) == ScoreStatus.UNDETERMINED.value


# --- select_attacks ---


def test_select_attacks_returns_all_pairs():
    a1, a2 = _attack(conversation_id="1"), _attack(conversation_id="2")
    result = make_scenario_result(attack_results={"tech": [a1, a2]})
    assert select_attacks(result) == [("tech", a1), ("tech", a2)]


def test_select_attacks_filters_by_id():
    a1, a2 = _attack(conversation_id="1"), _attack(conversation_id="2")
    result = make_scenario_result(attack_results={"tech": [a1, a2]})
    selected = select_attacks(result, attack_result_ids=[a2.attack_result_id])
    assert selected == [("tech", a2)]


# --- resolve_scorer_name ---


def test_resolve_scorer_name_present():
    score = Score(
        score_type="true_false",
        score_value="true",
        scorer_class_identifier=ComponentIdentifier(class_name="MockScorer", class_module="tests"),
    )
    assert resolve_scorer_name(score) == "MockScorer"


def test_resolve_scorer_name_absent_returns_none_value():
    score = Score(score_type="true_false", score_value="true")
    assert resolve_scorer_name(score) is None
    assert resolve_scorer_name(score, none_value="Unknown") == "Unknown"


# --- select_objective_scores ---

_OBJECTIVE = ComponentIdentifier(class_name="ObjectiveScorer", class_module="tests", params={"threshold": 0.5})
_REFUSAL = ComponentIdentifier(class_name="RefusalScorer", class_module="tests")


def _stored_score(*, owner: uuid.UUID | None, label: str, scorer: ComponentIdentifier | None) -> Score:
    return Score(
        score_type="true_false",
        score_value="true",
        score_rationale=label,
        message_piece_id=owner,
        scorer_class_identifier=scorer,
    )


def _selected(pieces: list[MessagePiece], scores: list[Score]) -> dict[str, str | None]:
    selected = select_objective_scores(pieces=pieces, scores=scores, objective_scorer_identifier=_OBJECTIVE)
    return {piece_id: score.score_rationale for piece_id, score in selected.items()}


def test_select_objective_scores_prefers_hash_match_over_earlier_class_name_match():
    piece = MessagePiece(role="assistant", original_value="reply")
    same_class = ComponentIdentifier(class_name="ObjectiveScorer", class_module="tests")
    scores = [
        _stored_score(owner=piece.id, label="class-only", scorer=same_class),
        _stored_score(owner=piece.id, label="exact", scorer=_OBJECTIVE),
    ]
    assert _selected([piece], scores) == {str(piece.id): "exact"}


def test_select_objective_scores_falls_back_to_class_name():
    piece = MessagePiece(role="assistant", original_value="reply")
    same_class = ComponentIdentifier(class_name="ObjectiveScorer", class_module="elsewhere")
    scores = [
        _stored_score(owner=piece.id, label="auxiliary", scorer=_REFUSAL),
        _stored_score(owner=piece.id, label="same-class", scorer=same_class),
    ]
    assert _selected([piece], scores) == {str(piece.id): "same-class"}


def test_select_objective_scores_keeps_the_first_match():
    piece = MessagePiece(role="assistant", original_value="reply")
    same_class = ComponentIdentifier(class_name="ObjectiveScorer", class_module="elsewhere")
    exact = [_stored_score(owner=piece.id, label=label, scorer=_OBJECTIVE) for label in ("first", "second")]
    class_only = [_stored_score(owner=piece.id, label=label, scorer=same_class) for label in ("first", "second")]
    assert _selected([piece], exact) == {str(piece.id): "first"}
    assert _selected([piece], class_only) == {str(piece.id): "first"}


def test_select_objective_scores_ignores_auxiliary_unidentified_and_ownerless_scores():
    piece = MessagePiece(role="assistant", original_value="reply")
    scores = [
        _stored_score(owner=piece.id, label="auxiliary", scorer=_REFUSAL),
        _stored_score(owner=piece.id, label="unidentified", scorer=None),
        _stored_score(owner=None, label="ownerless", scorer=_OBJECTIVE),
    ]
    assert _selected([piece], scores) == {}


def test_select_objective_scores_keeps_message_level_score_on_the_piece_that_stores_it():
    first = MessagePiece(role="assistant", original_value="first")
    second = MessagePiece(role="assistant", original_value="second")
    message_level = _stored_score(owner=first.id, label="message-level", scorer=_OBJECTIVE)
    assert _selected([first, second], [message_level]) == {str(first.id): "message-level"}


def test_select_objective_scores_reads_duplicated_piece_score_from_original():
    original_id = uuid.uuid4()
    duplicate = MessagePiece(role="assistant", original_value="reply", original_prompt_id=original_id)
    scores = [_stored_score(owner=original_id, label="original", scorer=_OBJECTIVE)]
    assert _selected([duplicate], scores) == {str(duplicate.id): "original"}
