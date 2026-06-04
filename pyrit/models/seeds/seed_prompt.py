# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedPrompt class for representing seed prompts with role and sequence information.
"""

from __future__ import annotations

import logging
import os
from dataclasses import InitVar, dataclass, field
from typing import TYPE_CHECKING, Optional, Union

from tinytag import TinyTag

from pyrit.common.path import PATHS_DICT
from pyrit.models import DataTypeSerializer
from pyrit.models.json_schema_definition import get_common_json_schema
from pyrit.models.seeds.seed import Seed

if TYPE_CHECKING:
    import uuid
    from collections.abc import Sequence
    from pathlib import Path

    from pyrit.models import Message
    from pyrit.models.json_schema_definition import JsonSchemaDefinition
    from pyrit.models.literals import ChatMessageRole, PromptDataType

logger = logging.getLogger(__name__)


@dataclass
class SeedPrompt(Seed):
    """Represents a seed prompt with various attributes and metadata."""

    # The type of data this prompt represents (e.g., text, image_path, audio_path, video_path)
    # This field shadows the base class property to allow per-prompt data types
    data_type: Optional[PromptDataType] = None

    # Optional JSON schema for constraining the scoring response. When set and the
    # target supports JSON schemas, it is forwarded so the target can enforce the
    # response shape; otherwise the normalization pipeline omits it.
    response_json_schema: JsonSchemaDefinition | None = None

    # Init-only YAML/constructor sugar: pass a registry name (e.g.
    # ``true_false_with_rationale``) and ``__post_init__`` resolves it against
    # ``COMMON_JSON_SCHEMAS`` into ``response_json_schema``. Because it is an
    # ``InitVar`` it is NOT an instance attribute after construction — runtime
    # consumers (scorers, memory, attacks) read ``response_json_schema`` only.
    # Mutually exclusive with ``response_json_schema`` — setting both raises
    # ``ValueError``.
    response_json_schema_name: InitVar[Optional[str]] = None

    # Role of the prompt in a conversation (e.g., "user", "assistant")
    role: Optional[ChatMessageRole] = None

    # Sequence number for ordering prompts in a conversation, prompts with
    # the same sequence number are grouped together if they also share the same prompt_group_id
    sequence: int = 0

    # Parameters that can be used in the prompt template
    parameters: Optional[Sequence[str]] = field(default_factory=list)

    def __post_init__(self, response_json_schema_name: Optional[str]) -> None:
        """
        Render template placeholders, resolve named schemas, and infer data_type.

        Args:
            response_json_schema_name: Optional registry name supplied via the
                ``response_json_schema_name`` constructor kwarg or YAML key. It
                is an ``InitVar`` and is **not** stored on the instance; it is
                resolved here into ``response_json_schema`` and then discarded.

        Raises:
            ValueError: If both ``response_json_schema`` and
                ``response_json_schema_name`` are set, if the named schema is not
                registered in ``COMMON_JSON_SCHEMAS``, or if a file-based
                ``data_type`` cannot be inferred from extension.

        """
        # Resolve a named JSON schema before anything else so callers can rely
        # on ``response_json_schema`` being populated after construction.
        self._resolve_response_json_schema_name(response_json_schema_name)

        # Only trusted templates (is_jinja_template=True, e.g. from YAML files) are rendered
        # through Jinja. Untrusted text (e.g. from remote datasets) must NOT be rendered — a
        # crafted payload containing "{% endraw %}" can escape the raw wrapper and execute
        # arbitrary Jinja expressions. See seed_objective.py for the same pattern.
        if self.is_jinja_template:
            self.value = self.render_template_value_silent(**PATHS_DICT)

        if not self.data_type:
            # If data_type is not provided, infer it from the value
            # Note: Does not assign 'error' or 'url' implicitly
            if os.path.isfile(self.value):
                _, ext = os.path.splitext(self.value)
                ext = ext.lstrip(".").lower()
                if ext in ["mp4", "avi", "mov", "mkv", "ogv", "flv", "wmv", "webm"]:
                    self.data_type = "video_path"
                elif ext in ["flac", "mp3", "mpeg", "mpga", "m4a", "ogg", "wav"]:
                    self.data_type = "audio_path"
                elif ext in ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif"]:
                    self.data_type = "image_path"
                else:
                    raise ValueError(f"Unable to infer data_type from file extension: {ext}")
            else:
                self.data_type = "text"

    def _resolve_response_json_schema_name(self, response_json_schema_name: Optional[str]) -> None:
        """
        Resolve a ``response_json_schema_name`` value against ``COMMON_JSON_SCHEMAS``.

        Populates ``response_json_schema`` with a deep copy of the named schema
        so downstream consumers (scorers, attacks, converters) can read a
        single field regardless of how the schema was supplied.

        Args:
            response_json_schema_name: Registry name to resolve, or ``None`` to
                skip resolution.

        Raises:
            ValueError: If both ``response_json_schema`` and
                ``response_json_schema_name`` are set, or if the name is not
                registered in ``COMMON_JSON_SCHEMAS``.
        """
        if response_json_schema_name is None:
            return

        if self.response_json_schema is not None:
            raise ValueError(
                "Set only one of response_json_schema or response_json_schema_name on SeedPrompt; "
                f"both were provided (name={response_json_schema_name!r})."
            )

        try:
            self.response_json_schema = get_common_json_schema(response_json_schema_name)
        except KeyError as exc:
            raise ValueError(
                f"response_json_schema_name {response_json_schema_name!r} is not registered "
                "in COMMON_JSON_SCHEMAS."
            ) from exc

    def set_encoding_metadata(self) -> None:
        """
        Set encoding metadata for the prompt within metadata dictionary. For images, this is just the
        file format. For audio and video, this also includes bitrate (kBits/s as int), samplerate (samples/second
        as int), bitdepth (as int), filesize (bytes as int), and duration (seconds as int) if the file type is
        supported by TinyTag. Example supported file types include: MP3, MP4, M4A, and WAV.
        """
        if self.data_type not in ["audio_path", "video_path", "image_path"]:
            return
        if self.metadata is None:
            self.metadata = {}
        extension = DataTypeSerializer.get_extension(self.value)
        if extension:
            extension = extension.lstrip(".")
            self.metadata.update({"format": extension})
        if self.data_type in ["audio_path", "video_path"]:
            if TinyTag.is_supported(self.value):
                try:
                    tag = TinyTag.get(self.value)
                    bitrate = int(round(tag.bitrate)) if tag.bitrate is not None else 0
                    duration = int(round(tag.duration)) if tag.duration is not None else 0
                    self.metadata.update(
                        {
                            "bitrate": bitrate,
                            "samplerate": tag.samplerate if tag.samplerate is not None else 0,
                            "bitdepth": tag.bitdepth if tag.bitdepth is not None else 0,
                            "filesize": tag.filesize if tag.filesize is not None else 0,
                            "duration": duration,
                        }
                    )
                except Exception as ex:
                    logger.error(f"Error getting audio/video data for {self.value}: {ex}")
            else:
                logger.warning(
                    f"Getting audio/video data via TinyTag is not supported for {self.value}.\
                                If needed, update metadata manually."
                )

    @classmethod
    def from_yaml_with_required_parameters(
        cls,
        template_path: Union[str, Path],
        required_parameters: list[str],
        error_message: Optional[str] = None,
    ) -> SeedPrompt:
        """
        Load a Seed from a YAML file and validate that it contains specific parameters.

        Args:
            template_path: Path to the YAML file containing the template.
            required_parameters: List of parameter names that must exist in the template.
            error_message: Custom error message if validation fails. If None, a default message is used.

        Returns:
            SeedPrompt: The loaded and validated SeedPrompt of the specific subclass type.

        Raises:
            ValueError: If the template doesn't contain all required parameters.

        """
        sp = cls.from_yaml_file(template_path)

        if sp.parameters is None or not all(param in sp.parameters for param in required_parameters):
            if error_message is None:
                error_message = f"Template must have these parameters: {', '.join(required_parameters)}"
            raise ValueError(f"{error_message}: '{sp}'")

        return sp

    @staticmethod
    def from_messages(
        messages: list[Message],
        *,
        starting_sequence: int = 0,
        prompt_group_id: Optional[uuid.UUID] = None,
    ) -> list[SeedPrompt]:
        """
        Convert a list of Messages to a list of SeedPrompts.

        Each MessagePiece becomes a SeedPrompt. All pieces from the same message
        share the same sequence number, preserving the grouping.

        Args:
            messages: List of Messages to convert.
            starting_sequence: The starting sequence number. Defaults to 0.
            prompt_group_id: Optional group ID to assign to all prompts. Defaults to None.

        Returns:
            List of SeedPrompts with incrementing sequence numbers per message.

        """
        seed_prompts: list[SeedPrompt] = []
        current_sequence = starting_sequence

        for message in messages:
            role: ChatMessageRole = message.api_role

            for piece in message.message_pieces:
                seed_prompt = SeedPrompt(
                    value=piece.converted_value,
                    data_type=piece.converted_value_data_type,
                    role=role,
                    sequence=current_sequence,
                    prompt_group_id=prompt_group_id,
                )
                seed_prompts.append(seed_prompt)

            current_sequence += 1

        return seed_prompts
