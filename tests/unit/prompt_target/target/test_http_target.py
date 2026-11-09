# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from pyrit.analytics import get_cached_results_for_technique_async
from pyrit.memory import SQLiteMemory
from pyrit.models import (
    AtomicAttackEvaluationIdentifier,
    AtomicAttackIdentifier,
    AttackOutcome,
    AttackResult,
    ComponentIdentifier,
    ContentScorable,
    Message,
    MessagePiece,
    ObjectiveTargetEvaluationIdentifier,
    ScorerEvaluationIdentifier,
    ScoringExpectation,
    TargetIdentifier,
)
from pyrit.prompt_target import (
    HTTPTarget,
    TargetCapabilities,
    TargetConfiguration,
    get_http_target_json_response_callback_function,
    get_http_target_regex_matching_callback_function,
)
from pyrit.score import NonReplayableObservationError, SelfAskTrueFalseScorer


@pytest.fixture
def mock_callback_function() -> Callable:
    return get_http_target_json_response_callback_function(key="mock_key")


@pytest.fixture
def mock_http_target(mock_callback_function, sqlite_instance) -> HTTPTarget:
    sample_request = (
        'POST / HTTP/1.1\nHost: example.com\nContent-Type: application/json\n\n{"prompt": "{PLACEHOLDER_PROMPT}"}'
    )
    return HTTPTarget(
        http_request=sample_request,
        prompt_regex_string="{PLACEHOLDER_PROMPT}",
        callback_function=mock_callback_function,
    )


@pytest.fixture
def mock_http_response() -> MagicMock:
    mock_response = MagicMock()
    mock_response.content = b'{"mock_key": "value1"}'
    return mock_response


def test_initilization_with_parameters(mock_http_target, mock_callback_function):
    assert (
        mock_http_target.http_request
        == 'POST / HTTP/1.1\nHost: example.com\nContent-Type: application/json\n\n{"prompt": "{PLACEHOLDER_PROMPT}"}'
    )
    assert mock_http_target.prompt_regex_string == "{PLACEHOLDER_PROMPT}"
    assert mock_http_target.callback_function == mock_callback_function


def test_http_target_sets_endpoint_and_rate_limit(mock_callback_function, sqlite_instance):
    sample_request = (
        'POST / HTTP/1.1\nHost: example.com\nContent-Type: application/json\n\n{"prompt": "{PLACEHOLDER_PROMPT}"}'
    )
    target = HTTPTarget(
        http_request=sample_request,
        prompt_regex_string="{PLACEHOLDER_PROMPT}",
        callback_function=mock_callback_function,
        max_requests_per_minute=25,
    )
    identifier = target.get_identifier()
    assert identifier.params["endpoint"] == "https://example.com/"
    assert target._max_requests_per_minute == 25


def _judge_target(*, auth: str = "Authorization: Bearer secret-1") -> HTTPTarget:
    return HTTPTarget(
        http_request=f"POST /judge HTTP/1.1\nHost: example.com\nContent-Type: text/plain\n{auth}\n\n"
        "policy=A\n{PROMPT}"
    )


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "other_request",
    [
        "POST /judge HTTP/1.1\nHost: example.com\nContent-Type: text/plain\n\npolicy=B\n{PROMPT}",
        "PUT /judge HTTP/1.1\nHost: example.com\nContent-Type: text/plain\n\npolicy=A\n{PROMPT}",
        "POST /judge HTTP/1.1\nHost: example.com\nContent-Type: application/json\n\npolicy=A\n{PROMPT}",
        "POST /judge HTTP/1.1\r\nHost: example.com\r\nContent-Type: text/plain\r\n\r\npolicy=A\r\n{PROMPT}",
    ],
    ids=["body", "method", "header", "body-line-endings"],
)
def test_identifier_changes_with_the_request_template(other_request: str):
    base = HTTPTarget(
        http_request="POST /judge HTTP/1.1\nHost: example.com\nContent-Type: text/plain\n\npolicy=A\n{PROMPT}"
    )
    other = HTTPTarget(http_request=other_request)

    assert base.get_identifier().params["endpoint"] == other.get_identifier().params["endpoint"]
    assert base.get_identifier().hash != other.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
def test_identifier_matches_for_identical_templates():
    assert _judge_target().get_identifier().hash == _judge_target().get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "auth",
    ["Authorization: Bearer secret-2", "X-API-Key: secret-2", "Cookie: session=secret-2", "api-key: secret-2"],
)
def test_identifier_ignores_credential_headers(auth: str):
    with_secret = _judge_target(auth=auth)

    assert (
        with_secret.get_identifier().hash == _judge_target(auth="Authorization: Bearer secret-1").get_identifier().hash
    )
    assert "secret" not in str(with_secret.get_identifier().params)


_POLICY_REQUEST = "POST /judge HTTP/1.1\nHost: {host}\nContent-Type: text/plain\n\npolicy={policy}\n{{PROMPT}}"
_JUDGMENT = '{"score_value": "true", "description": "matched", "rationale": "reason"}'
# An HTTP judge endpoint that accepts a system prompt and conversation history, as a scorer requires.
_JUDGE_CONFIGURATION = TargetConfiguration(
    capabilities=TargetCapabilities(supports_multi_turn=True, supports_system_prompt=True)
)


def _target_eval_hash(target: HTTPTarget) -> str:
    return ObjectiveTargetEvaluationIdentifier(target.get_identifier()).eval_hash


def _judge(*, host: str = "example.com", policy: str = "A", **options: Any) -> HTTPTarget:
    return HTTPTarget(
        http_request=_POLICY_REQUEST.format(host=host, policy=policy),
        custom_configuration=_JUDGE_CONFIGURATION,
        **options,
    )


def _judge_with_request(*, request: str, **options: Any) -> HTTPTarget:
    return HTTPTarget(http_request=request, custom_configuration=_JUDGE_CONFIGURATION, **options)


def _scorer_eval_hash(target: HTTPTarget) -> str:
    return ScorerEvaluationIdentifier(SelfAskTrueFalseScorer(chat_target=target).get_identifier()).eval_hash


@pytest.mark.usefixtures("patch_central_database")
def test_eval_hashes_change_with_the_request_policy():
    policy_a = _judge(policy="A")
    policy_b = _judge(policy="B")

    assert _target_eval_hash(policy_a) != _target_eval_hash(policy_b)
    assert _scorer_eval_hash(policy_a) != _scorer_eval_hash(policy_b)


@pytest.mark.usefixtures("patch_central_database")
def test_eval_hashes_match_across_deployments_of_the_same_request():
    east = _judge(host="east.example.com")
    west = _judge(host="west.example.com")

    assert east.get_identifier().hash != west.get_identifier().hash
    assert _target_eval_hash(east) == _target_eval_hash(west)
    assert _scorer_eval_hash(east) == _scorer_eval_hash(west)


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize("client_option", ["client", "headers"])
def test_identifier_includes_client_headers(client_option: str):
    def build(policy: str) -> HTTPTarget:
        headers = {"X-Policy": policy}
        options = {"client": httpx.AsyncClient(headers=headers)} if client_option == "client" else {"headers": headers}
        return HTTPTarget(http_request=_POLICY_REQUEST.format(host="example.com", policy="A"), **options)

    policy_a, policy_b = build("A"), build("B")

    assert policy_a.get_identifier().hash != policy_b.get_identifier().hash
    assert _target_eval_hash(policy_a) != _target_eval_hash(policy_b)
    assert policy_a.get_identifier().hash == build("A").get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
def test_identifier_ignores_client_defaults_and_credentials():
    request = _POLICY_REQUEST.format(host="example.com", policy="A")
    plain = HTTPTarget(http_request=request)
    with_client = HTTPTarget(http_request=request, client=httpx.AsyncClient(headers={"X-API-Key": "secret-1"}))
    with_kwargs = HTTPTarget(http_request=request, headers={"Authorization": "Bearer secret-2"})

    assert with_client.get_identifier().hash == plain.get_identifier().hash
    assert with_kwargs.get_identifier().hash == plain.get_identifier().hash


def _sent_policy(request: httpx.Request) -> str:
    return str(request.headers.get("x-policy") or request.url.params.get("policy"))


def _policy_judge(*, policy: str, policy_option: str, requests: list[httpx.Request]) -> SelfAskTrueFalseScorer:
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=_JUDGMENT)

    policy_setting = {"headers": {"X-Policy": policy}} if policy_option == "header" else {"params": {"policy": policy}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(respond), **policy_setting)
    return SelfAskTrueFalseScorer(chat_target=_judge(client=client))


@pytest.mark.parametrize("policy_option", ["header", "query"])
async def test_judgment_from_one_client_policy_does_not_replay_for_another_async(sqlite_instance, policy_option: str):
    policy_a_requests: list[httpx.Request] = []
    policy_b_requests: list[httpx.Request] = []
    expectation = ScoringExpectation(objective="Judge this response")
    score = (
        await _policy_judge(policy="A", policy_option=policy_option, requests=policy_a_requests).score_async(
            scorable=ContentScorable(value="candidate response"),
            expectation=expectation,
        )
    )[0]
    observation = (await sqlite_instance.get_observations_async(observation_ids=score.observation_ids))[0]

    with pytest.raises(NonReplayableObservationError, match="scorer configuration"):
        await _policy_judge(
            policy="B", policy_option=policy_option, requests=policy_b_requests
        ).score_observation_async(
            observation=observation,
            expectation=expectation,
        )

    assert [_sent_policy(request) for request in policy_a_requests] == ["A"]
    assert policy_b_requests == []


_QUERY_REQUEST = "POST {path} HTTP/1.1\nHost: example.com\nContent-Type: text/plain\n\n{{PROMPT}}"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    "first, second",
    [
        ({"path": "/judge?policy=A"}, {"path": "/judge?policy=B"}),
        ({"path": "/judge/strict"}, {"path": "/judge/lenient"}),
        ({"path": "/judge/strict%2Flenient"}, {"path": "/judge/strict/lenient"}),
        ({"path": "/judge/strict%2flenient"}, {"path": "/judge/strict/lenient"}),
        ({"path": "/judge", "params": {"policy": "A"}}, {"path": "/judge", "params": {"policy": "B"}}),
        (
            {"path": "/judge", "client": httpx.AsyncClient(params={"policy": "A"})},
            {"path": "/judge", "client": httpx.AsyncClient(params={"policy": "B"})},
        ),
    ],
    ids=["template-query", "path", "encoded-slash", "lowercase-encoded-slash", "params-kwarg", "client-params"],
)
def test_identity_changes_with_the_request_path_and_query(first: dict[str, Any], second: dict[str, Any]):
    def build(options: dict[str, Any]) -> HTTPTarget:
        options = dict(options)
        return _judge_with_request(request=_QUERY_REQUEST.format(path=options.pop("path")), **options)

    policy_a, policy_b = build(first), build(second)

    assert policy_a.get_identifier().hash != policy_b.get_identifier().hash
    assert _target_eval_hash(policy_a) != _target_eval_hash(policy_b)
    assert _scorer_eval_hash(policy_a) != _scorer_eval_hash(policy_b)


@pytest.mark.usefixtures("patch_central_database")
def test_identity_ignores_credential_query_parameters():
    def build(**options: Any) -> HTTPTarget:
        return _judge_with_request(request=_QUERY_REQUEST.format(path="/judge?policy=A"), **options)

    template_key_1 = _judge_with_request(request=_QUERY_REQUEST.format(path="/judge?policy=A&api_key=secret-1"))
    template_key_2 = _judge_with_request(request=_QUERY_REQUEST.format(path="/judge?policy=A&api_key=secret-2"))
    client_key_1 = build(params={"policy": "A", "access_token": "secret-1"})
    client_key_2 = build(params={"policy": "A", "access_token": "secret-2"})

    assert _target_eval_hash(template_key_1) == _target_eval_hash(template_key_2)
    assert client_key_1.get_identifier().hash == client_key_2.get_identifier().hash
    assert "secret" not in str(client_key_1.get_identifier().params)


@pytest.mark.parametrize(
    "path_a, path_b",
    [
        ("/judge?policy=A", "/judge?policy=B"),
        ("/judge/strict%2Flenient", "/judge/strict/lenient"),
    ],
    ids=["query-policy", "encoded-slash"],
)
async def test_cached_attack_results_are_not_shared_across_request_routes_async(
    sqlite_instance: SQLiteMemory, path_a: str, path_b: str
) -> None:
    def attack_result_for(target: HTTPTarget) -> AttackResult:
        attack = ComponentIdentifier(
            class_name="PromptSendingAttack",
            class_module="pyrit.executor.attack.single_turn.prompt_sending",
            children={"objective_target": target.get_identifier()},
        )
        return AttackResult(
            conversation_id=str(uuid.uuid4()),
            objective="test objective",
            atomic_attack_identifier=AtomicAttackIdentifier.build(attack_identifier=attack),
            outcome=AttackOutcome.SUCCESS,
        )

    policy_a = _judge_with_request(request=_QUERY_REQUEST.format(path=path_a))
    policy_b = _judge_with_request(request=_QUERY_REQUEST.format(path=path_b))
    saved = attack_result_for(policy_a)
    await sqlite_instance.add_attack_results_to_memory_async(attack_results=[saved])
    technique_eval_hash = AtomicAttackEvaluationIdentifier(saved.atomic_attack_identifier).eval_hash

    async def cached_for_async(target: HTTPTarget) -> list[AttackResult]:
        return await get_cached_results_for_technique_async(
            sqlite_instance,
            technique_eval_hash=technique_eval_hash,
            objective_target_eval_hash=_target_eval_hash(target),
        )

    assert [result.conversation_id for result in await cached_for_async(policy_a)] == [saved.conversation_id]
    assert await cached_for_async(policy_b) == []


async def test_encoded_path_identity_matches_distinct_sent_routes_async(sqlite_instance: SQLiteMemory) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=_JUDGMENT)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False) as client:
        encoded = _judge_with_request(request=_QUERY_REQUEST.format(path="/judge/strict%2Flenient"), client=client)
        literal = _judge_with_request(request=_QUERY_REQUEST.format(path="/judge/strict/lenient"), client=client)
        encoded_judge = SelfAskTrueFalseScorer(chat_target=encoded)
        literal_judge = SelfAskTrueFalseScorer(chat_target=literal)
        [score] = await encoded_judge.score_text_async("candidate response")
        [observation] = await sqlite_instance.get_observations_async(observation_ids=score.observation_ids)

        with pytest.raises(NonReplayableObservationError, match="scorer configuration"):
            await literal_judge.score_observation_async(observation=observation)
        assert len(requests) == 1
        await literal_judge.score_text_async("candidate response")

    assert [request.url.raw_path for request in requests] == [
        b"/judge/strict%2Flenient",
        b"/judge/strict/lenient",
    ]
    assert requests[0].url.path == requests[1].url.path
    encoded_id = encoded.get_identifier()
    restored = TargetIdentifier.model_validate_json(encoded_id.model_dump_json())
    assert restored.hash == encoded_id.hash
    assert ObjectiveTargetEvaluationIdentifier(restored).eval_hash == _target_eval_hash(encoded)
    assert _target_eval_hash(encoded) != _target_eval_hash(literal)
    assert _scorer_eval_hash(encoded) != _scorer_eval_hash(literal)


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async(mock_request, mock_http_target, mock_http_response):
    message = MagicMock()
    message.message_pieces = [
        MagicMock(
            converted_value="test_prompt",
            converted_value_data_type="text",
            attack_identifier=None,
            conversation_id="",
            labels={},
            prompt_metadata={},
        )
    ]
    mock_request.return_value = mock_http_response
    response = await mock_http_target.send_prompt_async(message=message)
    assert len(response) == 1
    assert response[0].get_value() == "value1"
    assert mock_request.call_count == 1
    mock_request.assert_called_with(
        method="POST",
        url="https://example.com/",
        headers={"host": "example.com", "content-type": "application/json"},
        content='{"prompt": "test_prompt"}',
        follow_redirects=True,
    )


@patch("httpx.AsyncClient.request", new_callable=AsyncMock)
async def test_send_prompt_async_uses_data_for_dict_body(mock_request, mock_http_response, patch_central_database):
    target = HTTPTarget(http_request="POST / HTTP/1.1\nHost: example.com\n\n")
    message = MagicMock()
    message.message_pieces = [
        MagicMock(
            converted_value="test_prompt",
            converted_value_data_type="text",
            attack_identifier=None,
            conversation_id="",
            labels={},
            prompt_metadata={},
        )
    ]
    mock_request.return_value = mock_http_response

    with patch.object(
        target,
        "parse_raw_http_request",
        return_value=(
            {"host": "example.com"},
            {"prompt": "test_prompt"},
            "https://example.com/",
            "POST",
            "HTTP/1.1",
        ),
    ):
        response = await target.send_prompt_async(message=message)

    assert len(response) == 1
    mock_request.assert_awaited_once_with(
        method="POST",
        url="https://example.com/",
        headers={"host": "example.com"},
        data={"prompt": "test_prompt"},
        follow_redirects=True,
    )


def test_parse_raw_http_request_ignores_content_length(patch_central_database):
    request = "POST / HTTP/1.1\nHost: example.com\nContent-Type: application/json\nContent-Length: 100\n\n"
    target = HTTPTarget(http_request=request)

    headers, _, _, _, _ = target.parse_raw_http_request(request)
    assert headers == {"host": "example.com", "content-type": "application/json"}


def test_parse_raw_http_respects_url_path(patch_central_database):
    request1 = (
        "POST https://diffsite.com/Test/Path?Token=AbC123 HTTP/1.1\nHost: example.com\nContent-Type: "
        "application/json\nContent-Length: 100\n\n"
    )
    target = HTTPTarget(http_request=request1)
    headers, _, url, _, _ = target.parse_raw_http_request(request1)
    assert url == "https://diffsite.com/Test/Path?Token=AbC123"

    # The host header should still be example.com
    assert headers == {"host": "example.com", "content-type": "application/json"}


async def test_send_prompt_async_client_kwargs(patch_central_database):
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
        # Create httpx_client_kwargs to test
        httpx_client_kwargs = {"timeout": 10, "verify": False}
        sample_request = "GET /test HTTP/1.1\nHost: example.com\n\n"
        # Create instance of HTTPTarget with httpx_client_kwargs
        # Use **httpx_client_kwargs to pass them as keyword arguments
        http_target = HTTPTarget(http_request=sample_request, **httpx_client_kwargs)
        message = MagicMock()
        message.message_pieces = [
            MagicMock(
                converted_value="",
                converted_value_data_type="text",
                attack_identifier=None,
                conversation_id="",
                labels={},
                prompt_metadata={},
            )
        ]
        mock_response = MagicMock()
        mock_response.content = b"Response content"
        mock_request.return_value = mock_response

        await http_target.send_prompt_async(message=message)

        mock_request.assert_called_with(
            method="GET",
            url="https://example.com/test",
            headers={"host": "example.com"},
            follow_redirects=True,
            content="",
        )
        assert http_target._client is None


@patch("httpx.AsyncClient.request", new_callable=AsyncMock)
async def test_send_prompt_async_rejects_prompt_destination_change(mock_request, patch_central_database):
    target = HTTPTarget(http_request="GET {PROMPT} HTTP/1.1\nHost: example.com\n\n")
    message = Message(
        message_pieces=[
            MessagePiece(
                role="user",
                original_value="https://attacker.example/path",
                converted_value="https://attacker.example/path",
                converted_value_data_type="text",
            )
        ]
    )

    with pytest.raises(ValueError, match="cannot change the configured HTTP destination"):
        await target.send_prompt_async(message=message)

    mock_request.assert_not_awaited()


@patch("httpx.AsyncClient.request", new_callable=AsyncMock)
async def test_send_prompt_async_allows_configured_internal_destination(mock_request, patch_central_database):
    target = HTTPTarget(http_request="POST /api/{PROMPT} HTTP/1.1\nHost: 10.0.0.8:8080\n\n")
    message = Message(message_pieces=[MessagePiece(role="user", original_value="jobs", converted_value="jobs")])
    mock_response = MagicMock()
    mock_response.content = b"ok"
    mock_request.return_value = mock_response

    await target.send_prompt_async(message=message)

    assert mock_request.call_args.kwargs["url"] == "https://10.0.0.8:8080/api/jobs"


@patch("httpx.AsyncClient.request", new_callable=AsyncMock)
async def test_send_prompt_async_without_callback_stores_decoded_text(mock_request, patch_central_database):
    target = HTTPTarget(http_request="POST /api HTTP/1.1\nHost: example.com\n\n{PROMPT}")
    message = Message(message_pieces=[MessagePiece(role="user", original_value="prompt")])
    body = "Sure \u2014 here\u2019s the answer:\nStep 1: caf\u00e9"
    mock_request.return_value = httpx.Response(200, content=body.encode("utf-8"))

    response = await target.send_prompt_async(message=message)

    assert response[0].get_value() == body


@patch("httpx.AsyncClient.request", new_callable=AsyncMock)
async def test_send_prompt_async_follows_redirects_when_enabled(mock_request, patch_central_database):
    target = HTTPTarget(
        http_request="POST /api HTTP/1.1\nHost: example.com\n\n",
        follow_redirects=True,
    )
    message = Message(message_pieces=[MessagePiece(role="user", original_value="prompt")])
    mock_response = MagicMock()
    mock_response.content = b"ok"
    mock_request.return_value = mock_response

    await target.send_prompt_async(message=message)

    assert mock_request.call_args.kwargs["follow_redirects"] is True


@patch("httpx.AsyncClient.request", new_callable=AsyncMock)
async def test_send_prompt_async_disables_redirects_when_requested(mock_request, patch_central_database):
    target = HTTPTarget(
        http_request="POST /api HTTP/1.1\nHost: example.com\n\n",
        follow_redirects=False,
    )
    message = Message(message_pieces=[MessagePiece(role="user", original_value="prompt")])
    mock_response = MagicMock()
    mock_response.content = b"ok"
    mock_request.return_value = mock_response

    await target.send_prompt_async(message=message)

    assert mock_request.call_args.kwargs["follow_redirects"] is False


def test_http_target_omitted_redirect_setting_preserves_behavior(patch_central_database):
    target = HTTPTarget(http_request="GET / HTTP/1.1\nHost: example.com\n\n")
    assert target.follow_redirects is True


@pytest.mark.parametrize(
    ("http_request", "prompt"),
    [
        ("GET /search?q={PROMPT} HTTP/1.1\nHost: example.com\n\n", "first\nsecond"),
        ("GET / HTTP/1.1\nHost: example.com\nX-Prompt: {PROMPT}\n\n", "first\nsecond"),
        ("GET /search?q={PROMPT} HTTP/1.1\nHost: example.com\n\n", "first\rsecond"),
        ("GET / HTTP/1.1\nHost: example.com\nX-Prompt: {PROMPT}\n\n", "first\rsecond"),
    ],
)
@patch("httpx.AsyncClient.request", new_callable=AsyncMock)
async def test_send_prompt_async_rejects_newlines_outside_body(
    mock_request,
    patch_central_database,
    http_request,
    prompt,
):
    target = HTTPTarget(http_request=http_request)
    message = Message(message_pieces=[MessagePiece(role="user", original_value=prompt)])

    with pytest.raises(ValueError, match="cannot contain CR or LF"):
        await target.send_prompt_async(message=message)

    mock_request.assert_not_awaited()


@patch("httpx.AsyncClient.request", new_callable=AsyncMock)
async def test_send_prompt_async_rejects_newline_when_placeholder_spans_header_and_body(
    mock_request, patch_central_database
):
    target = HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: example.com\nX-Prompt: {PROMPT_HEADER}\n\n{PROMPT_BODY}",
        prompt_regex_string=r"\{PROMPT_HEADER\}\n\n\{PROMPT_BODY\}",
    )
    message = Message(message_pieces=[MessagePiece(role="user", original_value="first\nsecond")])

    with pytest.raises(ValueError, match="cannot contain CR or LF"):
        await target.send_prompt_async(message=message)

    mock_request.assert_not_awaited()


@patch("httpx.AsyncClient.request", new_callable=AsyncMock)
async def test_send_prompt_async_allows_multiline_body_prompt(mock_request, patch_central_database):
    target = HTTPTarget(
        http_request="POST / HTTP/1.1\nHost: example.com\nContent-Type: text/plain\n\nbefore:{PROMPT}:after"
    )
    message = Message(message_pieces=[MessagePiece(role="user", original_value="first\nsecond")])
    mock_response = MagicMock()
    mock_response.content = b"ok"
    mock_request.return_value = mock_response

    await target.send_prompt_async(message=message)

    assert mock_request.call_args.kwargs["content"] == "before:first\nsecond:after"


async def test_send_prompt_async_validation(mock_http_target):
    # Creating a Message with no pieces raises immediately
    with pytest.raises(ValueError, match="must have at least one message piece"):
        Message(message_pieces=[])


@patch("httpx.AsyncClient.request")
async def test_send_prompt_regex_parse_async(mock_request, mock_http_target):
    callback_function = get_http_target_regex_matching_callback_function(key=r"Match: (\d+)")
    mock_http_target.callback_function = callback_function

    message = MagicMock()
    message.message_pieces = [
        MagicMock(
            converted_value="test_prompt",
            converted_value_data_type="text",
            attack_identifier=None,
            conversation_id="",
            labels={},
            prompt_metadata={},
        )
    ]

    mock_response = httpx.Response(200, content=b"<html><body>Match: 1234</body></html>")
    mock_request.return_value = mock_response

    response = await mock_http_target.send_prompt_async(message=message)
    assert len(response) == 1
    assert response[0].get_value() == "Match: 1234"
    assert mock_request.call_count == 1
    mock_request.assert_called_with(
        method="POST",
        url="https://example.com/",
        headers={"host": "example.com", "content-type": "application/json"},
        content='{"prompt": "test_prompt"}',
        follow_redirects=True,
    )


@patch("httpx.AsyncClient.request")
async def test_send_prompt_async_keeps_original_template(mock_request, mock_http_target, mock_http_response):
    original_http_request = mock_http_target.http_request
    mock_request.return_value = mock_http_response

    # Send first prompt
    message = MagicMock()
    message.message_pieces = [
        MagicMock(
            converted_value="test_prompt",
            converted_value_data_type="text",
            attack_identifier=None,
            conversation_id="",
            labels={},
            prompt_metadata={},
        )
    ]
    response = await mock_http_target.send_prompt_async(message=message)

    assert len(response) == 1
    assert response[0].get_value() == "value1"
    assert mock_http_target.http_request == original_http_request

    assert mock_request.call_count == 1
    mock_request.assert_called_with(
        method="POST",
        url="https://example.com/",
        headers={"host": "example.com", "content-type": "application/json"},
        content='{"prompt": "test_prompt"}',
        follow_redirects=True,
    )

    # Send second prompt
    second_message = MagicMock()
    second_message.message_pieces = [
        MagicMock(
            converted_value="second_test_prompt",
            converted_value_data_type="text",
            attack_identifier=None,
            conversation_id="",
            labels={},
            prompt_metadata={},
        )
    ]
    await mock_http_target.send_prompt_async(message=second_message)

    # Assert that the original template is still the same
    assert mock_http_target.http_request == original_http_request

    assert mock_request.call_count == 2
    # Verify HTTP requests were made with the correct prompts

    mock_request.assert_any_call(
        method="POST",
        url="https://example.com/",
        headers={"host": "example.com", "content-type": "application/json"},
        content='{"prompt": "test_prompt"}',
        follow_redirects=True,
    )
    mock_request.assert_any_call(
        method="POST",
        url="https://example.com/",
        headers={"host": "example.com", "content-type": "application/json"},
        content='{"prompt": "second_test_prompt"}',
        follow_redirects=True,
    )


async def test_http_target_with_injected_client(patch_central_database):
    custom_client = httpx.AsyncClient(timeout=30.0, verify=False, headers={"X-Custom-Header": "test_value"})

    sample_request = (
        'POST / HTTP/1.1\nHost: example.com\nContent-Type: application/json\n\n{"prompt": "{PLACEHOLDER_PROMPT}"}'
    )

    target = HTTPTarget.with_client(
        client=custom_client,
        http_request=sample_request,
        prompt_regex_string="{PLACEHOLDER_PROMPT}",
        callback_function=get_http_target_json_response_callback_function(key="mock_key"),
    )

    assert target._client is custom_client

    with patch.object(custom_client, "request") as mock_request:
        mock_response = MagicMock()
        mock_response.content = b'{"mock_key": "test_value"}'
        mock_request.return_value = mock_response

        message = MagicMock()
        message.message_pieces = [
            MagicMock(
                converted_value="test_prompt",
                converted_value_data_type="text",
                attack_identifier=None,
                conversation_id="",
                labels={},
                prompt_metadata={},
            )
        ]

        response = await target.send_prompt_async(message=message)

        assert len(response) == 1
        assert response[0].get_value() == "test_value"
        assert mock_request.call_count == 1
        args, kwargs = mock_request.call_args
        assert args == ()
        assert kwargs["method"] == "POST"
        assert kwargs["url"] == "https://example.com/"
        headers = kwargs.get("headers", {})
        assert headers["host"] == "example.com"
        assert headers["content-type"] == "application/json"
        assert headers["x-custom-header"] == "test_value"

    assert not custom_client.is_closed, "Client must not be closed after sending a prompt"
    await custom_client.aclose()


@pytest.mark.usefixtures("patch_central_database")
def test_http_target_init_basic():
    http_request = "POST / HTTP/1.1\nHost: example.com\n\n"
    target = HTTPTarget(http_request=http_request)
    assert target.http_request == http_request
    assert target.prompt_regex_string == "{PROMPT}"
    assert target.use_tls is True
    assert target.callback_function is None
    assert target.httpx_client_kwargs == {}
    assert target._client is None


@pytest.mark.usefixtures("patch_central_database")
def test_http_target_init_with_all_args():
    http_request = "POST / HTTP/1.1\nHost: example.com\n\n"

    def return_parsed(response):
        return "parsed"

    client_kwargs = {"timeout": 5}
    target = HTTPTarget(
        http_request=http_request,
        prompt_regex_string="{PLACEHOLDER_PROMPT}",
        use_tls=False,
        callback_function=return_parsed,
        max_requests_per_minute=10,
        follow_redirects=True,
        **client_kwargs,
    )
    assert target.http_request == http_request
    assert target.prompt_regex_string == "{PLACEHOLDER_PROMPT}"
    assert target.use_tls is False
    assert target.callback_function == return_parsed
    assert target.follow_redirects is True
    assert target.httpx_client_kwargs == client_kwargs
    assert target._client is None


@pytest.mark.usefixtures("patch_central_database")
def test_http_target_init_with_client_and_kwargs_raises():
    http_request = "POST / HTTP/1.1\nHost: example.com\n\n"
    client = MagicMock(spec=httpx.AsyncClient)
    with pytest.raises(ValueError) as excinfo:
        HTTPTarget(
            http_request=http_request,
            client=client,
            timeout=10,
        )
    assert "Cannot provide both a pre-configured client and additional httpx client kwargs." in str(excinfo.value)


@pytest.mark.usefixtures("patch_central_database")
def test_http_target_init_with_client_only():
    http_request = "POST / HTTP/1.1\nHost: example.com\n\n"
    client = MagicMock(spec=httpx.AsyncClient)
    target = HTTPTarget(
        http_request=http_request,
        client=client,
    )
    assert target._client is client
    assert target.httpx_client_kwargs == {}


def test_parse_raw_http_request_strips_transfer_encoding_and_content_length(patch_central_database):
    request = (
        "POST / HTTP/1.1\n"
        "Host: example.com\n"
        "Content-Type: application/json\n"
        "Transfer-Encoding: chunked\n"
        "Content-Length: 100\n\n"
        '{"test": "val"}'
    )
    target = HTTPTarget(http_request=request)
    headers, body, _, _, _ = target.parse_raw_http_request(request)

    assert headers == {"host": "example.com", "content-type": "application/json"}
    assert "transfer-encoding" not in headers
    assert "content-length" not in headers
    assert body == '{"test": "val"}'


async def test_send_prompt_async_strips_transfer_encoding_and_sends_intact_multibyte_body(patch_central_database):
    captured_requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, text="ok")

    raw_request = "POST / HTTP/1.1\nHost: example.com\nTransfer-Encoding: chunked\nContent-Length: 5\n\n{PROMPT}"
    target = HTTPTarget(http_request=raw_request, transport=httpx.MockTransport(handle_request))
    message = Message.from_prompt(prompt="你好世界", role="user")

    response = await target.send_prompt_async(message=message)

    assert len(response) == 1
    assert response[0].get_value() == "ok"
    assert len(captured_requests) == 1

    req = captured_requests[0]
    assert "transfer-encoding" not in req.headers
    # Verify httpx computed the accurate UTF-8 byte length on the wire (12 bytes for "你好世界")
    assert req.headers.get("content-length") == "12"
    assert req.content == "你好世界".encode()


def test_parse_raw_http_request_combines_repeated_cookies(patch_central_database):
    request = "GET / HTTP/2\nHost: example.com\nCookie: session=abc\ncookie: csrf=def\nCOOKIE: theme=dark\n\n"
    target = HTTPTarget(http_request=request)

    headers, _, _, _, _ = target.parse_raw_http_request(request)

    assert headers["cookie"] == "session=abc; csrf=def; theme=dark"


def test_parse_raw_http_request_ignores_empty_cookie_lines(patch_central_database):
    request = "GET / HTTP/1.1\nHost: example.com\nCookie:\nCookie: session=abc\nCookie:\n\n"
    target = HTTPTarget(http_request=request)

    headers, _, _, _, _ = target.parse_raw_http_request(request)

    assert headers["cookie"] == "session=abc"


def test_parse_raw_http_request_keeps_last_of_other_repeated_headers(patch_central_database):
    request = (
        "GET / HTTP/1.1\n"
        "Host: first.example.com\n"
        "Host: second.example.com\n"
        "Authorization: Bearer old\n"
        "Authorization: Bearer new\n\n"
    )
    target = HTTPTarget(http_request=request)

    headers, _, url, _, _ = target.parse_raw_http_request(request)

    assert headers["authorization"] == "Bearer new"
    assert url == "https://second.example.com/"


def test_parse_raw_http_request_raw_header_replaces_client_default(patch_central_database):
    request = "GET / HTTP/1.1\nHost: example.com\nCookie: session=abc\n\n"
    target = HTTPTarget(http_request=request, client=httpx.AsyncClient(headers={"Cookie": "default=1"}))

    headers, _, _, _, _ = target.parse_raw_http_request(request)

    assert headers["cookie"] == "session=abc"


async def test_send_prompt_async_sends_every_repeated_cookie(patch_central_database):
    captured_requests: list[httpx.Request] = []

    def handle_request(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, text="ok")

    raw_request = "POST / HTTP/1.1\nHost: example.com\nCookie: session=abc\nCookie: csrf=def\n\n{PROMPT}"
    target = HTTPTarget(http_request=raw_request, transport=httpx.MockTransport(handle_request))

    await target.send_prompt_async(message=Message.from_prompt(prompt="hello", role="user"))

    assert captured_requests[0].headers["cookie"] == "session=abc; csrf=def"
