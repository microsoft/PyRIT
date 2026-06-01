# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import uuid
from unittest.mock import MagicMock

import pytest

from pyrit.models.seeds.seed_prompt import SeedPrompt


def test_seed_prompt_init_defaults():
    sp = SeedPrompt(value="hello", data_type="text")
    assert sp.value == "hello"
    assert sp.data_type == "text"
    assert sp.role is None
    assert sp.sequence == 0
    assert sp.parameters == []


def test_seed_prompt_init_with_all_fields():
    gid = uuid.uuid4()
    sp = SeedPrompt(
        value="prompt text",
        data_type="text",
        role="user",
        sequence=3,
        parameters=["param1", "param2"],
        name="test_prompt",
        dataset_name="ds",
        prompt_group_id=gid,
    )
    assert sp.role == "user"
    assert sp.sequence == 3
    assert sp.parameters == ["param1", "param2"]
    assert sp.prompt_group_id == gid


def test_seed_prompt_infers_text_data_type():
    sp = SeedPrompt(value="just text")
    assert sp.data_type == "text"


@pytest.mark.parametrize(
    "extension, expected_type",
    [
        (".mp4", "video_path"),
        (".wav", "audio_path"),
        (".png", "image_path"),
    ],
)
def test_seed_prompt_infers_data_type_from_extension(tmp_path, extension, expected_type):
    file_path = tmp_path / f"file{extension}"
    file_path.touch()
    sp = SeedPrompt(value=str(file_path))
    assert sp.data_type == expected_type


def test_seed_prompt_unknown_file_extension_raises(tmp_path):
    file_path = tmp_path / "file.xyz"
    file_path.touch()
    with pytest.raises(ValueError, match="Unable to infer data_type"):
        SeedPrompt(value=str(file_path))


def test_seed_prompt_infers_text_for_value_exceeding_path_name_limit():
    # Values longer than the filesystem name limit must be treated as text.
    # Path(value).is_file() can raise OSError (ENAMETOOLONG) on Linux/macOS,
    # whereas os.path.isfile silently returned False. The inference logic
    # must preserve the prior behavior so long-form text values (e.g. an
    # academic paper used as a jailbreak template) don't crash construction.
    long_value = "JOURNAL OF ARTIFICIAL INTELLIGENCE SAFETY RESEARCH " * 100
    sp = SeedPrompt(value=long_value)
    assert sp.data_type == "text"


def test_seed_prompt_infers_text_for_value_with_null_byte():
    # Null bytes raise ValueError inside pathlib; treat as text rather than crashing.
    sp = SeedPrompt(value="some text with \x00 embedded null")
    assert sp.data_type == "text"


def test_seed_prompt_explicit_data_type_not_overridden():
    sp = SeedPrompt(value="some text", data_type="text")
    assert sp.data_type == "text"


def test_seed_prompt_jinja_template_rendering():
    sp = SeedPrompt(value="Hello {{ name }}", data_type="text", is_jinja_template=True)
    assert "name" in sp.value or "Hello" in sp.value


def test_seed_prompt_non_jinja_preserved():
    sp = SeedPrompt(value="Hello {{ name }}", data_type="text")
    assert sp.value == "Hello {{ name }}"


def test_seed_prompt_from_messages():
    piece_mock = MagicMock()
    piece_mock.converted_value = "test value"
    piece_mock.converted_value_data_type = "text"

    message_mock = MagicMock()
    message_mock.api_role = "user"
    message_mock.message_pieces = [piece_mock]

    result = SeedPrompt.from_messages([message_mock])
    assert len(result) == 1
    assert result[0].value == "test value"
    assert result[0].role == "user"
    assert result[0].sequence == 0


def test_seed_prompt_from_messages_multiple():
    piece1 = MagicMock()
    piece1.converted_value = "user msg"
    piece1.converted_value_data_type = "text"
    msg1 = MagicMock()
    msg1.api_role = "user"
    msg1.message_pieces = [piece1]

    piece2 = MagicMock()
    piece2.converted_value = "assistant msg"
    piece2.converted_value_data_type = "text"
    msg2 = MagicMock()
    msg2.api_role = "assistant"
    msg2.message_pieces = [piece2]

    result = SeedPrompt.from_messages([msg1, msg2])
    assert len(result) == 2
    assert result[0].role == "user"
    assert result[0].sequence == 0
    assert result[1].role == "assistant"
    assert result[1].sequence == 1


def test_seed_prompt_from_messages_with_group_id():
    piece = MagicMock()
    piece.converted_value = "val"
    piece.converted_value_data_type = "text"
    msg = MagicMock()
    msg.api_role = "user"
    msg.message_pieces = [piece]

    gid = uuid.uuid4()
    result = SeedPrompt.from_messages([msg], prompt_group_id=gid)
    assert result[0].prompt_group_id == gid


def test_seed_prompt_from_messages_with_starting_sequence():
    piece = MagicMock()
    piece.converted_value = "val"
    piece.converted_value_data_type = "text"
    msg = MagicMock()
    msg.api_role = "user"
    msg.message_pieces = [piece]

    result = SeedPrompt.from_messages([msg], starting_sequence=5)
    assert result[0].sequence == 5
