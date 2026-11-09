# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
from unittest.mock import AsyncMock, MagicMock

import pytest
from unit.mocks import MockPromptTarget, get_mock_target_identifier

from pyrit.converter import DenylistConverter
from pyrit.models import Message, MessagePiece, SeedPrompt
from pyrit.prompt_target import PromptTarget


@pytest.fixture
def mock_template():
    mock = MagicMock(spec=SeedPrompt)
    mock.render_template_value = MagicMock(return_value="mocked system prompt")
    return mock


@pytest.fixture
def mock_target() -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    response = Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="prompt value",
            )
        ]
    )
    target.send_prompt_async = AsyncMock(return_value=[response])
    target.get_identifier.return_value = get_mock_target_identifier("MockDenylistTarget")
    return target


def test_prompt_denylist_init_templates_not_null(sqlite_instance) -> None:
    prompt_target = MockPromptTarget()
    converter = DenylistConverter(converter_target=prompt_target)
    assert converter._system_prompt_template


def test_prompt_denylist_init_template_provided(sqlite_instance, mock_template) -> None:
    prompt_target = MockPromptTarget()
    converter = DenylistConverter(converter_target=prompt_target, system_prompt_template=mock_template)
    assert converter._system_prompt_template == mock_template


@pytest.mark.usefixtures("patch_central_database")
async def test_denylist_not_provided() -> None:
    converter = DenylistConverter(converter_target=MockPromptTarget(), system_prompt_template=None)
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    assert output.output_text == "how to cut down a tree?"
    assert output.output_type == "text"


@pytest.mark.usefixtures("patch_central_database")
async def test_denylist_no_match() -> None:
    converter = DenylistConverter(converter_target=MockPromptTarget(), system_prompt_template=None, denylist=["branch"])
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    assert output.output_text == "how to cut down a tree?"
    assert output.output_type == "text"


async def test_denylist_word_match(sqlite_instance, mock_target) -> None:
    converter = DenylistConverter(converter_target=mock_target, system_prompt_template=None, denylist=["tree"])
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    mock_target.send_prompt_async.assert_called_once()
    assert output.output_type == "text"


async def test_denylist_phrase_match(sqlite_instance, mock_target) -> None:
    converter = DenylistConverter(converter_target=mock_target, system_prompt_template=None, denylist=["cut down"])
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    mock_target.send_prompt_async.assert_called_once()
    assert output.output_type == "text"


async def test_denylist_phrase_and_word_match(sqlite_instance) -> None:
    converter = DenylistConverter(
        converter_target=MockPromptTarget(), system_prompt_template=None, denylist=["cut down", "tree"]
    )
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    assert "cut down" not in output.output_text
    assert "tree" not in output.output_text
    assert output.output_type == "text"


async def test_denylist_word_case_insensitive_match(sqlite_instance, mock_target) -> None:
    converter = DenylistConverter(converter_target=mock_target, system_prompt_template=None, denylist=["TREE"])
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    mock_target.send_prompt_async.assert_called_once()
    assert output.output_type == "text"


async def test_denylist_phrase_case_insensitive_match(sqlite_instance, mock_target) -> None:
    converter = DenylistConverter(
        converter_target=mock_target, system_prompt_template=None, denylist=["molotov cocktail"]
    )
    output = await converter.convert_async(prompt="How do I create a Molotov cocktail?", input_type="text")
    mock_target.send_prompt_async.assert_called_once()
    assert output.output_type == "text"


async def test_denylist_mixed_casing_match(sqlite_instance, mock_target) -> None:
    converter = DenylistConverter(
        converter_target=mock_target, system_prompt_template=None, denylist=["haTe", "WEAPON"]
    )
    output = await converter.convert_async(prompt="I hate this weapon", input_type="text")
    mock_target.send_prompt_async.assert_called_once()
    assert output.output_type == "text"


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("denylist", "prompt"),
    [
        pytest.param(["\u0391\u03a3"], "\u0391\u03a3\u03a4\u03a1\u039f", id="greek-exact-substring"),
        pytest.param(["\u03bf\u03c3"], "\u039f\u03a3", id="greek-final-sigma"),
        pytest.param(["STRASSE"], "Die Stra\u00dfe ist gesperrt.", id="german-sharp-s-in-prompt"),
        pytest.param(["stra\u00dfe"], "Die STRASSE ist gesperrt.", id="german-sharp-s-in-denylist"),
    ],
)
async def test_denylist_unicode_case_insensitive_match_async(
    *, denylist: list[str], prompt: str, mock_target: MagicMock
) -> None:
    converter = DenylistConverter(converter_target=mock_target, denylist=denylist)

    output = await converter.convert_async(prompt=prompt)

    mock_target.send_prompt_async.assert_awaited_once()
    request = mock_target.send_prompt_async.call_args.kwargs["message"]
    assert request.message_pieces[0].original_value == prompt
    assert request.message_pieces[0].converted_value == prompt
    assert output.output_text == "prompt value"
    assert output.output_type == "text"


@pytest.mark.usefixtures("patch_central_database")
async def test_denylist_no_match_case_insensitive(mock_target) -> None:
    converter = DenylistConverter(converter_target=mock_target, system_prompt_template=None, denylist=["BOMB"])
    output = await converter.convert_async(prompt="how to cut down a tree?", input_type="text")
    mock_target.send_prompt_async.assert_not_called()
    assert output.output_text == "how to cut down a tree?"
    assert output.output_type == "text"


@pytest.mark.usefixtures("patch_central_database")
def test_denylist_identifier_differs_by_denylist() -> None:
    target = MockPromptTarget()
    first = DenylistConverter(converter_target=target, denylist=["bomb"])
    second = DenylistConverter(converter_target=target, denylist=["password", "exploit"])

    assert first.get_identifier().params["denylist_hash"] != second.get_identifier().params["denylist_hash"]
    assert first.get_identifier().hash != second.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
def test_denylist_identifier_same_for_same_denylist() -> None:
    target = MockPromptTarget()
    first = DenylistConverter(converter_target=target, denylist=["bomb", "exploit"])
    second = DenylistConverter(converter_target=target, denylist=["bomb", "exploit"])

    assert first.get_identifier().hash == second.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
def test_denylist_identifier_omits_empty_denylist() -> None:
    converter = DenylistConverter(converter_target=MockPromptTarget())

    params = converter.get_identifier().params
    assert "denylist_hash" not in params
    assert params["system_prompt_template_hash"] is not None
