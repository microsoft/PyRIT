# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import get_mock_target_identifier

from pyrit.models import ComponentIdentifier, Message, MessagePiece, ScorerIdentifier
from pyrit.prompt_target import PromptTarget
from pyrit.score import (
    CallableResponseHandler,
    JsonSchemaResponseHandler,
    SelfAskGeneralTrueFalseScorer,
)


@pytest.fixture
def general_scorer_response() -> Message:
    json_response = (
        dedent(
            """
        {"score_value": "True",
         "rationale": "This is the rationale.",
         "description": "This is the description."}
        """
        )
        .strip()
        .replace("\n", " ")
    )

    return Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])


async def test_general_scorer_score_async(patch_central_database, general_scorer_response: Message):
    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[general_scorer_response])

    scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        category="test_category",
    )
    assert scorer

    score = await scorer.score_text_async(
        text="test prompt",
    )

    assert len(score) == 1
    assert score[0].score_value == "true"
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description


async def test_general_scorer_score_async_with_prompt_f_string(
    general_scorer_response: Message, patch_central_database
):
    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[general_scorer_response])

    scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        prompt_format_string="Is this cyberbullying? {prompt}",
        category="test_category",
    )

    score = await scorer.score_text_async(
        text="this is a test prompt",
    )

    assert len(score) == 1
    assert score[0].score_value == "true"
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description
    args = chat_target.send_prompt_async.call_args
    prompt = args[1]["message"].message_pieces[0].converted_value
    assert prompt == "Is this cyberbullying? this is a test prompt"


async def test_general_scorer_score_async_handles_custom_keys(patch_central_database):
    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    assert chat_target

    json_response = (
        dedent(
            """
        {"score_value": "false",
         "rationale_diff_key": "This is the rationale.",
         "description": "This is the description."}
        """
        )
        .strip()
        .replace("\n", " ")
    )
    # Simulate a response missing some keys
    response = Message(message_pieces=[MessagePiece(role="assistant", original_value=json_response)])
    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    assert chat_target

    scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="This is a system prompt.",
        prompt_format_string="This is a prompt format string.",
        category="test_category",
        rationale_output_key="rationale_diff_key",
    )
    score = await scorer.score_text_async(text="this is a test prompt")
    assert len(score) == 1
    assert score[0].score_value == "false"
    assert "This is the rationale." in score[0].score_rationale
    assert "This is the description." in score[0].score_value_description


def test_true_false_get_scorer_metrics_returns_none_when_eval_hash_is_none(patch_central_database):
    """Test that TrueFalseScorer.get_scorer_metrics returns None when eval_hash is None."""
    from unittest.mock import patch as _patch

    from pyrit.score import (
        SelfAskTrueFalseScorer,
    )

    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskTrueFalseScorer(chat_target=chat_target)
    mock_identifier = MagicMock()
    mock_identifier.eval_hash = None
    with _patch.object(scorer, "get_identifier", return_value=mock_identifier):
        result = scorer.get_scorer_metrics()
    assert result is None


def test_true_false_get_scorer_metrics_returns_metrics_when_eval_hash_is_set(patch_central_database):
    """Test that TrueFalseScorer.get_scorer_metrics returns metrics when eval_hash is set."""
    from unittest.mock import patch as _patch

    from pyrit.score import (
        SelfAskTrueFalseScorer,
    )

    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer = SelfAskTrueFalseScorer(chat_target=chat_target)

    mock_identifier = MagicMock()
    mock_identifier.eval_hash = "abc123"

    mock_metrics = MagicMock()
    mock_result_file = MagicMock()
    mock_result_file.exists.return_value = True

    with _patch.object(scorer, "get_identifier", return_value=mock_identifier):
        with _patch(
            "pyrit.score.scorer_evaluation.scorer_metrics_io.find_objective_metrics_by_eval_hash",
            return_value=mock_metrics,
        ) as mock_find:
            with _patch(
                "pyrit.common.path.SCORER_EVALS_PATH",
                new=MagicMock(__truediv__=MagicMock(return_value=mock_result_file)),
            ):
                result = scorer.get_scorer_metrics()

    assert result is mock_metrics
    mock_find.assert_called_once()


def test_general_true_false_no_chat_target_raises():
    with pytest.raises(ValueError, match="A chat_target must be provided"):
        SelfAskGeneralTrueFalseScorer(chat_target=None, system_prompt_format_string="prompt")


def _response(body: str) -> Message:
    return Message(message_pieces=[MessagePiece(role="assistant", original_value=body)])


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("body", "configured_category", "expected_category"),
    [
        ('{"score_value": "true", "rationale": "r", "category": "violence"}', "harm", ["harm"]),
        ('{"score_value": "true", "rationale": "r", "category": "harm"}', "harm", ["harm"]),
        ('{"score_value": "true", "rationale": "r"}', "harm", ["harm"]),
        ('{"score_value": "true", "rationale": "r", "category": null}', "harm", ["harm"]),
        ('{"score_value": "true", "rationale": "r", "category": "violence"}', None, ["violence"]),
    ],
    ids=[
        "configured_category_wins",
        "matching_categories",
        "no_response_category",
        "null_response_category",
        "no_configured_category",
    ],
)
async def test_general_scorer_category_precedence_async(
    body: str, configured_category: str | None, expected_category: list[str]
) -> None:
    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.set_system_prompt_async = AsyncMock()
    chat_target.send_prompt_async = AsyncMock(return_value=[_response(body)])
    scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target, system_prompt_format_string="Prompt.", category=configured_category
    )

    score = await scorer.score_text_async(text="prompt", objective="obj")

    assert score[0].score_category == expected_category
    assert score[0].score_value == "true"
    chat_target.send_prompt_async.assert_awaited_once()


@pytest.mark.parametrize(
    ("key_name", "custom_value"),
    [
        ("score_value_output_key", "custom_score"),
        ("rationale_output_key", "custom_rationale"),
        ("description_output_key", "custom_description"),
        ("metadata_output_key", "custom_metadata"),
        ("category_output_key", "custom_category"),
    ],
)
def test_general_true_false_scorer_identifier_differs_by_output_keys(key_name: str, custom_value: str) -> None:
    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    default_scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="Score this.",
    )
    custom_scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="Score this.",
        **{key_name: custom_value},
    )

    default_ident = default_scorer.get_identifier()
    custom_ident = custom_scorer.get_identifier()

    assert default_ident.unique_name != custom_ident.unique_name
    assert default_ident.hash != custom_ident.hash
    assert default_ident.eval_hash != custom_ident.eval_hash

    default_wire = default_ident.children["response_handler"].children["response_handler"]
    custom_wire = custom_ident.children["response_handler"].children["response_handler"]
    assert isinstance(default_wire, ComponentIdentifier)
    assert isinstance(custom_wire, ComponentIdentifier)
    assert custom_wire.params[key_name] == custom_value
    assert default_wire.params[key_name] != custom_value


def test_general_true_false_scorer_identifier_deterministic() -> None:
    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    scorer_1 = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="Score this.",
        score_value_output_key="verdict",
        rationale_output_key="reason",
    )
    scorer_2 = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="Score this.",
        score_value_output_key="verdict",
        rationale_output_key="reason",
    )

    ident_1 = scorer_1.get_identifier()
    ident_2 = scorer_2.get_identifier()
    assert ident_1.unique_name == ident_2.unique_name
    assert ident_1.hash == ident_2.hash
    assert ident_1.eval_hash == ident_2.eval_hash
    assert ident_1.children == ident_2.children
    round_tripped = ScorerIdentifier.model_validate_json(ident_1.model_dump_json())
    assert round_tripped.hash == ident_1.hash


def test_general_true_false_scorer_identifier_reflects_json_schema_response_handler() -> None:
    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    default_scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="Score this.",
    )
    handler = JsonSchemaResponseHandler(
        score_value_output_key="custom_score_from_handler",
        rationale_output_key="custom_rationale_from_handler",
    )
    scorer = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="Score this.",
        response_handler=handler,
    )

    ident = scorer.get_identifier()
    assert ident.unique_name != default_scorer.get_identifier().unique_name
    wire_ident = ident.children["response_handler"].children["response_handler"]
    assert isinstance(wire_ident, ComponentIdentifier)
    assert wire_ident == handler.get_identifier()
    assert wire_ident.params["score_value_output_key"] == "custom_score_from_handler"
    assert wire_ident.params["rationale_output_key"] == "custom_rationale_from_handler"


@pytest.mark.parametrize(
    ("key_name", "custom_value"),
    [
        ("score_value_output_key", "verdict"),
        ("rationale_output_key", "reason"),
        ("description_output_key", "desc"),
        ("metadata_output_key", "meta"),
        ("category_output_key", "label"),
    ],
)
def test_general_true_false_scorer_identifier_reflects_callable_response_handler(
    key_name: str, custom_value: str
) -> None:
    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    def parser(_: str) -> dict[str, str]:
        return {"score_value": "true", "verdict": "false", "rationale": "r"}

    default_handler = CallableResponseHandler(parser=parser)
    custom_handler = CallableResponseHandler(parser=parser, **{key_name: custom_value})

    scorer_1 = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="Score this.",
        response_handler=default_handler,
    )
    scorer_2 = SelfAskGeneralTrueFalseScorer(
        chat_target=chat_target,
        system_prompt_format_string="Score this.",
        response_handler=custom_handler,
    )

    ident_1 = scorer_1.get_identifier()
    ident_2 = scorer_2.get_identifier()
    assert ident_1.hash != ident_2.hash
    assert ident_1.unique_name != ident_2.unique_name
    assert ident_1.eval_hash != ident_2.eval_hash
    wire_ident = ident_2.children["response_handler"].children["response_handler"]
    assert isinstance(wire_ident, ComponentIdentifier)
    assert wire_ident == custom_handler.get_identifier()
    assert wire_ident.params[key_name] == custom_value
