# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Base Seed class for representing seed data with various attributes and metadata.

This module is the foundation for all seed types in PyRIT.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, TypeVar, Union

from jinja2 import StrictUndefined, Undefined
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, ConfigDict, Field

from pyrit.models.literals import PromptDataType  # noqa: TC001  (runtime-required by Pydantic field annotations)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

# TypeVar for generic return type in class methods
T = TypeVar("T", bound="Seed")


class PartialUndefined(Undefined):
    """Jinja undefined value that preserves unresolved placeholders as text."""

    # Return the original placeholder format
    def __str__(self) -> str:
        """
        Render unresolved variable placeholders in template format.

        Returns:
            str: Placeholder text or empty string.

        """
        return f"{{{{ {self._undefined_name} }}}}" if self._undefined_name else ""

    def __repr__(self) -> str:
        """
        Return the placeholder representation for debugging contexts.

        Returns:
            str: Placeholder text or empty string.

        """
        return f"{{{{ {self._undefined_name} }}}}" if self._undefined_name else ""

    def __iter__(self) -> Iterator[object]:
        """
        Return an empty iterator to prevent iteration over undefined variables.

        Returns:
            Iterator[object]: Empty iterator.

        """
        return iter([])

    def __bool__(self) -> bool:
        """
        Evaluate as truthy to avoid falsey-branch side effects.

        Returns:
            bool: Always True.

        """
        return True  # Ensures it doesn't evaluate to False


class Seed(BaseModel):
    """Represents seed data with various attributes and metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    # The actual prompt value, which can be a string or a file path
    value: str

    # SHA256 hash of the value, used for deduplication
    value_sha256: Optional[str] = None

    # Unique identifier for the prompt
    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4)

    # Name of the prompt
    name: Optional[str] = None

    # Name of the dataset this prompt belongs to
    dataset_name: Optional[str] = None

    # Categories of harm associated with this prompt
    harm_categories: Optional[list[str]] = Field(default_factory=list)

    # Description of the prompt
    description: Optional[str] = None

    # Authors of the prompt
    authors: Optional[list[str]] = Field(default_factory=list)

    # Groups affiliated with the prompt
    groups: Optional[list[str]] = Field(default_factory=list)

    # Source of the prompt
    source: Optional[str] = None

    # Date when the prompt was added to the dataset
    date_added: Optional[datetime] = Field(default_factory=lambda: datetime.now(tz=timezone.utc))

    # User who added the prompt to the dataset
    added_by: Optional[str] = None

    # Arbitrary metadata that can be attached to the prompt
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict)

    # Unique identifier for the prompt group
    prompt_group_id: Optional[uuid.UUID] = None

    # Alias for the prompt group
    prompt_group_alias: Optional[str] = None

    # Whether this seed represents a general attack technique (not tied to a specific objective)
    is_general_technique: bool = False

    # When True, value contains Jinja2 template syntax that should be rendered as-is.
    # When False (default), value is treated as literal text and auto-escaped with {% raw %} tags
    # to prevent template injection. Trusted sources (YAML files) set this to True automatically.
    is_jinja_template: bool = False

    # The type of data this seed represents (e.g., text, image_path, audio_path, video_path).
    # SeedPrompt overrides the default to None and infers it from the value; other seed types
    # narrow it to Literal["text"].
    data_type: PromptDataType = "text"

    def render_template_value(self, **kwargs: Any) -> str:
        """
        Render self.value as a template with provided parameters.

        Args:
            kwargs:Key-value pairs to replace in the SeedPrompt value.

        Returns:
            A new prompt with the parameters applied.

        Raises:
            ValueError: If parameters are missing or invalid in the template.

        """
        template_identifier = self.name or "<unnamed template>"

        try:
            env = SandboxedEnvironment(undefined=StrictUndefined)
            is_jinja_template = env.from_string(self.value)
            return is_jinja_template.render(**kwargs)
        except Exception as e:
            raise ValueError(
                f"Error rendering template '{template_identifier}': {str(e)}. "
                f"Template value preview: {self.value[:100]}..."
            ) from e

    def render_template_value_silent(self, **kwargs: Any) -> str:
        """
        Render self.value as a template with provided parameters. For parameters in the template
        that are not provided as kwargs here, this function will leave them as is instead of raising an error.

        Args:
            kwargs: Key-value pairs to replace in the SeedPrompt value.

        Returns:
            A new prompt with the parameters applied.

        Raises:
            ValueError: If parameters are missing or invalid in the template.

        """
        # Check if the template contains Jinja2 control structures (for loops, if statements, etc.)
        # If it does, and we don't have all required parameters, don't render it to preserve the structure

        has_control_structures = bool(re.search(r"\{%[-\s]*(for|if|block|macro|call)", self.value))

        if has_control_structures:
            # Check if all parameters in control structures are provided
            # Extract variable names from {% for var in collection %} patterns
            for_vars = re.findall(r"\{%[-\s]*for\s+\w+\s+in\s+(\w+)", self.value)
            if any(var not in kwargs for var in for_vars):
                # Don't render if we're missing loop collection variables - preserve the template as-is
                return self.value

        # Create a Jinja template with PartialUndefined placeholders
        env = SandboxedEnvironment(undefined=PartialUndefined)
        is_jinja_template = env.from_string(self.value)

        try:
            # Render the template with the provided kwargs
            return is_jinja_template.render(**kwargs)
        except Exception as e:
            logger.error("Error rendering template: %s", e)
            return self.value

    async def set_sha256_value_async(self) -> None:
        """
        Compute the SHA256 hash value asynchronously.
        It should be called after prompt `value` is serialized to text,
        as file paths used in the `value` may have changed from local to memory storage paths.

        Note, this method is async due to the blob retrieval. And because of that, we opted
        to take it out of main and setter functions. The disadvantage is that it must be explicitly called.
        """
        from pyrit.models.data_type_serializer import data_serializer_factory

        original_serializer = data_serializer_factory(
            category="seed-prompt-entries", data_type=self.data_type, value=self.value
        )

        self.value_sha256 = await original_serializer.get_sha256()

    @staticmethod
    def escape_for_jinja(value: str) -> str:
        """
        Wrap a string in Jinja2 {% raw %}...{% endraw %} tags to prevent template evaluation.

        Use this for any untrusted or externally-fetched text that will be stored as a
        Seed value, to ensure it is treated as literal text by the Jinja2 renderer.

        Args:
            value: The raw string to escape.

        Returns:
            str: The string wrapped in {% raw %}...{% endraw %} tags.
        """
        return f"{{% raw %}}{value}{{% endraw %}}"

    @classmethod
    def from_yaml_file(cls: type[T], file: Union[str, Path]) -> T:
        """
        Create a new Seed from a YAML file, marking it as a trusted Jinja2 template.

        Thin shim that delegates to :func:`pyrit.models.seeds.seed_loader.load_seed_from_yaml`;
        file I/O and the ``is_jinja_template`` trust marker live in the loader module.

        Args:
            file: The input file path.

        Returns:
            A new Seed of the specific subclass type.

        Raises:
            FileNotFoundError: If the path does not resolve to an existing file.
            ValueError: If the YAML file is invalid or empty.
        """
        from pyrit.models.seeds.seed_loader import load_seed_from_yaml

        return load_seed_from_yaml(file, cls=cls)
