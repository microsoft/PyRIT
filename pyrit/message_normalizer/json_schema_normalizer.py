# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json

from pyrit.message_normalizer.message_normalizer import MessageListNormalizer
from pyrit.models import JSON_SCHEMA_METADATA_KEY, JsonSchemaDefinition, Message, MessagePiece

# Default text appended to a message piece when the target cannot natively
# enforce a JSON schema. The exact phrasing mirrors the legacy in-prompt
# instructions in the refusal scorer YAMLs and is generic enough to apply
# to any schema body. The schema itself is pretty-printed so a model can
# reason about its structure during decoding. Exposed as a module-level
# constant (and as ``JsonSchemaNormalizer.DEFAULT_SCHEMA_INSTRUCTIONS_TEMPLATE``)
# so callers building a custom template can extend the default phrasing
# instead of re-deriving it from scratch.
DEFAULT_SCHEMA_INSTRUCTIONS_TEMPLATE = (
    "\n\n"
    "### Response format\n\n"
    'Do not include the following text or variations of it in the response: "Possible JSON response".\n'
    "Start the response with **only** a valid JSON object.\n\n"
    "The response should conform to the following JSON schema:\n"
    "{schema_json}\n\n"
    "Remember, only return the JSON object.\n"
    'Do not include the following text or variations of it in the response: "Possible JSON response"\n'
)


class JsonSchemaNormalizer(MessageListNormalizer[Message]):
    """
    Adapts JSON-schema metadata for targets that cannot enforce it natively.

    The conversation normalization pipeline invokes this normalizer when the
    JSON_SCHEMA capability is not natively supported by the prompt target.
    For every message piece carrying a schema in
    ``JSON_SCHEMA_METADATA_KEY``:

    * If the piece is text, the schema is rendered into prompt text appended
      to the existing value so the model is still instructed to produce a
      conforming JSON object via prompt engineering.
    * Otherwise (non-text pieces — image, audio, video, etc.) the schema
      metadata is simply removed; non-text modalities have no natural place
      to embed a textual schema instruction.

    The original schema metadata key is removed in both cases so downstream
    consumers do not attempt to enforce a schema the target cannot honor.

    Callers that need a different prose wrapper around the schema (for
    example, to match a domain-specific style guide or to suppress a
    particular phrasing) can pass a custom ``schema_instructions_template``
    to ``__init__``. The template must contain a ``{schema_json}``
    placeholder where the pretty-printed schema body is substituted.
    """

    # Exposed as a class attribute so callers building a custom template
    # can compose it from the default instead of duplicating the prose.
    DEFAULT_SCHEMA_INSTRUCTIONS_TEMPLATE: str = DEFAULT_SCHEMA_INSTRUCTIONS_TEMPLATE

    def __init__(
        self,
        *,
        schema_instructions_template: str = DEFAULT_SCHEMA_INSTRUCTIONS_TEMPLATE,
    ) -> None:
        """
        Initialize the normalizer with an optional custom instructions template.

        Args:
            schema_instructions_template (str): A ``str.format``-style template
                appended to text pieces. Must contain a ``{schema_json}``
                placeholder, which is replaced with the pretty-printed JSON
                schema body. Defaults to
                ``DEFAULT_SCHEMA_INSTRUCTIONS_TEMPLATE``.

        Raises:
            ValueError: If ``schema_instructions_template`` does not contain
                a ``{schema_json}`` placeholder.
        """
        if "{schema_json}" not in schema_instructions_template:
            raise ValueError("schema_instructions_template must contain a '{schema_json}' placeholder.")
        self._schema_instructions_template = schema_instructions_template

    async def normalize_async(self, messages: list[Message]) -> list[Message]:
        """
        Return messages adapted for a target that does not support JSON schemas.

        New pieces and messages are constructed so the input (and any persisted
        metadata) is never mutated in place. Pieces without the schema key are
        copied through unchanged.

        Args:
            messages (list[Message]): The conversation messages to adapt.

        Returns:
            list[Message]: Messages whose pieces either have the schema rendered
            into their text value or have the schema metadata stripped, depending
            on the piece data type.
        """
        return [self._adapt_message(message=message) for message in messages]

    def _adapt_message(self, *, message: Message) -> Message:
        """
        Return a copy of a single message with each piece's schema metadata adapted.

        When no piece in the message carries the schema key, the original
        ``message`` is returned unchanged (object identity is preserved) so the
        pipeline is a true no-op for prompts that never embedded a schema.

        Args:
            message (Message): The message whose pieces should be adapted.

        Returns:
            Message: The original message when nothing was adapted, otherwise a
            new message whose pieces have the schema either rendered into text
            or stripped from metadata.
        """
        new_pieces: list[MessagePiece] = []
        changed = False
        for piece in message.message_pieces:
            if JSON_SCHEMA_METADATA_KEY not in piece.prompt_metadata:
                new_pieces.append(piece)
                continue

            new_pieces.append(self._adapt_piece(piece=piece))
            changed = True

        if not changed:
            return message

        return Message(new_pieces)

    def _adapt_piece(self, *, piece: MessagePiece) -> MessagePiece:
        """
        Return a new piece with the schema metadata key removed.

        For text pieces, the schema is additionally rendered into the piece's
        ``converted_value`` so the model is still instructed (via prompt text)
        to produce JSON conforming to the schema. For non-text pieces the
        schema is simply dropped from metadata.

        Args:
            piece (MessagePiece): The piece carrying a schema in its metadata.

        Returns:
            MessagePiece: A new piece without the schema metadata key, with the
            schema rendered into the text value when ``converted_value_data_type``
            is ``"text"``.
        """
        schema = piece.prompt_metadata[JSON_SCHEMA_METADATA_KEY]
        new_metadata = {key: value for key, value in piece.prompt_metadata.items() if key != JSON_SCHEMA_METADATA_KEY}

        updates: dict[str, object] = {"prompt_metadata": new_metadata}
        if piece.converted_value_data_type == "text":
            updates["converted_value"] = self._append_schema_instructions(text=piece.converted_value, schema=schema)

        return piece.model_copy(update=updates)

    def _append_schema_instructions(self, *, text: str, schema: JsonSchemaDefinition) -> str:
        """
        Return ``text`` with a schema-instructions block appended.

        Args:
            text (str): The existing prompt text.
            schema (JsonSchemaDefinition): The JSON schema to render.

        Returns:
            str: ``text`` followed by the configured instructions template with
            the schema body substituted in.
        """
        schema_json = json.dumps(schema, indent=2)
        return text + self._schema_instructions_template.format(schema_json=schema_json)
