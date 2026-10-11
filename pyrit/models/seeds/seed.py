# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Base Seed class for representing seed data with various attributes and metadata.

This module is the foundation for all seed types in PyRIT.
"""

from __future__ import annotations

import functools
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, TypeVar, cast

from jinja2 import StrictUndefined, Undefined, meta
from jinja2.sandbox import SandboxedEnvironment
from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field

from pyrit.models.literals import PromptDataType  # noqa: TC001  (runtime-required by Pydantic field annotations)
from pyrit.models.seeds.seed_origin import SeedOrigin

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

# TypeVar for generic return type in class methods
T = TypeVar("T", bound="Seed")


def _ensure_aware_utc(value: Any) -> Any:
    """
    Coerce naive datetimes (and bare date strings) to UTC so AwareDatetime accepts them.

    Args:
        value: The raw value provided for a datetime field (string, datetime, or anything else).

    Returns:
        Any: A timezone-aware datetime when the input was naive or a parseable date string;
            otherwise the value unchanged for Pydantic to validate.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# Timezone-aware datetime that interprets naive inputs as UTC instead of rejecting them.
AwareDatetimeUTC = Annotated[AwareDatetime, BeforeValidator(_ensure_aware_utc)]


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


class _DeferRenderError(Exception):
    """Raised when an unresolved variable would decide a branch or a loop."""


class _DeferringUndefined(PartialUndefined):
    """
    Placeholder for the load-time render of trusted templates.

    It cannot decide a branch or a loop: answering at load would drop the {% if %} or {% for %} tags,
    and the later render with the real parameters could not decide again.
    """

    def __iter__(self) -> Iterator[object]:
        """
        Defer rendering instead of iterating over an unresolved variable.

        Raises:
            _DeferRenderError: Always.

        """
        raise _DeferRenderError(self._undefined_name)

    def __bool__(self) -> bool:
        """
        Defer rendering instead of testing an unresolved variable.

        Raises:
            _DeferRenderError: Always.

        """
        raise _DeferRenderError(self._undefined_name)

    def __eq__(self, other: object) -> bool:
        """
        Defer rendering instead of comparing an unresolved variable.

        Raises:
            _DeferRenderError: Always.

        """
        raise _DeferRenderError(self._undefined_name)

    def __ne__(self, other: object) -> bool:
        """
        Defer rendering instead of comparing an unresolved variable.

        Raises:
            _DeferRenderError: Always.

        """
        raise _DeferRenderError(self._undefined_name)

    __hash__ = Undefined.__hash__


_JinjaCallable = TypeVar("_JinjaCallable", bound="Callable[..., Any]")


def _deferring(function: _JinjaCallable) -> _JinjaCallable:
    # Jinja tests such as `is defined` and the `default` filter check the value's type, not its truth.
    # functools.wraps keeps Jinja's pass_environment marker, so the value may not be the first argument.
    @functools.wraps(function)
    def deferring(*args: Any, **kwargs: Any) -> Any:
        for arg in args:
            if isinstance(arg, _DeferringUndefined):
                raise _DeferRenderError(arg._undefined_name)
        return function(*args, **kwargs)

    # Same signature as the wrapped test or filter, so the environment's test and filter tables keep their types.
    return cast("_JinjaCallable", deferring)


class Seed(BaseModel):
    """Represents seed data with various attributes and metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    # The actual prompt value, which can be a string or a file path
    value: str

    # SHA256 hash of the value, used for deduplication
    value_sha256: str | None = None

    # Unique identifier for the prompt
    id: uuid.UUID | None = Field(default_factory=uuid.uuid4)

    # Name of the prompt
    name: str | None = None

    # Name of the dataset this prompt belongs to
    dataset_name: str | None = None

    origin: SeedOrigin = SeedOrigin.UNKNOWN

    # Categories of harm associated with this prompt
    harm_categories: list[str] | None = Field(default_factory=list)

    # Description of the prompt
    description: str | None = None

    # Authors of the prompt
    authors: list[str] | None = Field(default_factory=list)

    # Groups affiliated with the prompt
    groups: list[str] | None = Field(default_factory=list)

    # Source of the prompt
    source: str | None = None

    # Date when the prompt was added to the dataset
    date_added: AwareDatetimeUTC | None = Field(default_factory=lambda: datetime.now(tz=UTC))

    # User who added the prompt to the dataset
    added_by: str | None = None

    # Arbitrary metadata that can be attached to the prompt
    metadata: dict[str, Any] | None = Field(default_factory=dict)

    # Unique identifier for the prompt group
    prompt_group_id: uuid.UUID | None = None

    # Alias for the prompt group
    prompt_group_alias: str | None = None

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

    def _render_trusted_template_value(self, **kwargs: Any) -> str:
        """
        Render a trusted template at load time, keeping the decisions its later render must make.

        Behaves like render_template_value_silent, except when a missing parameter would decide a branch,
        a loop, a comparison, a Jinja test or the `default` filter. Then the template is kept as-is, with a
        `{% set %}` in front for each supplied parameter it uses, so later renders and stored copies keep them.

        Args:
            kwargs: Key-value pairs to replace in the SeedPrompt value.

        Returns:
            The rendered value, or the unchanged value when rendering is deferred.

        """
        env = SandboxedEnvironment(undefined=_DeferringUndefined)
        env.tests = {name: _deferring(test) for name, test in env.tests.items()}
        env.filters["default"] = env.filters["d"] = _deferring(env.filters["default"])

        try:
            return env.from_string(self.value).render(**kwargs)
        except _DeferRenderError:
            used = meta.find_undeclared_variables(env.parse(self.value))
            bindings = "".join(f"{{% set {name} = {str(kwargs[name])!r} %}}" for name in sorted(used & kwargs.keys()))
            return bindings + self.value
        except Exception as e:
            logger.error("Error rendering template: %s", e)
            return self.value

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
    def from_yaml_file(cls: type[T], file: str | Path) -> T:
        """
        Create a new Seed from a YAML file, marking it as a trusted Jinja2 template.

        Thin shim that delegates to ``load_seed_from_yaml`` in the ``yaml_seed_loader`` module;
        file I/O and the ``is_jinja_template`` trust marker live in the loader module.

        Args:
            file: The input file path.

        Returns:
            A new Seed of the specific subclass type.

        Raises:
            FileNotFoundError: If the path does not resolve to an existing file.
            ValueError: If the YAML file is invalid or empty.
        """
        # Deferred import: yaml_seed_loader imports Seed, so importing it at module top would cycle.
        from pyrit.models.seeds.yaml_seed_loader import load_seed_from_yaml

        return load_seed_from_yaml(file, cls=cls)
