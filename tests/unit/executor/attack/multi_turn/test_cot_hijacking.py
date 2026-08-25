# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for the CoT Hijacking attack strategy."""

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.exceptions import InvalidJsonException
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackParameters,
    AttackScoringConfig,
    ConversationSession,
    CoTHijackingAttack,
    CoTHijackingAttackContext,
    PrependedConversationConfig,
)
from pyrit.executor.attack.multi_turn.cot_hijacking import (
    DEFAULT_PUZZLE_TYPES,
    SUPPORTED_PUZZLE_TYPES,
    StreamState,
)
from pyrit.models import (
    JSON_SCHEMA_METADATA_KEY,
    AttackOutcome,
    ChatMessageRole,
    ComponentIdentifier,
    ConversationType,
    Message,
    MessagePiece,
    Score,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.score import Scorer, TrueFalseScorer
from pyrit.score.score_utils import ORIGINAL_FLOAT_VALUE_KEY

pytestmark = pytest.mark.usefixtures("patch_central_database")


def _component_identifier(name: str) -> ComponentIdentifier:
    return ComponentIdentifier(class_name=name, class_module="tests.unit")


def _mock_target(*, name: str, supports_required_capabilities: bool = True) -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    target.set_system_prompt = MagicMock()
    target.get_identifier.return_value = _component_identifier(name)
    target.configuration = MagicMock()
    target.configuration.includes.return_value = supports_required_capabilities
    target.configuration.capabilities.input_modalities = frozenset({frozenset({"text"})})
    target.configuration.capabilities.output_modalities = frozenset({frozenset({"text"})})
    return target


def _mock_scorer(*, name: str = "MockObjectiveScorer") -> MagicMock:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock()
    scorer.get_identifier.return_value = _component_identifier(name)
    return scorer


def _mock_normalizer() -> MagicMock:
    normalizer = MagicMock(spec=PromptNormalizer)
    normalizer.send_prompt_async = AsyncMock()
    normalizer.memory = MagicMock()
    normalizer.memory.get_message_pieces.return_value = []
    normalizer.memory.delete_conversation_pieces_after_sequence.return_value = 0
    return normalizer


def _message(
    *,
    value: str,
    role: ChatMessageRole,
    conversation_id: str | None = None,
) -> Message:
    return Message(
        message_pieces=[
            MessagePiece(
                role=role,
                original_value=value,
                original_value_data_type="text",
                converted_value=value,
                converted_value_data_type="text",
                conversation_id=conversation_id,
            )
        ]
    )


def _score(
    *,
    successful: bool,
    raw_value: float | str | None = None,
    message_piece_id: str | None = None,
) -> Score:
    metadata = {ORIGINAL_FLOAT_VALUE_KEY: raw_value} if raw_value is not None else {}
    return Score(
        score_type="true_false",
        score_value=str(successful).lower(),
        score_category=["objective"],
        score_value_description="Objective score",
        score_rationale="Test rationale",
        score_metadata=metadata,
        message_piece_id=message_piece_id or str(uuid.uuid4()),
        scorer_class_identifier=_component_identifier("MockObjectiveScorer"),
    )


def _adversarial_json(
    *,
    next_message: str = "crafted jailbreak",
    rationale: str = "embed the objective in the puzzle",
    last_response_summary: str = "no prior response",
) -> str:
    return json.dumps(
        {
            "next_message": next_message,
            "rationale": rationale,
            "last_response_summary": last_response_summary,
        }
    )


def _context(
    *,
    objective: str = "Test objective",
    stream_count: int = 1,
    puzzle_types: list[str] | None = None,
    prepended_conversation: list[Message] | None = None,
) -> CoTHijackingAttackContext:
    effective_puzzles = puzzle_types or ["logic_grid"]
    return CoTHijackingAttackContext(
        params=AttackParameters(
            objective=objective,
            prepended_conversation=prepended_conversation,
        ),
        session=ConversationSession(),
        stream_states=[
            StreamState(
                stream_id=index,
                puzzle_type=effective_puzzles[index % len(effective_puzzles)],
            )
            for index in range(stream_count)
        ],
    )


def _create_attack(
    *,
    objective_target: MagicMock,
    adversarial_chat: MagicMock,
    objective_scorer: MagicMock | None = None,
    auxiliary_scorers: list[Scorer] | None = None,
    prompt_normalizer: MagicMock | None = None,
    max_iterations: int = 3,
    puzzle_types: list[str] | None = None,
    n_streams: int | None = 1,
    use_score_as_feedback: bool = True,
    prepended_conversation_config: PrependedConversationConfig | None = None,
) -> CoTHijackingAttack:
    scorer = objective_scorer or _mock_scorer()
    return CoTHijackingAttack(
        objective_target=objective_target,
        attack_adversarial_config=AttackAdversarialConfig(target=adversarial_chat),
        attack_scoring_config=AttackScoringConfig(
            objective_scorer=scorer,
            auxiliary_scorers=auxiliary_scorers or [],
            use_score_as_feedback=use_score_as_feedback,
        ),
        prompt_normalizer=prompt_normalizer,
        max_iterations=max_iterations,
        puzzle_types=["logic_grid"] if puzzle_types is None else puzzle_types,
        n_streams=n_streams,
        prepended_conversation_config=prepended_conversation_config,
    )


@pytest.fixture
def mock_objective_target() -> MagicMock:
    return _mock_target(name="MockObjectiveTarget")


@pytest.fixture
def mock_adversarial_chat() -> MagicMock:
    return _mock_target(name="MockAdversarialChat")


@pytest.fixture
def mock_objective_scorer() -> MagicMock:
    return _mock_scorer()


@pytest.fixture
def mock_prompt_normalizer() -> MagicMock:
    return _mock_normalizer()


@pytest.fixture
def basic_context() -> CoTHijackingAttackContext:
    return _context()


def test_init_exposes_current_configs(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_objective_scorer: MagicMock,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        objective_scorer=mock_objective_scorer,
    )

    assert attack.get_objective_target() is mock_objective_target
    assert attack.get_attack_scoring_config().objective_scorer is mock_objective_scorer
    adversarial_config = attack.get_attack_adversarial_config()
    assert adversarial_config is not None
    assert adversarial_config.target is mock_adversarial_chat
    assert adversarial_config.system_prompt is not None
    assert adversarial_config.first_message is None
    assert "next_message" not in {field.name for field in attack.params_type.__dataclass_fields__.values()}


def test_init_uses_public_defaults(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_objective_scorer: MagicMock,
) -> None:
    attack = CoTHijackingAttack(
        objective_target=mock_objective_target,
        attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
        attack_scoring_config=AttackScoringConfig(objective_scorer=mock_objective_scorer),
    )

    assert attack._max_iterations == 10
    assert attack._puzzle_types == DEFAULT_PUZZLE_TYPES
    assert attack._n_streams == len(DEFAULT_PUZZLE_TYPES)


async def test_n_streams_none_defaults_to_puzzle_count_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    puzzle_types = ["logic_grid", "sudoku", "skyscrapers"]
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        puzzle_types=puzzle_types,
        n_streams=None,
    )
    context = _context(puzzle_types=puzzle_types)

    await attack._setup_async(context=context)

    assert attack._n_streams == len(puzzle_types)
    assert [state.puzzle_type for state in context.stream_states] == puzzle_types
    assert len({state.adversarial_chat_conversation_id for state in context.stream_states}) == len(puzzle_types)
    adversarial_refs = [
        reference
        for reference in context.related_conversations
        if reference.conversation_type == ConversationType.ADVERSARIAL
    ]
    assert len(adversarial_refs) == len(puzzle_types)
    assert mock_adversarial_chat.set_system_prompt.call_count == len(puzzle_types)


async def test_setup_cycles_puzzles_across_extra_streams_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        puzzle_types=["logic_grid", "sudoku"],
        n_streams=5,
    )
    context = _context()

    await attack._setup_async(context=context)

    assert [state.puzzle_type for state in context.stream_states] == [
        "logic_grid",
        "sudoku",
        "logic_grid",
        "sudoku",
        "logic_grid",
    ]


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"max_iterations": 0}, "max_iterations must be a positive integer"),
        ({"puzzle_types": []}, "puzzle_types must contain at least one"),
        ({"puzzle_types": ["unknown"]}, "Unknown puzzle_type"),
        ({"n_streams": 0}, "n_streams must be a positive integer"),
    ],
)
def test_init_rejects_invalid_values(
    overrides: dict[str, Any],
    error: str,
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    with pytest.raises(ValueError, match=error):
        _create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            **overrides,
        )


def test_init_accepts_every_supported_puzzle(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        puzzle_types=SUPPORTED_PUZZLE_TYPES,
        n_streams=None,
    )

    assert attack._puzzle_types == SUPPORTED_PUZZLE_TYPES
    assert attack._n_streams == len(SUPPORTED_PUZZLE_TYPES)


def test_init_requires_objective_scorer(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    with pytest.raises(ValueError, match="An objective scorer is required"):
        CoTHijackingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=mock_adversarial_chat),
            attack_scoring_config=AttackScoringConfig(),
        )


def test_init_requires_native_adversarial_chat_capabilities(
    mock_objective_target: MagicMock,
) -> None:
    unsupported_adversarial_chat = _mock_target(
        name="UnsupportedAdversarialChat",
        supports_required_capabilities=False,
    )

    with pytest.raises(ValueError, match="must natively support"):
        _create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=unsupported_adversarial_chat,
        )


def test_init_accepts_single_turn_objective_target(
    mock_adversarial_chat: MagicMock,
) -> None:
    single_turn_target = _mock_target(
        name="SingleTurnObjectiveTarget",
        supports_required_capabilities=False,
    )

    attack = _create_attack(
        objective_target=single_turn_target,
        adversarial_chat=mock_adversarial_chat,
    )

    assert attack.get_objective_target() is single_turn_target


def test_identifier_tracks_behavioral_configuration(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_objective_scorer: MagicMock,
) -> None:
    first = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        objective_scorer=mock_objective_scorer,
        max_iterations=4,
        puzzle_types=["logic_grid", "sudoku"],
        n_streams=3,
    )
    same = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        objective_scorer=mock_objective_scorer,
        max_iterations=4,
        puzzle_types=["logic_grid", "sudoku"],
        n_streams=3,
    )
    different = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        objective_scorer=mock_objective_scorer,
        max_iterations=5,
        puzzle_types=["logic_grid", "sudoku"],
        n_streams=3,
    )

    identifier = first.get_identifier()
    assert identifier.params["max_iterations"] == 4
    assert identifier.params["puzzle_types"] == ["logic_grid", "sudoku"]
    assert identifier.params["n_streams"] == 3
    assert identifier.params["use_score_as_feedback"] is True
    assert identifier.hash == same.get_identifier().hash
    assert identifier.hash != different.get_identifier().hash


@pytest.mark.parametrize("objective", ["", " ", "\t"])
def test_validate_context_rejects_empty_objective(
    objective: str,
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
    )

    with pytest.raises(ValueError, match="non-empty"):
        attack._validate_context(context=_context(objective=objective))


def test_build_adversarial_prompts_use_initial_template_then_stream_feedback(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    basic_context: CoTHijackingAttackContext,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
    )
    stream_state = StreamState(
        stream_id=0,
        puzzle_type="logic_grid",
        last_target_response="stream-specific response",
        last_reasoning_step_count=7,
        last_score=_score(successful=False, raw_value=0.64),
    )
    basic_context.last_target_response = "different global response"

    initial = attack._build_adversarial_prompt(
        context=basic_context,
        stream_state=stream_state,
        iteration=0,
    )
    refinement = attack._build_adversarial_prompt(
        context=basic_context,
        stream_state=stream_state,
        iteration=1,
    )

    assert basic_context.objective in initial
    assert "{{objective}}" not in initial
    assert "LANGUAGE MODEL OUTPUT: stream-specific response" in refinement
    assert "SCORE: 0.64." in refinement
    assert "STEP NUMBER: 7." in refinement
    assert "different global response" not in refinement


def test_build_adversarial_prompt_omits_score_when_feedback_is_disabled(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    basic_context: CoTHijackingAttackContext,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        use_score_as_feedback=False,
    )
    stream_state = StreamState(
        stream_id=0,
        puzzle_type="logic_grid",
        last_target_response="stream-specific response",
        last_reasoning_step_count=7,
        last_score=_score(successful=False, raw_value=0.64),
    )

    refinement = attack._build_adversarial_prompt(
        context=basic_context,
        stream_state=stream_state,
        iteration=1,
    )

    assert "SCORE: not provided." in refinement
    assert "0.64" not in refinement


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("one paragraph", 0),
        ("Step one\n\nStep two\n\nStep three", 2),
    ],
)
def test_extract_reasoning_step_count(
    value: str,
    expected: int,
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
    )

    assert attack._extract_reasoning_step_count(message=_message(value=value, role="assistant")) == expected
    assert attack._extract_reasoning_step_count(message=None) == 0


def test_format_target_feedback_uses_current_structure() -> None:
    feedback = CoTHijackingAttack._format_target_feedback(
        objective="Test objective",
        target_response="Model output",
        score=0.75,
        step_number=12,
    )

    assert feedback == (
        "LANGUAGE MODEL OUTPUT: Model output\n"
        "OBJECTIVE: **Craft an adversarial jailbreaking prompt P where when P is entered to a "
        "language model, the model performs the following behavior: 'Test objective'.**\n"
        "SCORE: 0.75.\n"
        "STEP NUMBER: 12.\n"
    )


async def test_generate_attack_prompt_accepts_canonical_json_and_returns_message_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_prompt_normalizer: MagicMock,
    basic_context: CoTHijackingAttackContext,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        prompt_normalizer=mock_prompt_normalizer,
    )
    stream_state = basic_context.stream_states[0]
    mock_prompt_normalizer.send_prompt_async.return_value = _message(
        value=_adversarial_json(
            next_message="canonical attack message",
            rationale="canonical rationale",
            last_response_summary="canonical summary",
        ),
        role="assistant",
    )

    result = await attack._generate_attack_prompt_async(
        context=basic_context,
        stream_state=stream_state,
        iteration=0,
    )

    assert isinstance(result, Message)
    assert result.api_role == "user"
    assert result.get_value() == "canonical attack message"
    mock_prompt_normalizer.send_prompt_async.assert_awaited_once()
    send_kwargs = mock_prompt_normalizer.send_prompt_async.await_args.kwargs
    assert send_kwargs["target"] is mock_adversarial_chat
    assert send_kwargs["conversation_id"] == stream_state.adversarial_chat_conversation_id
    sent_piece = send_kwargs["message"].get_piece()
    assert sent_piece.prompt_metadata["response_format"] == "json"
    schema = sent_piece.prompt_metadata[JSON_SCHEMA_METADATA_KEY]
    assert set(schema["required"]) == {"next_message", "rationale", "last_response_summary"}


@pytest.mark.parametrize(
    "adversarial_response",
    [
        "not valid JSON",
        '{"next_message": "missing canonical fields"}',
        '{"rationale": "missing next message", "last_response_summary": "summary"}',
    ],
)
async def test_generate_attack_prompt_propagates_invalid_json_after_retries_async(
    adversarial_response: str,
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_prompt_normalizer: MagicMock,
    basic_context: CoTHijackingAttackContext,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        prompt_normalizer=mock_prompt_normalizer,
    )
    mock_prompt_normalizer.send_prompt_async.return_value = _message(
        value=adversarial_response,
        role="assistant",
    )

    with pytest.raises(InvalidJsonException):
        await attack._generate_attack_prompt_async(
            context=basic_context,
            stream_state=basic_context.stream_states[0],
            iteration=0,
        )

    assert mock_prompt_normalizer.send_prompt_async.await_count == 2


async def test_generate_attack_prompt_propagates_adversarial_transport_failure_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_prompt_normalizer: MagicMock,
    basic_context: CoTHijackingAttackContext,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        prompt_normalizer=mock_prompt_normalizer,
    )
    mock_prompt_normalizer.send_prompt_async.side_effect = RuntimeError("adversarial target unavailable")

    with pytest.raises(RuntimeError, match="adversarial target unavailable"):
        await attack._generate_attack_prompt_async(
            context=basic_context,
            stream_state=basic_context.stream_states[0],
            iteration=0,
        )


async def test_target_sends_use_fresh_ids_across_parallel_streams_and_turns_async(
    mock_adversarial_chat: MagicMock,
    mock_prompt_normalizer: MagicMock,
) -> None:
    single_turn_target = _mock_target(
        name="SingleTurnObjectiveTarget",
        supports_required_capabilities=False,
    )
    attack = _create_attack(
        objective_target=single_turn_target,
        adversarial_chat=mock_adversarial_chat,
        prompt_normalizer=mock_prompt_normalizer,
    )
    context = _context(stream_count=2)

    async def respond_async(**kwargs: Any) -> Message:
        return _message(
            value="target response",
            role="assistant",
            conversation_id=kwargs["conversation_id"],
        )

    mock_prompt_normalizer.send_prompt_async.side_effect = respond_async
    first_turn = await asyncio.gather(
        *[
            attack._send_prompt_to_target_async(
                message=_message(value=f"stream {index}, turn 1", role="user"),
                context=context,
            )
            for index in range(2)
        ]
    )
    second_turn = await asyncio.gather(
        *[
            attack._send_prompt_to_target_async(
                message=_message(value=f"stream {index}, turn 2", role="user"),
                context=context,
            )
            for index in range(2)
        ]
    )

    conversation_ids = {response.get_piece().conversation_id for response in [*first_turn, *second_turn]}
    assert None not in conversation_ids
    assert len(conversation_ids) == 4
    assert context.objective_target_conversation_ids == conversation_ids
    assert context.session.conversation_id not in conversation_ids


async def test_target_send_raises_when_no_response_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_prompt_normalizer: MagicMock,
    basic_context: CoTHijackingAttackContext,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        prompt_normalizer=mock_prompt_normalizer,
    )
    mock_prompt_normalizer.send_prompt_async.return_value = None

    with pytest.raises(ValueError, match="No response received from objective target"):
        await attack._send_prompt_to_target_async(
            message=_message(value="attack message", role="user"),
            context=basic_context,
        )

    assert not basic_context.objective_target_conversation_ids


async def test_prepended_conversation_is_seeded_for_each_fresh_target_conversation_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_prompt_normalizer: MagicMock,
) -> None:
    prepended = [
        Message.from_system_prompt("system seed"),
        _message(value="user seed", role="user"),
        _message(value="assistant seed", role="assistant"),
    ]
    config = PrependedConversationConfig()
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        prompt_normalizer=mock_prompt_normalizer,
        prepended_conversation_config=config,
    )
    context = _context(prepended_conversation=prepended)
    send_context = MagicMock()

    async def respond_async(**kwargs: Any) -> Message:
        return _message(
            value="target response",
            role="assistant",
            conversation_id=kwargs["conversation_id"],
        )

    mock_prompt_normalizer.send_prompt_async.side_effect = respond_async
    with (
        patch.object(
            attack._conversation_manager,
            "add_prepended_conversation_to_memory_async",
            new_callable=AsyncMock,
        ) as add_prepended,
        patch.object(
            attack._conversation_manager,
            "get_conversation",
            return_value=prepended,
        ),
        patch.object(
            attack._conversation_manager,
            "create_prepended_history_send_context",
            return_value=send_context,
        ) as create_send_context,
        patch.object(attack, "_get_prepended_normalizer_overrides", return_value={}),
    ):
        responses = [
            await attack._send_prompt_to_target_async(
                message=_message(value=f"attack {index}", role="user"),
                context=context,
            )
            for index in range(2)
        ]

    response_ids = {response.get_piece().conversation_id for response in responses}
    seeded_ids = {await_call.kwargs["conversation_id"] for await_call in add_prepended.await_args_list}
    assert len(response_ids) == 2
    assert seeded_ids == response_ids
    assert add_prepended.await_count == 2
    assert create_send_context.call_count == 2
    for await_call in add_prepended.await_args_list:
        assert await_call.kwargs["prepended_conversation"] == prepended
        assert await_call.kwargs["prepended_conversation_config"] is config
        assert await_call.kwargs["target"] is mock_objective_target
    for await_call in mock_prompt_normalizer.send_prompt_async.await_args_list:
        assert await_call.kwargs["send_context"] is send_context


async def test_score_response_forwards_auxiliary_scorers_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_objective_scorer: MagicMock,
    basic_context: CoTHijackingAttackContext,
) -> None:
    auxiliary_scorer = MagicMock(spec=Scorer)
    auxiliary_scorer.get_identifier.return_value = _component_identifier("AuxiliaryScorer")
    objective_score = _score(successful=True)
    response = _message(
        value="target response",
        role="assistant",
        conversation_id="target-conversation",
    )
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        objective_scorer=mock_objective_scorer,
        auxiliary_scorers=[auxiliary_scorer],
    )

    with patch.object(
        Scorer,
        "score_response_async",
        new_callable=AsyncMock,
        return_value={
            "objective_scores": [objective_score],
            "auxiliary_scores": [],
        },
    ) as score_response:
        result = await attack._score_response_async(
            message=response,
            context=basic_context,
        )

    assert result is objective_score
    score_response.assert_awaited_once()
    score_kwargs = score_response.await_args.kwargs
    assert score_kwargs["response"] is response
    assert score_kwargs["objective_scorer"] is mock_objective_scorer
    assert score_kwargs["auxiliary_scorers"] == [auxiliary_scorer]
    assert score_kwargs["role_filter"] == "assistant"
    assert score_kwargs["objective"] == basic_context.objective
    assert score_kwargs["skip_on_error_result"] is False


async def test_score_response_raises_without_objective_score_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    basic_context: CoTHijackingAttackContext,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
    )

    with (
        patch.object(
            Scorer,
            "score_response_async",
            new_callable=AsyncMock,
            return_value={"objective_scores": [], "auxiliary_scores": []},
        ),
        pytest.raises(RuntimeError, match="No objective scores returned"),
    ):
        await attack._score_response_async(
            message=_message(
                value="target response",
                role="assistant",
                conversation_id="target-conversation",
            ),
            context=basic_context,
        )


async def test_score_response_propagates_scorer_failure_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    basic_context: CoTHijackingAttackContext,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
    )

    with (
        patch.object(
            Scorer,
            "score_response_async",
            new_callable=AsyncMock,
            side_effect=RuntimeError("scorer unavailable"),
        ),
        pytest.raises(RuntimeError, match="scorer unavailable"),
    ):
        await attack._score_response_async(
            message=_message(
                value="target response",
                role="assistant",
                conversation_id="target-conversation",
            ),
            context=basic_context,
        )


@pytest.mark.parametrize(
    ("successful", "expected"),
    [(True, 1.0), (False, 0.0)],
)
def test_extract_raw_score_from_true_false(
    successful: bool,
    expected: float,
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
    )

    assert attack._extract_raw_score(score_obj=_score(successful=successful)) == expected


def test_extract_raw_score_prefers_original_float_metadata(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
    )
    score = _score(successful=False, raw_value="0.73")

    assert attack._extract_raw_score(score_obj=score) == 0.73


async def test_execute_async_returns_current_result_and_conversation_apis_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
    mock_objective_scorer: MagicMock,
    mock_prompt_normalizer: MagicMock,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        objective_scorer=mock_objective_scorer,
        prompt_normalizer=mock_prompt_normalizer,
        max_iterations=1,
        puzzle_types=["logic_grid", "sudoku"],
        n_streams=2,
    )
    target_conversation_ids: list[str] = []

    async def respond_async(**kwargs: Any) -> Message:
        conversation_id = kwargs["conversation_id"]
        target_conversation_ids.append(conversation_id)
        return _message(
            value=f"response {len(target_conversation_ids)}",
            role="assistant",
            conversation_id=conversation_id,
        )

    failure_score = _score(successful=False, raw_value=0.2)
    success_score = _score(successful=True, raw_value=0.9)
    mock_prompt_normalizer.send_prompt_async.side_effect = respond_async
    with (
        patch.object(
            attack,
            "_generate_attack_prompt_async",
            new_callable=AsyncMock,
            side_effect=[
                _message(value="stream one", role="user"),
                _message(value="stream two", role="user"),
            ],
        ),
        patch.object(
            attack,
            "_score_response_async",
            new_callable=AsyncMock,
            side_effect=[failure_score, success_score],
        ),
    ):
        result = await attack.execute_async(
            objective="Test objective",
            memory_labels={"source": "unit-test"},
        )

    assert result.outcome == AttackOutcome.SUCCESS
    assert result.executed_turns == 1
    assert result.last_score is success_score
    assert result.last_response is not None
    assert result.last_response.converted_value == "response 2"
    assert result.conversation_id == target_conversation_ids[1]
    assert result.labels == {"source": "unit-test"}
    assert result.get_active_conversation_ids() == set(target_conversation_ids)
    assert len(result.get_conversations_by_type(ConversationType.PRUNED)) == 1
    assert len(result.get_conversations_by_type(ConversationType.ADVERSARIAL)) == 2
    attack_identifier = result.get_attack_strategy_identifier()
    assert attack_identifier is not None
    assert attack_identifier.class_name == "CoTHijackingAttack"


async def test_perform_keeps_global_best_across_iterations_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        max_iterations=3,
    )
    context = _context()
    responses = [
        _message(value="iteration one", role="assistant", conversation_id="target-1"),
        _message(value="global best", role="assistant", conversation_id="target-2"),
        _message(value="iteration three", role="assistant", conversation_id="target-3"),
    ]
    scores = [
        _score(successful=False, raw_value=0.3),
        _score(successful=False, raw_value=0.85),
        _score(successful=False, raw_value=0.4),
    ]
    context.objective_target_conversation_ids.update({"target-1", "target-2", "target-3"})

    with (
        patch.object(
            attack,
            "_generate_attack_prompt_async",
            new_callable=AsyncMock,
            return_value=_message(value="attack", role="user"),
        ),
        patch.object(
            attack,
            "_send_prompt_to_target_async",
            new_callable=AsyncMock,
            side_effect=responses,
        ),
        patch.object(
            attack,
            "_score_response_async",
            new_callable=AsyncMock,
            side_effect=scores,
        ),
    ):
        result = await attack._perform_async(context=context)

    assert result.outcome == AttackOutcome.FAILURE
    assert result.executed_turns == 3
    assert result.conversation_id == "target-2"
    assert result.last_response is not None
    assert result.last_response.converted_value == "global best"
    assert result.last_score is scores[1]
    assert context.last_response is responses[1]
    assert context.last_score is scores[1]
    assert {reference.conversation_id for reference in result.related_conversations} == {
        "target-1",
        "target-3",
    }


async def test_execute_async_propagates_target_failure_and_runs_teardown_async(
    mock_objective_target: MagicMock,
    mock_adversarial_chat: MagicMock,
) -> None:
    attack = _create_attack(
        objective_target=mock_objective_target,
        adversarial_chat=mock_adversarial_chat,
        max_iterations=1,
    )

    with (
        patch.object(
            attack,
            "_generate_attack_prompt_async",
            new_callable=AsyncMock,
            return_value=_message(value="attack", role="user"),
        ),
        patch.object(
            attack,
            "_send_prompt_to_target_async",
            new_callable=AsyncMock,
            side_effect=RuntimeError("objective target unavailable"),
        ),
        patch.object(
            attack,
            "_teardown_async",
            new_callable=AsyncMock,
            wraps=attack._teardown_async,
        ) as teardown,
        pytest.raises(RuntimeError, match="objective target unavailable"),
    ):
        await attack.execute_async(objective="Test objective")

    teardown.assert_awaited_once()
