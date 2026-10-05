# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedPrompt class for representing seed prompts with role and sequence information.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, model_validator
from tinytag import TinyTag

from pyrit.common.path import PATHS_DICT
from pyrit.models.literals import (  # noqa: TC001  (runtime-required by Pydantic field annotations)
    ChatMessageRole,
    PromptDataType,
)
from pyrit.models.seeds.seed import Seed
from pyrit.models.target.json_schema_definition import (  # noqa: TC001  (runtime-required by Pydantic field annotations)
    JsonSchemaDefinition,
    get_common_json_schema,
)

if TYPE_CHECKING:
    import uuid

    from pyrit.models import Message

logger = logging.getLogger(__name__)


class SeedPrompt(Seed):
    """Represents a seed prompt with various attributes and metadata."""

    # Discriminator field for the polymorphic Seed union (see seed_group.SeedUnion).
    seed_type: Literal["prompt"] = "prompt"

    # The type of data this prompt represents (e.g., text, image_path, audio_path, video_path)
    # This field overrides the base default to allow per-prompt data types inferred from the value
    data_type: PromptDataType | None = None

    # Optional JSON schema for constraining the scoring response. When set and the
    # target supports JSON schemas, it is forwarded so the target can enforce the
    # response shape; otherwise the normalization pipeline omits it.
    response_json_schema: JsonSchemaDefinition | None = None

    # Role of the prompt in a conversation (e.g., "user", "assistant")
    role: ChatMessageRole | None = None

    # Sequence number for ordering prompts in a conversation, prompts with
    # the same sequence number are grouped together if they also share the same prompt_group_id
    sequence: int = 0

    # Parameters that can be used in the prompt template
    parameters: list[str] | None = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _resolve_response_json_schema_name(cls, data: Any) -> Any:
        """
        Resolve a ``response_json_schema_name`` init kwarg into ``response_json_schema``.

        Runs in ``mode="before"`` so the name key is popped from the input dict
        before Pydantic's ``extra="forbid"`` rejects it. The base class sets
        ``extra="forbid"``, so ``response_json_schema_name`` cannot be a real
        field — this validator is the only place it is accepted.

        Args:
            data: Raw input passed to the model constructor. Typically a dict
                from YAML loading or direct kwargs.

        Returns:
            The (possibly mutated) input, with ``response_json_schema_name``
            removed and ``response_json_schema`` populated when applicable.

        Raises:
            ValueError: If both ``response_json_schema`` and
                ``response_json_schema_name`` are set, or if the name is not
                registered in ``COMMON_JSON_SCHEMAS``.
        """
        if not isinstance(data, dict):
            return data

        name = data.pop("response_json_schema_name", None)
        if name is None:
            return data

        if data.get("response_json_schema") is not None:
            raise ValueError(
                "Set only one of response_json_schema or response_json_schema_name on SeedPrompt; "
                f"both were provided (name={name!r})."
            )

        try:
            data["response_json_schema"] = get_common_json_schema(name)
        except KeyError as exc:
            raise ValueError(f"response_json_schema_name {name!r} is not registered in COMMON_JSON_SCHEMAS.") from exc
        return data

    @model_validator(mode="after")
    def _render_and_infer_data_type(self) -> SeedPrompt:
        """
        Render template placeholders and infer data_type after initialization.

        Returns:
            SeedPrompt: The validated prompt with rendered value and inferred data_type.

        Raises:
            ValueError: If file-based data type cannot be inferred from extension.

        """
        # Only trusted templates (is_jinja_template=True, e.g. from YAML files) are rendered
        # through Jinja. Untrusted text (e.g. from remote datasets) must NOT be rendered — a
        # crafted payload containing "{% endraw %}" can escape the raw wrapper and execute
        # arbitrary Jinja expressions. See seed_objective.py for the same pattern.
        if self.is_jinja_template:
            self.value = self.render_template_value_silent(**PATHS_DICT)

        if not self.data_type:
            # If data_type is not provided, infer it from the value
            # Note: Does not assign 'error' or 'url' implicitly
            # Guard against OSError / ValueError so values that aren't valid path
            # strings (too long, null bytes, etc.) are treated as text, matching
            # the prior os.path.isfile semantics.
            try:
                is_file = Path(self.value).is_file()
            except (OSError, ValueError):
                is_file = False
            if is_file:
                ext = Path(self.value).suffix
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

        return self

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
        extension = Path(self.value).suffix or None
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
                            "filesize": tag.filesize,
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
        template_path: str | Path,
        required_parameters: list[str],
        error_message: str | None = None,
    ) -> SeedPrompt:
        """
        Load a SeedPrompt from a YAML file and validate that it declares each required parameter.

        Thin shim that delegates to
        ``pyrit.models.seeds.yaml_seed_loader.load_seed_prompt_from_yaml_with_required_parameters``.

        Args:
            template_path: Path to the YAML file containing the template.
            required_parameters: List of parameter names that must exist in the template.
            error_message: Custom error message if validation fails. If None, a default message is used.

        Returns:
            SeedPrompt: The loaded and validated SeedPrompt.

        Raises:
            ValueError: If the template doesn't contain all required parameters.
        """
        # Deferred import: yaml_seed_loader imports SeedPrompt at module load, so importing
        # it at the top of this module would create a circular import.
        from pyrit.models.seeds.yaml_seed_loader import load_seed_prompt_from_yaml_with_required_parameters

        return load_seed_prompt_from_yaml_with_required_parameters(
            template_path, required_parameters, error_message=error_message
        )

    @classmethod
    def from_value_with_required_parameters(
        cls,
        value: str | SeedPrompt,
        *,
        required_parameters: list[str],
        error_message: str | None = None,
        component_name: str = "prompt",
    ) -> SeedPrompt:
        """
        Coerce an inline string or ``SeedPrompt`` into a ``SeedPrompt`` declaring ``required_parameters``.

        Inline strings are trusted: they are wrapped in a Jinja ``SeedPrompt`` whose declared
        parameters are set to ``required_parameters``. Explicitly provided ``SeedPrompt`` objects
        are validated against ``required_parameters`` and returned unchanged (never copied).

        Args:
            value: The inline string or SeedPrompt to coerce.
            required_parameters: Parameter names the resolved template must support.
            error_message: Optional custom error message for validation failures.
            component_name: Human-readable label used in the default failure message (ignored
                when ``error_message`` is provided).

        Returns:
            The resolved SeedPrompt.

        Raises:
            ValueError: If an explicitly provided SeedPrompt is missing required parameters.
        """
        if isinstance(value, SeedPrompt):
            # Validate only explicitly provided SeedPrompts against the required parameters.
            declared = value.parameters or []
            missing = [param for param in required_parameters if param not in declared]
            if missing:
                raise ValueError(error_message or f"{component_name} is missing required parameters: {missing}")
            return value

        # Inline strings are trusted — declare all required params so Jinja rendering works.
        return cls(
            value=value,
            is_jinja_template=True,
            parameters=list(required_parameters),
        )

    @staticmethod
    def reject_jinja_syntax(value: str, *, component_name: str = "prompt") -> None:
        """
        Raise if an inline string contains Jinja template syntax.

        Shared by callers that accept static, reviewable guidance rather than a template
        (e.g. ``compose_with_prefix``, ``AttackTechniqueFactory.create``).

        Args:
            value: The inline string to check.
            component_name: Human-readable label for ``value``, used in the failure message.

        Raises:
            ValueError: If value contains "{{", "{%", or "{#".
        """
        if any(delimiter in value for delimiter in ("{{", "{%", "{#")):
            raise ValueError(f"{component_name} must be static text without Jinja syntax.")

    @staticmethod
    def compose_with_prefix(
        *,
        base_prompt: SeedPrompt,
        prefix: str | SeedPrompt,
        required_parameters: list[str],
        base_component_name: str = "prompt",
        prefix_component_name: str = "prefix",
    ) -> SeedPrompt:
        """
        Prepend static guidance ahead of an already-resolved base prompt.

        Combines ``prefix`` with ``base_prompt`` (separated by a blank line, prefix first). The
        combined prompt declares the union of both prompts' parameters — not just
        ``required_parameters``, which is only the minimum each is validated against — and its
        ``response_json_schema`` is whichever of the two declares one (an error if both do).

        An inline string ``prefix`` must be static text (no Jinja syntax); an explicitly provided
        ``SeedPrompt`` prefix is exempt and validated against ``required_parameters`` like any
        other SeedPrompt.

        The combined prompt is marked ``is_jinja_template`` only when both components are, so
        composing never promotes untrusted text into a trusted template. Promoting it would
        Jinja-render a value that was deliberately left unrendered, letting a crafted
        ``{% endraw %}`` escape its raw wrapper (see ``_render_and_infer_data_type``).

        Descriptive metadata (``name``, ``description``, ``source``) is intentionally not carried
        over: the composed prompt is a distinct prompt and should not inherit the base template's
        identity.

        Args:
            base_prompt: The already-resolved base SeedPrompt that the prefix is layered ahead of.
            prefix: Inline string or SeedPrompt to prepend before ``base_prompt``.
            required_parameters: Minimum parameter names the resolved (and, if explicit, the
                prefix) template must support.
            base_component_name: Human-readable label for ``base_prompt``, used only in the
                ``response_json_schema`` conflict message.
            prefix_component_name: Human-readable label for ``prefix``, used in its validation
                messages and the ``response_json_schema`` conflict message.

        Returns:
            The combined SeedPrompt.

        Raises:
            ValueError: If prefix is an inline string containing Jinja syntax, if it is an
                explicitly provided SeedPrompt missing required parameters, or if both base_prompt
                and the resolved prefix declare a response_json_schema.
        """
        if isinstance(prefix, str):
            SeedPrompt.reject_jinja_syntax(prefix, component_name=prefix_component_name)

        prefix_prompt = SeedPrompt.from_value_with_required_parameters(
            prefix,
            required_parameters=required_parameters,
            component_name=prefix_component_name,
        )
        if base_prompt.response_json_schema is not None and prefix_prompt.response_json_schema is not None:
            raise ValueError(
                f"Both the {base_component_name} and {prefix_component_name} declare a "
                "response_json_schema; set the schema on only one of them."
            )
        combined_parameters = list(dict.fromkeys([*(prefix_prompt.parameters or []), *(base_prompt.parameters or [])]))
        return SeedPrompt(
            value=f"{prefix_prompt.value}\n\n{base_prompt.value}",
            is_jinja_template=base_prompt.is_jinja_template and prefix_prompt.is_jinja_template,
            parameters=combined_parameters,
            response_json_schema=base_prompt.response_json_schema or prefix_prompt.response_json_schema,
        )

    @staticmethod
    def from_messages(
        messages: list[Message],
        *,
        starting_sequence: int = 0,
        prompt_group_id: uuid.UUID | None = None,
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
