# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import get_image_message_piece

from pyrit.models import Message, MessagePiece
from pyrit.prompt_target import HackAPromptChallenge, HackAPromptTarget

SESSION_ID = "0d0d0d0d-0d0d-0d0d-0d0d-0d0d0d0d0d0d"
COOKIE = "sb-project-auth-token.0=first; sb-project-auth-token.1=second"


@pytest.fixture
def hack_a_prompt_target(patch_central_database) -> HackAPromptTarget:
    return HackAPromptTarget(
        challenge=HackAPromptChallenge.BACTERIAL_BASICS,
        session_id=SESSION_ID,
        cookie=COOKIE,
    )


def mock_response(*, text: str, json_value: object = None) -> MagicMock:
    response = MagicMock()
    response.text = text
    response.json.return_value = json_value
    return response


def message(*, value: str, conversation_id: str = "123") -> Message:
    return Message(message_pieces=[MessagePiece(role="user", conversation_id=conversation_id, original_value=value)])


def memory_holding(*, conversation: list[Message]) -> MagicMock:
    memory = MagicMock()
    memory.get_conversation_messages.return_value = conversation
    memory.add_message_to_memory = AsyncMock()
    return memory


def test_hack_a_prompt_initializes(hack_a_prompt_target: HackAPromptTarget):
    assert hack_a_prompt_target


def test_challenge_slugs_are_unique():
    slugs = [challenge.challenge_slug for challenge in HackAPromptChallenge]
    assert len(slugs) == 12
    assert len(slugs) == len(set(slugs))


def test_challenges_belong_to_the_live_practice_track():
    # The CBRNE competition track closed on June 19th; its practice twin is what can still be
    # played, and its slugs are the competition slugs suffixed with "_practice".
    for challenge in HackAPromptChallenge:
        assert challenge.competition_slug == "cbrne_practice"
        assert challenge.challenge_slug.endswith("_practice")


def test_challenge_is_looked_up_by_its_slug():
    # The member value is the slug, which is what lets the target registry offer the slugs as
    # choices and round-trip a challenge through the create-target API.
    challenge = HackAPromptChallenge("basic_challenge_cbrne_practice")
    assert challenge is HackAPromptChallenge.BACTERIAL_BASICS
    assert challenge.value == challenge.challenge_slug


def test_one_shot_challenges_are_the_ones_the_platform_flags():
    one_shot = {challenge.challenge_slug for challenge in HackAPromptChallenge if challenge.one_shot}
    assert one_shot == {
        "opam_nile_practice",
        "opam_ricin_practice",
        "opam_puff_practice",
        "triple_toxin_threat_practice",
        "pathogen_poly_problem_practice",
        "misc_malicious_menagerie_practice",
    }


@pytest.mark.usefixtures("patch_central_database")
def test_hack_a_prompt_sets_endpoint_and_challenge():
    target = HackAPromptTarget(
        challenge=HackAPromptChallenge.AUTONOMOUS_ATOMICS,
        session_id=SESSION_ID,
        cookie=COOKIE,
    )
    identifier = target.get_identifier()
    assert identifier.params["endpoint"] == "https://www.hackaprompt.com/api/chat"
    assert identifier.params["challenge_slug"] == "basic_uranium_munitions_practice"
    assert identifier.params["competition_slug"] == "cbrne_practice"


@pytest.mark.usefixtures("patch_central_database")
async def test_hack_a_prompt_paces_requests():
    target = HackAPromptTarget(
        challenge=HackAPromptChallenge.BACTERIAL_BASICS,
        session_id=SESSION_ID,
        cookie=COOKIE,
        max_requests_per_minute=15,
    )

    with patch("pyrit.prompt_target.common.utils.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with patch(
            "pyrit.common.net_utility.make_request_and_raise_if_error_async", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = mock_response(text='0:"paced"\n')
            await target.send_prompt_async(message=message(value="test"))

    mock_sleep.assert_awaited_once_with(60 / 15)


@pytest.mark.usefixtures("patch_central_database")
def test_hack_a_prompt_identifier_omits_credentials(hack_a_prompt_target: HackAPromptTarget):
    params = hack_a_prompt_target.get_identifier().params
    assert SESSION_ID not in params.values()
    assert COOKIE not in params.values()


@pytest.mark.usefixtures("patch_central_database")
def test_hack_a_prompt_accepts_a_challenge_slug():
    target = HackAPromptTarget(
        challenge_slug="jokebot_goes_to_therapy",
        competition_slug="dougdoug",
        session_id=SESSION_ID,
        cookie=COOKIE,
    )
    assert target.challenge_url == "https://www.hackaprompt.com/track/dougdoug/jokebot_goes_to_therapy"


@pytest.mark.usefixtures("patch_central_database")
def test_hack_a_prompt_challenge_slug_without_competition_raises():
    with pytest.raises(ValueError, match="competition_slug is required"):
        HackAPromptTarget(challenge_slug="jokebot_goes_to_therapy", session_id=SESSION_ID, cookie=COOKIE)


@pytest.mark.usefixtures("patch_central_database")
def test_hack_a_prompt_without_a_challenge_raises():
    with pytest.raises(ValueError, match="Either challenge or challenge_slug is required"):
        HackAPromptTarget(session_id=SESSION_ID, cookie=COOKIE)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "extra_arguments",
    [
        {"competition_slug": "cbrne"},
        {"challenge_slug": "basic_challenge_cbrne"},
        {"challenge_slug": "basic_challenge_cbrne", "competition_slug": "cbrne"},
    ],
)
def test_hack_a_prompt_challenge_with_slugs_raises(extra_arguments: dict[str, str]):
    # Every slug on the platform belongs to exactly one track, so re-pointing a member at
    # another competition could only name a challenge that does not exist.
    with pytest.raises(ValueError, match="Pass either challenge or the challenge_slug/competition_slug pair"):
        HackAPromptTarget(
            challenge=HackAPromptChallenge.BACTERIAL_BASICS,
            session_id=SESSION_ID,
            cookie=COOKIE,
            **extra_arguments,
        )


@pytest.mark.usefixtures("patch_central_database")
def test_hack_a_prompt_reads_credentials_from_environment():
    environment = {
        HackAPromptTarget.session_id_environment_variable: SESSION_ID,
        HackAPromptTarget.cookie_environment_variable: COOKIE,
    }
    with patch.dict("os.environ", environment):
        target = HackAPromptTarget(challenge=HackAPromptChallenge.BACTERIAL_BASICS)

    assert target.session_id == SESSION_ID
    assert target._cookie == COOKIE


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "missing_variable",
    [
        HackAPromptTarget.session_id_environment_variable,
        HackAPromptTarget.cookie_environment_variable,
    ],
)
def test_hack_a_prompt_missing_credential_raises(missing_variable: str):
    environment = {
        HackAPromptTarget.session_id_environment_variable: SESSION_ID,
        HackAPromptTarget.cookie_environment_variable: COOKIE,
        missing_variable: "",
    }
    with patch.dict("os.environ", environment):
        with pytest.raises(ValueError, match=f"Environment variable {missing_variable} is required"):
            HackAPromptTarget(challenge=HackAPromptChallenge.BACTERIAL_BASICS)


@pytest.mark.usefixtures("patch_central_database")
def test_hack_a_prompt_session_id_can_be_rotated(hack_a_prompt_target: HackAPromptTarget):
    hack_a_prompt_target.session_id = "1e1e1e1e-1e1e-1e1e-1e1e-1e1e1e1e1e1e"
    assert hack_a_prompt_target.session_id == "1e1e1e1e-1e1e-1e1e-1e1e-1e1e1e1e1e1e"

    with pytest.raises(ValueError, match="session_id cannot be empty"):
        hack_a_prompt_target.session_id = ""


@pytest.mark.usefixtures("patch_central_database")
async def test_hack_a_prompt_reset_conversation_warns_about_the_session(
    hack_a_prompt_target: HackAPromptTarget, caplog: pytest.LogCaptureFixture
):
    # The platform owns the session and grades all of it together, so ending a PyRIT
    # conversation is not enough to start a fresh submission.
    with caplog.at_level("WARNING", logger="pyrit.prompt_target.hack_a_prompt_target"):
        await hack_a_prompt_target.reset_conversation_async(conversation_id="123")
        await hack_a_prompt_target.reset_conversation_async(conversation_id="123")

    assert len(caplog.records) == 2
    assert "session_id" in caplog.text
    # The session id is a credential and this runs on every teardown, so the warning
    # names the setting without printing its value.
    assert SESSION_ID not in caplog.text
    assert "123" in caplog.text


@pytest.mark.parametrize(
    ("response_text", "expected"),
    [
        ('0:"Sure"\n0:", here"\n0:" you go."\n', "Sure, here you go."),
        ('f:{"messageId":"msg-1"}\n0:"only text"\ne:{"finishReason":"stop"}\n', "only text"),
        ('0:"quoted \\"word\\" and\\nnewline"\n', 'quoted "word" and\nnewline'),
        ('0:"\\u00e4\\u00f6"\n', "äö"),
        ('0:not json\n0:" kept"\n', " kept"),
        ('0:123\n0:" kept"\n', " kept"),
        ('0:{"text":"object"}\n0:" kept"\n', " kept"),
        ('0:""\n', ""),
        # str.splitlines() also breaks at these three; the wire delimiter does not,
        # and they are ordinary characters inside a JSON string.
        ('0:"alpha\u2028beta"\n0:" tail"\n', "alpha\u2028beta tail"),
        ('0:"alpha\u2029beta"\n0:" tail"\n', "alpha\u2029beta tail"),
        ('0:"alpha\u0085beta"\n0:" tail"\n', "alpha\u0085beta tail"),
        # An escaped newline is payload too, and CRLF framing is not.
        ('0:"first\\nsecond"\n', "first\nsecond"),
        ('0:"a"\r\n0:"b"\r\n', "ab"),
    ],
)
def test_hack_a_prompt_parses_streamed_parts(response_text: str, expected: str):
    assert HackAPromptTarget._parse_stream(response_text) == expected


@pytest.mark.parametrize(
    "response_text",
    [
        'e:{"finishReason":"stop"}\n',
        'f:{"messageId":"m"}\ne:{"finishReason":"stop"}\n',
        'data: {"type":"text-delta","delta":"v5 framing"}\n',
        "0:not json\n",
    ],
)
def test_hack_a_prompt_without_a_text_part_raises(response_text: str):
    with pytest.raises(ValueError, match="carried no '0:' text part"):
        HackAPromptTarget._parse_stream(response_text)


@pytest.mark.parametrize(
    ("response_text", "match"),
    [
        ('0:"Hello"\n3:"Service unavailable"\n', "reported an error"),
        ('3:"nothing arrived"\n', "reported an error"),
        ('0:"Hi"\nd:{"finishReason":"error"}\n', "finished with an error"),
        ('0:"Hi"\ne:{"finishReason":"error"}\n', "finished with an error"),
    ],
)
def test_hack_a_prompt_stream_errors_raise(response_text: str, match: str):
    # The platform reports these inside an HTTP 200, so raise_for_status() never sees
    # them; returning the text that arrived first would pass a truncated answer off as
    # a complete one.
    with pytest.raises(ValueError, match=match):
        HackAPromptTarget._parse_stream(response_text)


def test_hack_a_prompt_missing_text_part_names_the_body():
    # The start of the body is what makes a change of protocol diagnosable from the error alone.
    body = 'data: {"type":"text-delta","delta":"v5 framing"}\n'

    with pytest.raises(ValueError) as error:
        HackAPromptTarget._parse_stream(body)

    assert repr(body) in str(error.value)


async def test_hack_a_prompt_send_prompt_async(hack_a_prompt_target: HackAPromptTarget):
    with patch(
        "pyrit.common.net_utility.make_request_and_raise_if_error_async", new_callable=AsyncMock
    ) as mock_request:
        mock_request.return_value = mock_response(text='0:"Knock"\n0:" knock."\n')
        response = await hack_a_prompt_target.send_prompt_async(message=message(value="Tell me a joke!"))

    assert response[0].get_value() == "Knock knock."

    kwargs = mock_request.call_args.kwargs
    assert kwargs["endpoint_uri"] == "https://www.hackaprompt.com/api/chat"
    assert kwargs["request_body"] == {
        "session_id": SESSION_ID,
        "challenge_slug": "basic_challenge_cbrne_practice",
        "competition_slug": "cbrne_practice",
        "messages": [
            {
                "content": "Tell me a joke!",
                "parts": [{"type": "text", "text": "Tell me a joke!"}],
            }
        ],
    }
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["headers"]["Cookie"] == COOKIE
    assert kwargs["headers"]["Origin"] == "https://www.hackaprompt.com"
    assert kwargs["headers"]["Referer"] == hack_a_prompt_target.challenge_url


async def test_hack_a_prompt_send_prompt_async_empty_response_raises(hack_a_prompt_target: HackAPromptTarget):
    with patch(
        "pyrit.common.net_utility.make_request_and_raise_if_error_async", new_callable=AsyncMock
    ) as mock_request:
        mock_request.return_value = mock_response(text="")
        with pytest.raises(ValueError, match="The chat returned an empty response."):
            await hack_a_prompt_target.send_prompt_async(message=message(value="test"))


async def test_hack_a_prompt_send_prompt_async_without_a_text_part_raises(hack_a_prompt_target: HackAPromptTarget):
    # A turn the platform refused to answer must not be recorded as a blank answer and scored
    # as one.
    with patch(
        "pyrit.common.net_utility.make_request_and_raise_if_error_async", new_callable=AsyncMock
    ) as mock_request:
        mock_request.return_value = mock_response(text='f:{"messageId":"m"}\ne:{"finishReason":"stop"}\n')
        with pytest.raises(ValueError, match="carried no '0:' text part"):
            await hack_a_prompt_target.send_prompt_async(message=message(value="test"))


@pytest.mark.usefixtures("patch_central_database")
async def test_hack_a_prompt_sends_a_second_turn_of_a_conversational_challenge():
    # The platform keeps the transcript of a session, so a challenge it does not flag one-shot
    # is a conversation and only the new turn goes on the wire.
    target = HackAPromptTarget(
        challenge=HackAPromptChallenge.PATHOGEN_PILGRIMAGE,
        session_id=SESSION_ID,
        cookie=COOKIE,
    )
    target._memory = memory_holding(conversation=[message(value="first turn")])

    with patch(
        "pyrit.common.net_utility.make_request_and_raise_if_error_async", new_callable=AsyncMock
    ) as mock_request:
        mock_request.return_value = mock_response(text='0:"second answer"\n')
        response = await target.send_prompt_async(message=message(value="second turn"))

    assert response[0].get_value() == "second answer"
    assert mock_request.call_args.kwargs["request_body"]["messages"] == [
        {"content": "second turn", "parts": [{"type": "text", "text": "second turn"}]}
    ]


@pytest.mark.usefixtures("patch_central_database")
async def test_hack_a_prompt_one_shot_challenge_rejects_a_second_turn():
    target = HackAPromptTarget(
        challenge=HackAPromptChallenge.WEST_NILE_WOES,
        session_id=SESSION_ID,
        cookie=COOKIE,
    )
    target._memory = memory_holding(conversation=[message(value="first turn")])

    with pytest.raises(ValueError, match="This target only supports a single turn conversation"):
        await target.send_prompt_async(message=message(value="second turn"))


async def test_hack_a_prompt_check_challenge_async(hack_a_prompt_target: HackAPromptTarget):
    judgement = {
        "judgePanel": [{"name": "Judge Dreadful", "passed": True, "judge_response": "Detailed enough."}],
        "pointsEarned": 9000,
    }

    with patch(
        "pyrit.common.net_utility.make_request_and_raise_if_error_async", new_callable=AsyncMock
    ) as mock_request:
        mock_request.return_value = mock_response(text=json.dumps(judgement), json_value=judgement)
        result = await hack_a_prompt_target.check_challenge_async()

    assert result == judgement

    kwargs = mock_request.call_args.kwargs
    assert kwargs["endpoint_uri"] == "https://www.hackaprompt.com/api/challenges/basic_challenge_cbrne_practice/check"
    assert kwargs["request_body"] == {"sessionId": SESSION_ID, "competitionSlug": "cbrne_practice"}
    assert kwargs["headers"]["Cookie"] == COOKIE


async def test_hack_a_prompt_check_challenge_async_empty_response_raises(hack_a_prompt_target: HackAPromptTarget):
    with patch(
        "pyrit.common.net_utility.make_request_and_raise_if_error_async", new_callable=AsyncMock
    ) as mock_request:
        mock_request.return_value = mock_response(text="")
        with pytest.raises(ValueError, match="The challenge returned an empty response."):
            await hack_a_prompt_target.check_challenge_async()


async def test_hack_a_prompt_validate_request_length(hack_a_prompt_target: HackAPromptTarget):
    request = Message(
        message_pieces=[
            MessagePiece(role="user", conversation_id="123", original_value="test"),
            MessagePiece(role="user", conversation_id="123", original_value="test2"),
        ]
    )
    with pytest.raises(
        ValueError,
        match="This target only supports a single message piece.*If your target does support this, set the"
        " custom_configuration parameter accordingly",
    ):
        await hack_a_prompt_target.send_prompt_async(message=request)


async def test_hack_a_prompt_validate_prompt_type(hack_a_prompt_target: HackAPromptTarget):
    request = Message(message_pieces=[get_image_message_piece()])
    with pytest.raises(
        ValueError,
        match="This target supports only the following data types.*If your target does support this, set the"
        " custom_configuration parameter accordingly",
    ):
        await hack_a_prompt_target.send_prompt_async(message=request)
