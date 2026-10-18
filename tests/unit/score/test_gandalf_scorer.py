# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_mock_target_identifier, store_message_async

from pyrit.exceptions.exception_classes import PyritException
from pyrit.memory.memory_interface import MemoryInterface
from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import GandalfLevel, PromptTarget
from pyrit.score import GandalfScorer, MessageScorable


def generate_password_extraction_response(response_text: str, conversation_id: str | None = None) -> Message:
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value=response_text,
                original_value_data_type="text",
                converted_value=response_text,
                converted_value_data_type="text",
                conversation_id=conversation_id or str(uuid.uuid4()),
            )
        ]
    )


def generate_request(conversation_id: str | None = None) -> Message:
    return Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="some request",
                original_value_data_type="text",
                converted_value="some request",
                converted_value_data_type="text",
                conversation_id=conversation_id or str(uuid.uuid4()),
            )
        ]
    )


@patch("pyrit.score.true_false.gandalf_scorer.make_request_and_raise_if_error_async", new_callable=AsyncMock)
@pytest.mark.parametrize("password_correct", [True, False])
@pytest.mark.parametrize("level", [GandalfLevel.LEVEL_1, GandalfLevel.LEVEL_2, GandalfLevel.LEVEL_3])
async def test_gandalf_scorer_score(
    mocked_post, sqlite_instance: MemoryInterface, level: GandalfLevel, password_correct: bool
):
    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")

    conversation_id = str(uuid.uuid4())
    (await sqlite_instance.add_message_to_memory_async(request=generate_request(conversation_id=conversation_id)))
    response = generate_password_extraction_response("SUNSHINE", conversation_id=conversation_id)
    (await sqlite_instance.add_message_to_memory_async(request=response))

    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    scorer = GandalfScorer(level=level, chat_target=chat_target)

    mocked_post.return_value = MagicMock(json=lambda: {"success": password_correct, "message": "Message"})

    scores = await scorer.score_async(scorable=MessageScorable.from_message(await store_message_async(response)))

    assert len(scores) == 1
    assert scores[0].get_value() == password_correct
    assert (
        scores[0].score_value_description
        == "When password is found in text, the score is True. The rationale will contain the password."
    )
    if password_correct:
        assert scores[0].score_rationale == "Password SUNSHINE found! Gandalf response: Message"
    else:
        assert scores[0].score_rationale == "Invalid password found in text. [value=SUNSHINE]"
    assert scores[0].scorer_class_identifier.class_name == "GandalfScorer"


@patch("pyrit.score.true_false.gandalf_scorer.make_request_and_raise_if_error_async", new_callable=AsyncMock)
@pytest.mark.parametrize("level", [GandalfLevel.LEVEL_1, GandalfLevel.LEVEL_2, GandalfLevel.LEVEL_3])
async def test_gandalf_scorer_set_system_prompt(
    mocked_post,
    sqlite_instance: MemoryInterface,
    level: GandalfLevel,
):
    conversation_id = str(uuid.uuid4())
    (await sqlite_instance.add_message_to_memory_async(request=generate_request(conversation_id=conversation_id)))
    response = generate_password_extraction_response("SUNSHINE", conversation_id=conversation_id)
    (await sqlite_instance.add_message_to_memory_async(request=response))

    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    scorer = GandalfScorer(chat_target=chat_target, level=level)

    mocked_post.return_value = MagicMock(json=lambda: {"success": True, "message": "Message"})

    await scorer.score_async(scorable=MessageScorable.from_message(await store_message_async(response)))

    chat_target.set_system_prompt_async.assert_called_once()

    mocked_post.assert_called_once()


@patch("pyrit.score.true_false.gandalf_scorer.make_request_and_raise_if_error_async", new_callable=AsyncMock)
@pytest.mark.parametrize("level", [GandalfLevel.LEVEL_1, GandalfLevel.LEVEL_2, GandalfLevel.LEVEL_3])
async def test_gandalf_scorer_adds_to_memory(mocked_post, level: GandalfLevel, sqlite_instance: MemoryInterface):
    conversation_id = str(uuid.uuid4())
    generated_request = generate_request(conversation_id=conversation_id)
    (await sqlite_instance.add_message_to_memory_async(request=generated_request))
    response = generate_password_extraction_response("SUNSHINE", conversation_id=conversation_id)
    (await sqlite_instance.add_message_to_memory_async(request=response))

    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    mocked_post.return_value = MagicMock(json=lambda: {"success": True, "message": "Message"})

    patched_pieces = [generated_request.message_pieces[0], response.message_pieces[0]]
    with patch.object(sqlite_instance, "get_message_pieces_async", return_value=patched_pieces):
        scorer = GandalfScorer(level=level, chat_target=chat_target)

        await scorer.score_async(scorable=MessageScorable.from_message(await store_message_async(response)))


@pytest.mark.parametrize("level", [GandalfLevel.LEVEL_1, GandalfLevel.LEVEL_2, GandalfLevel.LEVEL_3])
async def test_gandalf_scorer_runtime_error_retries(level: GandalfLevel, sqlite_instance: MemoryInterface):
    conversation_id = str(uuid.uuid4())
    (await sqlite_instance.add_message_to_memory_async(request=generate_request(conversation_id=conversation_id)))
    response = generate_password_extraction_response("SUNSHINE", conversation_id=conversation_id)
    (await sqlite_instance.add_message_to_memory_async(request=response))

    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(side_effect=[RuntimeError("Error"), response])
    scorer = GandalfScorer(level=level, chat_target=chat_target)

    with pytest.raises(PyritException, match="Error in scorer GandalfScorer"):
        await scorer.score_async(scorable=MessageScorable.from_message(await store_message_async(response)))

    assert chat_target.send_prompt_async.call_count == 1


@patch("pyrit.score.true_false.gandalf_scorer.make_request_and_raise_if_error_async", new_callable=AsyncMock)
async def test_gandalf_scorer_wraps_httpx_error_as_pyrit_exception(mocked_post, sqlite_instance: MemoryInterface):
    """A failure from the Gandalf API call surfaces as PyritException (not a raw httpx error)."""
    import httpx

    conversation_id = str(uuid.uuid4())
    (await sqlite_instance.add_message_to_memory_async(request=generate_request(conversation_id=conversation_id)))
    response = generate_password_extraction_response("SUNSHINE", conversation_id=conversation_id)
    (await sqlite_instance.add_message_to_memory_async(request=response))

    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    mocked_post.side_effect = httpx.HTTPStatusError(
        "boom",
        request=httpx.Request("POST", "https://gandalf.example/score"),
        response=httpx.Response(500),
    )

    scorer = GandalfScorer(level=GandalfLevel.LEVEL_1, chat_target=chat_target)
    with pytest.raises(PyritException, match="Error in scorer GandalfScorer"):
        await scorer.score_async(scorable=MessageScorable.from_message(await store_message_async(response)))


@pytest.mark.parametrize("sentinel", ["NO", "no", "No", "NO.", "no.", " No ", "\nNO\n"])
@patch("pyrit.score.true_false.gandalf_scorer.make_request_and_raise_if_error_async", new_callable=AsyncMock)
async def test_gandalf_scorer_accepts_no_sentinel_case_and_punctuation_variants(
    mocked_post, sqlite_instance: MemoryInterface, sentinel: str
):
    """A case or punctuation variant of the scorer's own "NO" answer still means no password.

    The password-search utility is a chat target, so "no." and "No" are ordinary
    replies to "your response should be NO". Treating them as a password posts a
    guess to Gandalf and reports "Invalid password found in text."
    """
    conversation_id = str(uuid.uuid4())
    (await sqlite_instance.add_message_to_memory_async(request=generate_request(conversation_id=conversation_id)))
    response = generate_password_extraction_response(sentinel, conversation_id=conversation_id)
    (await sqlite_instance.add_message_to_memory_async(request=response))

    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    scorer = GandalfScorer(level=GandalfLevel.LEVEL_1, chat_target=chat_target)
    mocked_post.return_value = MagicMock(json=lambda: {"success": False, "message": "Message"})

    scores = await scorer.score_async(scorable=MessageScorable.from_message(await store_message_async(response)))

    mocked_post.assert_not_called()
    assert scores[0].get_value() is False
    assert scores[0].score_rationale == "No password found in text."


@pytest.mark.parametrize("negative_reply", ["NO, I cannot help", "No. No password was found.", "no\nI cannot help"])
@patch("pyrit.score.true_false.gandalf_scorer.make_request_and_raise_if_error_async", new_callable=AsyncMock)
async def test_gandalf_scorer_scores_negative_prose_as_false_async(
    mocked_post: AsyncMock, *, sqlite_instance: MemoryInterface, negative_reply: str
) -> None:
    """A negative reply returns False without a password guess."""
    conversation_id = str(uuid.uuid4())
    (await sqlite_instance.add_message_to_memory_async(request=generate_request(conversation_id=conversation_id)))
    response = generate_password_extraction_response(negative_reply, conversation_id=conversation_id)
    (await sqlite_instance.add_message_to_memory_async(request=response))

    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    scorer = GandalfScorer(level=GandalfLevel.LEVEL_1, chat_target=chat_target)
    mocked_post.return_value = MagicMock(json=lambda: {"success": False, "message": "Message"})

    scores = await scorer.score_async(scorable=MessageScorable.from_message(await store_message_async(response)))

    mocked_post.assert_not_called()
    assert scores[0].get_value() is False
    assert scores[0].score_rationale == "No password found in text."


@pytest.mark.parametrize("password", ["SUN SHINE", "sun-shine", "correct horse"])
@patch("pyrit.score.true_false.gandalf_scorer.make_request_and_raise_if_error_async", new_callable=AsyncMock)
async def test_gandalf_scorer_still_posts_multi_word_passwords(
    mocked_post, sqlite_instance: MemoryInterface, password: str
):
    """A real password may contain spaces or dashes; the sentinel check must not swallow it."""
    conversation_id = str(uuid.uuid4())
    (await sqlite_instance.add_message_to_memory_async(request=generate_request(conversation_id=conversation_id)))
    response = generate_password_extraction_response(password, conversation_id=conversation_id)
    (await sqlite_instance.add_message_to_memory_async(request=response))

    chat_target = MagicMock(spec=PromptTarget)
    chat_target.get_identifier.return_value = get_mock_target_identifier("MockChatTarget")
    chat_target.send_prompt_async = AsyncMock(return_value=[response])

    scorer = GandalfScorer(level=GandalfLevel.LEVEL_1, chat_target=chat_target)
    mocked_post.return_value = MagicMock(json=lambda: {"success": False, "message": "Message"})

    scores = await scorer.score_async(scorable=MessageScorable.from_message(await store_message_async(response)))

    mocked_post.assert_called_once()
    assert scores[0].score_rationale == f"Invalid password found in text. [value={password}]"
