# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

import pytest

from pyrit.message_normalizer import JsonSchemaNormalizer
from pyrit.models import JSON_SCHEMA_METADATA_KEY, Message, MessagePiece


@pytest.fixture
def normalizer() -> JsonSchemaNormalizer:
    return JsonSchemaNormalizer()


def _text_piece(
    *, value: str = "hello", metadata: dict | None = None, conversation_id: str | None = None
) -> MessagePiece:
    kwargs: dict = {
        "role": "user",
        "original_value": value,
        "original_value_data_type": "text",
        "prompt_metadata": metadata or {},
    }
    if conversation_id is not None:
        kwargs["conversation_id"] = conversation_id
    return MessagePiece(**kwargs)


def _image_piece(
    *, value: str = "fake.png", metadata: dict | None = None, conversation_id: str | None = None
) -> MessagePiece:
    kwargs: dict = {
        "role": "user",
        "original_value": value,
        "original_value_data_type": "image_path",
        "prompt_metadata": metadata or {},
    }
    if conversation_id is not None:
        kwargs["conversation_id"] = conversation_id
    return MessagePiece(**kwargs)


class TestJsonSchemaNormalizer:
    async def test_text_piece_gets_schema_appended_to_converted_value(self, normalizer: JsonSchemaNormalizer) -> None:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        piece = _text_piece(value="Answer the question.", metadata={JSON_SCHEMA_METADATA_KEY: schema})
        message = Message([piece])

        result = await normalizer.normalize_async([message])
        out_piece = result[0].message_pieces[0]

        assert JSON_SCHEMA_METADATA_KEY not in out_piece.prompt_metadata
        assert out_piece.converted_value.startswith("Answer the question.")
        assert "### Response format" in out_piece.converted_value
        # Schema body is JSON-rendered into the appended text so the model can
        # reason about its structure.
        assert json.dumps(schema, indent=2) in out_piece.converted_value

    async def test_text_piece_preserves_other_metadata(self, normalizer: JsonSchemaNormalizer) -> None:
        piece = _text_piece(
            metadata={
                JSON_SCHEMA_METADATA_KEY: {"type": "object"},
                "response_format": "json",
                "other": 7,
            },
        )
        result = await normalizer.normalize_async([Message([piece])])
        new_metadata = result[0].message_pieces[0].prompt_metadata
        assert JSON_SCHEMA_METADATA_KEY not in new_metadata
        assert new_metadata == {"response_format": "json", "other": 7}

    async def test_non_text_piece_only_strips_key(self, normalizer: JsonSchemaNormalizer) -> None:
        schema = {"type": "object"}
        piece = _image_piece(value="fake.jpg", metadata={JSON_SCHEMA_METADATA_KEY: schema, "extra": "stay"})
        original_converted_value = piece.converted_value

        result = await normalizer.normalize_async([Message([piece])])
        out_piece = result[0].message_pieces[0]

        assert JSON_SCHEMA_METADATA_KEY not in out_piece.prompt_metadata
        assert out_piece.prompt_metadata == {"extra": "stay"}
        # Non-text pieces have no natural place for a textual schema instruction
        # so the converted_value is left alone.
        assert out_piece.converted_value == original_converted_value

    async def test_no_schema_is_noop(self, normalizer: JsonSchemaNormalizer) -> None:
        piece = _text_piece(value="just say hi", metadata={"unrelated": True})
        message = Message([piece])

        result = await normalizer.normalize_async([message])

        # No-op: the original Message instance is returned unchanged.
        assert result[0] is message

    async def test_input_pieces_not_mutated(self, normalizer: JsonSchemaNormalizer) -> None:
        schema = {"type": "object"}
        piece = _text_piece(value="hi", metadata={JSON_SCHEMA_METADATA_KEY: schema})

        await normalizer.normalize_async([Message([piece])])

        # The original piece still carries the schema and its unchanged text.
        assert piece.prompt_metadata == {JSON_SCHEMA_METADATA_KEY: schema}
        assert piece.converted_value == "hi"

    async def test_mixed_pieces_in_message_each_handled(self, normalizer: JsonSchemaNormalizer) -> None:
        schema = {"type": "object"}
        conversation_id = "shared-conv-id"
        text_piece = _text_piece(
            value="t", metadata={JSON_SCHEMA_METADATA_KEY: schema}, conversation_id=conversation_id
        )
        image_piece = _image_piece(
            value="fake.png",
            metadata={JSON_SCHEMA_METADATA_KEY: schema, "k": 1},
            conversation_id=conversation_id,
        )
        no_schema_piece = _text_piece(value="z", metadata={"foo": "bar"}, conversation_id=conversation_id)

        result = await normalizer.normalize_async([Message([text_piece, image_piece, no_schema_piece])])
        out_pieces = result[0].message_pieces

        assert JSON_SCHEMA_METADATA_KEY not in out_pieces[0].prompt_metadata
        assert "### Response format" in out_pieces[0].converted_value

        assert JSON_SCHEMA_METADATA_KEY not in out_pieces[1].prompt_metadata
        assert out_pieces[1].converted_value == "fake.png"
        assert out_pieces[1].prompt_metadata == {"k": 1}

        # No-schema piece passed through with object identity preserved so the
        # rest of the pipeline can rely on cheap reference equality.
        assert out_pieces[2] is no_schema_piece

    async def test_multiple_messages(self, normalizer: JsonSchemaNormalizer) -> None:
        schema = {"type": "object"}
        msg_with_schema = Message([_text_piece(value="a", metadata={JSON_SCHEMA_METADATA_KEY: schema})])
        msg_without_schema = Message([_text_piece(value="b", metadata={})])

        result = await normalizer.normalize_async([msg_with_schema, msg_without_schema])
        assert "### Response format" in result[0].message_pieces[0].converted_value
        # No-schema message is passed through with object identity preserved.
        assert result[1] is msg_without_schema

    async def test_empty_messages_list(self, normalizer: JsonSchemaNormalizer) -> None:
        assert await normalizer.normalize_async([]) == []

    async def test_appended_text_lists_schema_keys(self, normalizer: JsonSchemaNormalizer) -> None:
        schema = {
            "type": "object",
            "properties": {
                "score_value": {"type": "string"},
                "rationale": {"type": "string"},
            },
            "required": ["score_value", "rationale"],
        }
        piece = _text_piece(value="prompt", metadata={JSON_SCHEMA_METADATA_KEY: schema})

        result = await normalizer.normalize_async([Message([piece])])
        appended = result[0].message_pieces[0].converted_value

        # Sanity-check that the rendered text actually surfaces schema field names.
        assert "score_value" in appended
        assert "rationale" in appended
        assert "JSON" in appended

    async def test_custom_template_is_used(self) -> None:
        """A caller-supplied template replaces the default prose wrapper."""
        custom = "\n<<SCHEMA START>>{schema_json}<<SCHEMA END>>"
        normalizer = JsonSchemaNormalizer(schema_instructions_template=custom)
        schema = {"type": "object"}
        piece = _text_piece(value="hi", metadata={JSON_SCHEMA_METADATA_KEY: schema})

        result = await normalizer.normalize_async([Message([piece])])
        out_value = result[0].message_pieces[0].converted_value

        assert "<<SCHEMA START>>" in out_value
        assert "<<SCHEMA END>>" in out_value
        # Default prose must not leak through when a custom template is supplied.
        assert "### Response format" not in out_value

    def test_template_without_placeholder_raises(self) -> None:
        """Validation up front avoids silent KeyError at normalize-time."""
        with pytest.raises(ValueError, match=r"\{schema_json\}"):
            JsonSchemaNormalizer(schema_instructions_template="no placeholder here")

    def test_default_template_is_exposed_as_class_attribute(self) -> None:
        """Callers can extend the default phrasing instead of redefining it."""
        assert "{schema_json}" in JsonSchemaNormalizer.DEFAULT_SCHEMA_INSTRUCTIONS_TEMPLATE
        assert "### Response format" in JsonSchemaNormalizer.DEFAULT_SCHEMA_INSTRUCTIONS_TEMPLATE
