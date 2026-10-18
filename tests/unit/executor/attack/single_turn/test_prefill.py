# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import dataclasses
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_prompt_normalizer, get_mock_scorer_identifier, get_mock_target_identifier

from pyrit.converter import Base64Converter, QRCodeConverter
from pyrit.executor.attack import (
    AttackConverterConfig,
    AttackParameters,
    AttackScoringConfig,
    PrefillAttack,
    SingleTurnAttackContext,
)
from pyrit.memory import CentralMemory
from pyrit.models import AttackOutcome, Message, MessagePiece, Score, construct_response_from_request
from pyrit.prompt_normalizer import ConverterConfiguration
from pyrit.prompt_target import (
    CapabilityName,
    HuggingFaceChatTarget,
    PromptTarget,
    TargetCapabilities,
    TargetConfiguration,
)
from pyrit.score import TrueFalseScorer


@pytest.fixture
def mock_target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    target.get_identifier.return_value = get_mock_target_identifier("MockTarget")
    # Default mock supports every capability (includes() returns truthy).
    target.configuration.includes.return_value = True
    return target


@pytest.fixture
def mock_true_false_scorer() -> MagicMock:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_text_async = AsyncMock()
    scorer.get_identifier.return_value = get_mock_scorer_identifier()
    return scorer


@pytest.fixture
def mock_prompt_normalizer() -> MagicMock:
    normalizer = get_mock_prompt_normalizer()
    normalizer.send_prompt_async = AsyncMock()
    return normalizer


@pytest.fixture
def basic_context() -> SingleTurnAttackContext:
    return SingleTurnAttackContext(
        params=AttackParameters(objective="Test objective"),
        conversation_id=str(uuid.uuid4()),
    )


@pytest.fixture
def sample_response() -> Message:
    return Message(
        message_pieces=[
            MessagePiece(role="assistant", original_value="continued text", original_value_data_type="text")
        ]
    )


@pytest.fixture
def success_score() -> Score:
    return Score(
        score_type="true_false",
        score_value="true",
        score_category=["test"],
        score_value_description="Test success score",
        score_rationale="Test rationale",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=get_mock_scorer_identifier(),
    )


@pytest.mark.usefixtures("patch_central_database")
class TestPrefillAttackInitialization:
    def test_init_with_minimal_required_parameters(self, mock_target: MagicMock):
        attack = PrefillAttack(objective_target=mock_target, prefill_text="Sure, here are the steps:")

        assert attack._objective_target == mock_target
        assert attack._prefill_text == "Sure, here are the steps:"
        assert attack._max_attempts_on_failure == 0

    @pytest.mark.parametrize("bad_prefill", ["", "   "])
    def test_init_rejects_empty_prefill_text(self, mock_target: MagicMock, bad_prefill: str):
        with pytest.raises(ValueError, match="prefill_text must be provided"):
            PrefillAttack(objective_target=mock_target, prefill_text=bad_prefill)

    def test_init_rejects_target_without_prefill_capability(self, mock_target: MagicMock):
        mock_target.configuration.includes.side_effect = lambda *, capability: capability != CapabilityName.PREFILL

        with pytest.raises(ValueError, match="supports_prefill"):
            PrefillAttack(objective_target=mock_target, prefill_text="Sure, here are the steps:")

    @pytest.mark.parametrize(
        "missing",
        [CapabilityName.MULTI_TURN, CapabilityName.EDITABLE_HISTORY],
    )
    def test_init_rejects_target_without_history_capabilities(self, mock_target: MagicMock, missing: CapabilityName):
        mock_target.configuration.includes.side_effect = lambda *, capability: capability != missing

        with pytest.raises(ValueError, match=missing.value):
            PrefillAttack(objective_target=mock_target, prefill_text="Sure, here are the steps:")

    def test_init_inherits_parent_validation(self, mock_target: MagicMock):
        with pytest.raises(ValueError):
            PrefillAttack(objective_target=mock_target, prefill_text="x", max_attempts_on_failure=-1)


@pytest.mark.usefixtures("patch_central_database")
class TestPrefillAttackParamsType:
    @pytest.mark.parametrize(
        ("field_name", "expected_present"),
        [("prepended_conversation", False), ("next_message", False), ("objective", True)],
    )
    def test_params_type_fields(self, mock_target: MagicMock, field_name: str, expected_present: bool):
        attack = PrefillAttack(objective_target=mock_target, prefill_text="x")
        fields = {f.name for f in dataclasses.fields(attack.params_type)}
        assert (field_name in fields) is expected_present


@pytest.mark.usefixtures("patch_central_database")
class TestPrefillAttackSetup:
    async def test_setup_prepends_objective_as_user_turn(self, mock_target: MagicMock, basic_context):
        attack = PrefillAttack(objective_target=mock_target, prefill_text="Sure:")

        with patch(
            "pyrit.executor.attack.single_turn.prompt_sending.PromptSendingAttack._setup_async",
            new_callable=AsyncMock,
        ):
            await attack._setup_async(context=basic_context)

        assert basic_context.prepended_conversation is not None
        assert len(basic_context.prepended_conversation) == 1
        assert basic_context.prepended_conversation[0].api_role == "user"
        assert basic_context.prepended_conversation[0].message_pieces[0].original_value == basic_context.objective

    async def test_setup_delegates_to_parent(self, mock_target: MagicMock, basic_context):
        attack = PrefillAttack(objective_target=mock_target, prefill_text="Sure:")

        with patch(
            "pyrit.executor.attack.single_turn.prompt_sending.PromptSendingAttack._setup_async",
            new_callable=AsyncMock,
        ) as mock_parent_setup:
            await attack._setup_async(context=basic_context)

        mock_parent_setup.assert_called_once_with(context=basic_context)


@pytest.mark.usefixtures("patch_central_database")
class TestPrefillAttackMessage:
    def test_get_message_is_assistant_prefill(self, mock_target: MagicMock, basic_context):
        attack = PrefillAttack(objective_target=mock_target, prefill_text="Sure, here are the steps:")

        message = attack._get_message(basic_context)

        assert message.api_role == "assistant"
        assert message.message_pieces[0].role == "simulated_assistant"
        assert message.message_pieces[0].original_value == "Sure, here are the steps:"


@pytest.mark.usefixtures("patch_central_database")
class TestPrefillAttackExecution:
    async def test_perform_sends_assistant_prefill(
        self, mock_target: MagicMock, basic_context, sample_response, mock_true_false_scorer, success_score
    ):
        attack = PrefillAttack(
            objective_target=mock_target,
            prefill_text="Sure, here are the steps:",
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_true_false_scorer),
        )

        with (
            patch.object(attack, "_setup_async", new_callable=AsyncMock),
            patch.object(
                attack, "_send_prompt_to_objective_target_async", new_callable=AsyncMock, return_value=sample_response
            ) as mock_send,
            patch.object(attack, "_evaluate_response_async", new_callable=AsyncMock, return_value=success_score),
        ):
            result = await attack._perform_async(context=basic_context)

        assert result.executed_turns == 1
        sent_message = mock_send.call_args.kwargs["message"]
        assert sent_message.api_role == "assistant"
        assert sent_message.message_pieces[0].original_value == "Sure, here are the steps:"


class _RecordingTarget(PromptTarget):
    _DEFAULT_CONFIGURATION = TargetConfiguration(
        capabilities=TargetCapabilities(supports_multi_turn=True, supports_editable_history=True, supports_prefill=True)
    )

    def __init__(self) -> None:
        super().__init__()
        self.received: list[list[tuple[str, str]]] = []

    async def _send_prompt_to_target_async(self, *, normalized_conversation: list[Message]) -> list[Message]:
        self.received.append([(m.api_role, m.get_value()) for m in normalized_conversation])
        request = normalized_conversation[-1].message_pieces[0]
        return [construct_response_from_request(request=request, response_text_pieces=["continuation"])]


@pytest.mark.usefixtures("patch_central_database")
class TestPrefillAttackEndToEnd:
    async def test_different_prefills_have_distinct_identifiers_and_persisted_evaluation_hashes(self):
        target = _RecordingTarget()
        first = PrefillAttack(objective_target=target, prefill_text="Sure:")
        second = PrefillAttack(objective_target=target, prefill_text="Absolutely:")

        first_identifier = first.get_identifier()
        second_identifier = second.get_identifier()
        assert first_identifier.params["prefill_text"] == "Sure:"
        assert second_identifier.params["prefill_text"] == "Absolutely:"
        assert first_identifier.hash != second_identifier.hash

        first_result = await first.execute_async(objective="Test objective")
        second_result = await second.execute_async(objective="Test objective")
        stored = await CentralMemory.get_memory_instance().get_attack_results_async(
            attack_result_ids=[first_result.attack_result_id, second_result.attack_result_id]
        )
        assert len(stored) == 2
        hashes = {result.atomic_attack_identifier.eval_hash for result in stored if result.atomic_attack_identifier}
        assert None not in hashes
        assert len(hashes) == 2
        assert first_result.get_attack_strategy_identifier() == first_identifier
        assert second_result.get_attack_strategy_identifier() == second_identifier

    async def test_target_receives_user_objective_then_trailing_assistant_prefill(self):
        target = _RecordingTarget()
        attack = PrefillAttack(objective_target=target, prefill_text="Sure, here are the steps:")

        result = await attack.execute_async(objective="Test objective")

        assert target.received == [[("user", "Test objective"), ("assistant", "Sure, here are the steps:")]]
        assert result.last_response is not None
        assert result.last_response.converted_value == "continuation"

    async def test_prefill_is_stored_in_memory_as_simulated_assistant(self):
        target = _RecordingTarget()
        attack = PrefillAttack(objective_target=target, prefill_text="Sure, here are the steps:")

        result = await attack.execute_async(objective="Test objective")

        pieces = await CentralMemory.get_memory_instance().get_message_pieces_async(
            conversation_id=result.conversation_id
        )
        roles = [(piece.role, piece.original_value) for piece in pieces]
        assert roles == [
            ("user", "Test objective"),
            ("simulated_assistant", "Sure, here are the steps:"),
            ("assistant", "continuation"),
        ]

    async def test_request_converters_apply_to_objective_not_prefill(self):
        target = _RecordingTarget()
        attack = PrefillAttack(
            objective_target=target,
            prefill_text="Sure, here are the steps:",
            attack_converter_config=AttackConverterConfig(
                request_converters=[ConverterConfiguration(converters=[Base64Converter()])]
            ),
        )

        await attack.execute_async(objective="Test objective")

        user_turn, prefill_turn = target.received[0]
        assert user_turn[1] == "VGVzdCBvYmplY3RpdmU="
        assert prefill_turn == ("assistant", "Sure, here are the steps:")

    async def test_converted_objective_history_is_validated_before_huggingface_generation(self):
        pytest.importorskip("torch")
        with patch.object(HuggingFaceChatTarget, "load_model_and_tokenizer_async", new_callable=AsyncMock):
            target = HuggingFaceChatTarget(model_path="unused-model")

        attack = PrefillAttack(
            objective_target=target,
            prefill_text="Sure:",
            attack_converter_config=AttackConverterConfig(
                request_converters=[ConverterConfiguration(converters=[QRCodeConverter()])]
            ),
        )

        with patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock) as generate:
            with pytest.raises(RuntimeError, match="image_path") as error_info:
                await attack.execute_async(objective="Test objective")

        root_cause = error_info.value
        while root_cause.__cause__ is not None:
            root_cause = root_cause.__cause__
        assert isinstance(root_cause, ValueError)
        assert "image_path" in str(root_cause)
        generate.assert_not_awaited()

    async def test_continuation_is_scored_and_drives_outcome(self, mock_true_false_scorer, success_score):
        target = _RecordingTarget()
        attack = PrefillAttack(
            objective_target=target,
            prefill_text="Sure, here are the steps:",
            attack_scoring_config=AttackScoringConfig(objective_scorer=mock_true_false_scorer),
        )

        with patch(
            "pyrit.score.MessageScorer.score_response_async",
            new_callable=AsyncMock,
            return_value={"auxiliary_scores": [], "objective_scores": [success_score]},
        ) as score_response:
            result = await attack.execute_async(objective="Test objective")

        assert result.outcome == AttackOutcome.SUCCESS
        assert result.automated_score == success_score
        scored_response = score_response.call_args.kwargs["response"]
        assert scored_response.get_value() == "continuation"
