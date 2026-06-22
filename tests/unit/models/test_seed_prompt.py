# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import textwrap
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


# --- response_json_schema resolution (response_json_schema_name is init-only) ---


class TestSeedPromptResponseJsonSchemaResolution:
    """Tests covering how SeedPrompt resolves an embedded JSON schema.

    ``response_json_schema_name`` is accepted as a constructor kwarg / YAML key
    and resolved by a ``model_validator(mode="before")`` into
    ``response_json_schema``, but **not** stored as an instance attribute.
    Downstream readers (scorers, memory, attacks) only see
    ``response_json_schema``.
    """

    def test_name_is_not_a_pydantic_field(self):
        """Regression guard: ``response_json_schema_name`` must stay init-only.

        If a future change converts it into a real Pydantic field, this test
        breaks loudly so we catch the leak before it propagates into memory
        persistence or scorer identifier params.
        """
        field_names = set(SeedPrompt.model_fields.keys())
        assert "response_json_schema_name" not in field_names
        assert "response_json_schema" in field_names

    def test_inline_schema_left_unchanged(self):
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        sp = SeedPrompt(value="hi", data_type="text", response_json_schema=schema)
        assert sp.response_json_schema == schema
        # Init-only kwarg is consumed by the before-validator; it must not land on the instance.
        assert "response_json_schema_name" not in sp.__dict__

    def test_name_resolves_against_registry(self):
        sp = SeedPrompt(value="hi", data_type="text", response_json_schema_name="true_false_with_rationale")
        # Init-only kwarg is not stored on the instance — only the resolved schema is.
        assert "response_json_schema_name" not in sp.__dict__
        assert sp.response_json_schema is not None
        assert sp.response_json_schema["type"] == "object"
        assert set(sp.response_json_schema["required"]) == {"score_value", "rationale"}

    def test_name_resolution_is_deep_copy(self):
        sp_a = SeedPrompt(value="a", data_type="text", response_json_schema_name="true_false_with_rationale")
        sp_a.response_json_schema["properties"]["score_value"]["type"] = "string"

        sp_b = SeedPrompt(value="b", data_type="text", response_json_schema_name="true_false_with_rationale")
        assert sp_b.response_json_schema["properties"]["score_value"]["type"] == "boolean"

    def test_setting_both_inline_and_name_raises(self):
        with pytest.raises(ValueError, match="Set only one of response_json_schema"):
            SeedPrompt(
                value="hi",
                data_type="text",
                response_json_schema={"type": "object"},
                response_json_schema_name="true_false_with_rationale",
            )

    def test_unknown_schema_name_raises(self):
        with pytest.raises(ValueError, match="not registered in COMMON_JSON_SCHEMAS"):
            SeedPrompt(value="hi", data_type="text", response_json_schema_name="definitely_not_real")

    def test_no_schema_set_leaves_field_none(self):
        sp = SeedPrompt(value="hi", data_type="text")
        assert sp.response_json_schema is None
        assert "response_json_schema_name" not in sp.__dict__

    def test_yaml_load_with_inline_schema(self, tmp_path):
        """A YAML file may inline the full schema body under ``response_json_schema``."""
        yaml_text = textwrap.dedent(
            """
            value: |
                Score this answer.
            data_type: text
            response_json_schema:
                type: object
                properties:
                    score_value:
                        type: string
                    rationale:
                        type: string
                required:
                    - score_value
                    - rationale
            """
        ).strip()
        yaml_file = tmp_path / "inline_schema.yaml"
        yaml_file.write_text(yaml_text, encoding="utf-8")

        sp = SeedPrompt.from_yaml_file(yaml_file)
        assert "response_json_schema_name" not in sp.__dict__
        assert sp.response_json_schema == {
            "type": "object",
            "properties": {
                "score_value": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["score_value", "rationale"],
        }

    def test_yaml_load_with_schema_name(self, tmp_path):
        """A YAML file may reference a named registry schema instead of inlining."""
        yaml_text = textwrap.dedent(
            """
            value: |
                Score this answer.
            data_type: text
            response_json_schema_name: true_false_with_rationale
            """
        ).strip()
        yaml_file = tmp_path / "named_schema.yaml"
        yaml_file.write_text(yaml_text, encoding="utf-8")

        sp = SeedPrompt.from_yaml_file(yaml_file)
        # Name is consumed at construction and discarded; only the resolved
        # body is observable on the instance.
        assert "response_json_schema_name" not in sp.__dict__
        assert sp.response_json_schema is not None
        assert set(sp.response_json_schema["required"]) == {"score_value", "rationale"}

    def test_yaml_load_setting_both_raises(self, tmp_path):
        """Inline schema + name in the same YAML must raise on construction."""
        yaml_text = textwrap.dedent(
            """
            value: hi
            data_type: text
            response_json_schema:
                type: object
            response_json_schema_name: true_false_with_rationale
            """
        ).strip()
        yaml_file = tmp_path / "both.yaml"
        yaml_file.write_text(yaml_text, encoding="utf-8")

        with pytest.raises(ValueError, match="Set only one of response_json_schema"):
            SeedPrompt.from_yaml_file(yaml_file)
