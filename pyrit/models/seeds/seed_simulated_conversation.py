# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
SeedSimulatedConversation - Configuration for generating simulated conversations dynamically.

This class holds the configuration (prompts, num_turns) needed to generate a simulated
conversation. It is a pure data/config class - the actual generation logic lives in
`pyrit.executor.attack.component.simulated_conversation`.

As a Seed subclass, it can be stored in the database for reproducibility tracking.
"""

from __future__ import annotations

import enum
import hashlib
import importlib.metadata
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, WrapValidator, field_validator, model_validator

from pyrit.common.deprecation import print_deprecation_message
from pyrit.common.path import EXECUTOR_SIMULATED_TARGET_PATH
from pyrit.models.seeds.seed import Seed
from pyrit.models.seeds.seed_prompt import SeedPrompt

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import ValidatorFunctionWrapHandler

logger = logging.getLogger(__name__)

SIMULATED_TARGET_REQUIRED_PARAMETERS = ["objective", "num_turns"]
SIMULATED_TARGET_PARAMETER_ERROR = "Simulated target system prompt must have objective and num_turns parameters"
NEXT_MESSAGE_REQUIRED_PARAMETERS = ["objective", "conversation_context"]
NEXT_MESSAGE_PARAMETER_ERROR = "Next message system prompt must have objective and conversation_context parameters"

_PROMPT_PATH_REMOVED_IN = "1.4.0"


class SimulatedTargetSystemPromptPaths(enum.Enum):
    """Enum for predefined simulated target system prompt paths."""

    COMPLIANT = Path(EXECUTOR_SIMULATED_TARGET_PATH, "compliant.yaml").resolve()


class NextMessageSystemPromptPaths(enum.Enum):
    """Enum for predefined next message generation system prompt paths."""

    DIRECT = Path(EXECUTOR_SIMULATED_TARGET_PATH, "direct_next_message.yaml").resolve()


def load_simulated_target_prompt(template_path: str | Path) -> SeedPrompt:
    """
    Load a simulated target system prompt template and verify it declares the parameters it needs.

    Args:
        template_path: Path to the YAML file containing the prompt template.

    Returns:
        SeedPrompt: The loaded template.

    Raises:
        ValueError: If the template does not declare ``objective`` and ``num_turns``.
    """
    return SeedPrompt.from_yaml_with_required_parameters(
        template_path=template_path,
        required_parameters=SIMULATED_TARGET_REQUIRED_PARAMETERS,
        error_message=SIMULATED_TARGET_PARAMETER_ERROR,
    )


def load_next_message_prompt(template_path: str | Path) -> SeedPrompt:
    """
    Load a next-message system prompt template and verify it declares the parameters it needs.

    Args:
        template_path: Path to the YAML file containing the prompt template.

    Returns:
        SeedPrompt: The loaded template.

    Raises:
        ValueError: If the template does not declare ``objective`` and ``conversation_context``.
    """
    return SeedPrompt.from_yaml_with_required_parameters(
        template_path=template_path,
        required_parameters=NEXT_MESSAGE_REQUIRED_PARAMETERS,
        error_message=NEXT_MESSAGE_PARAMETER_ERROR,
    )


def _load_compliant_simulated_target_prompt() -> SeedPrompt:
    """
    Load the default compliant simulated target prompt.

    Returns:
        SeedPrompt: The compliant simulated target template.
    """
    return load_simulated_target_prompt(SimulatedTargetSystemPromptPaths.COMPLIANT.value)


def resolve_prompt_source(
    *,
    prompt: SeedPrompt | None,
    path: str | Path | None,
    prompt_name: str,
    path_name: str,
    load_prompt: Callable[[str | Path], SeedPrompt],
) -> SeedPrompt | None:
    """
    Choose between a canonical prompt and its deprecated path input, loading the path if needed.

    Every boundary that still accepts a ``*_system_prompt_path`` uses this so they all warn the
    same way and reject the same ambiguity. It reads from disk, so async callers must run it
    through ``asyncio.to_thread``.

    Args:
        prompt: The canonical prompt, if the caller supplied one.
        path: The deprecated path input, if the caller supplied one.
        prompt_name: Name of the canonical parameter, used in messages.
        path_name: Name of the deprecated parameter, used in messages.
        load_prompt: Loader that turns the path into a prompt.

    Returns:
        SeedPrompt | None: The resolved prompt, or None when neither input was supplied.

    Raises:
        ValueError: If both the canonical prompt and its deprecated path are supplied.
    """
    if path is None:
        return prompt
    warn_prompt_path_deprecated(prompt=prompt, prompt_name=prompt_name, path_name=path_name)
    return load_prompt(path)


def warn_prompt_path_deprecated(*, prompt: SeedPrompt | None, prompt_name: str, path_name: str) -> None:
    """
    Reject an ambiguous prompt source and warn that the path input is deprecated.

    Separated from loading so async callers can run this on the event loop, where the warning
    points at their own call site, and send only the file read to a worker thread.

    Args:
        prompt: The canonical prompt, if the caller supplied one alongside the path.
        prompt_name: Name of the canonical parameter, used in messages.
        path_name: Name of the deprecated parameter, used in messages.

    Raises:
        ValueError: If both the canonical prompt and its deprecated path are supplied.
    """
    if prompt is not None:
        raise ValueError(f"Set only one of {prompt_name} or {path_name}; both were provided.")
    print_deprecation_message(old_item=path_name, new_item=prompt_name, removed_in=_PROMPT_PATH_REMOVED_IN)


# Deprecated ``*_path`` inputs, mapped to the canonical field they populate and the loader that
# resolves them. These are accepted at construction only; they never become model fields.
_LEGACY_PROMPT_PATH_INPUTS: dict[str, tuple[str, Any]] = {
    "adversarial_chat_system_prompt_path": ("adversarial_chat_system_prompt", SeedPrompt.from_yaml_file),
    "simulated_target_system_prompt_path": ("simulated_target_system_prompt", load_simulated_target_prompt),
    "next_message_system_prompt_path": ("next_message_system_prompt", load_next_message_prompt),
}


def _prompt_identity(prompt: SeedPrompt | None) -> dict[str, Any] | None:
    """
    Project a prompt onto the fields that change how a simulated conversation behaves.

    Only the fields that alter rendering, validation, or the response contract are kept, so they
    survive reconstruction from a persisted record. Descriptive metadata such as ``name``,
    ``description``, and ``source`` is deliberately left out: renaming a template should not change
    the configuration's identity. Those fields are therefore not restored from a persisted record.

    ``is_jinja_template`` is also left out. It marks a template that still needs its one-shot
    path substitution, and the projected value has already had it, so restoring the flag would
    make a rebuilt prompt render a second time.

    Args:
        prompt: The prompt to project, or None.

    Returns:
        dict[str, Any] | None: The projected prompt, or None when no prompt was given.
    """
    if prompt is None:
        return None
    return {
        "value": prompt.value,
        "data_type": prompt.data_type,
        "parameters": list(prompt.parameters or []),
        "response_json_schema": prompt.response_json_schema,
    }


def _keep_prompt_instance(value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    """
    Accept an existing prompt as-is instead of validating it again.

    ``SeedPrompt`` substitutes dataset paths into a trusted template once, while it is being
    validated. Re-validating an instance would run that substitution a second time, which
    consumes template syntax the executor is meant to fill in and mutates the caller's object.

    Args:
        value: The raw value supplied for the field.
        handler: The validator to fall back to for anything that is not already a prompt.

    Returns:
        Any: The prompt unchanged, or the result of normal validation.
    """
    if isinstance(value, SeedPrompt):
        return value
    return handler(value)


# Prompt fields hold templates that have already been prepared, so an existing instance is
# never re-validated (see _keep_prompt_instance).
SystemPrompt = Annotated[SeedPrompt, WrapValidator(_keep_prompt_instance)]


class SeedSimulatedConversation(Seed):
    """
    Configuration for generating a simulated conversation dynamically.

    This class holds the prompts and parameters needed to generate prepended conversation
    content by running an adversarial chat against a simulated (compliant) target.

    This is a pure configuration class. The actual generation is performed by
    `generate_simulated_conversation_async` in the executor layer, which accepts
    this config along with runtime dependencies (adversarial_chat target, scorer).

    The `value` property returns a JSON serialization of the config for database
    storage and deduplication.

    The prompts are canonical `SeedPrompt` templates, so a technique carries its prompt text
    rather than a file location and can be inspected or edited in place. The matching
    `*_system_prompt_path` inputs are still accepted at construction for legacy callers and for
    reading records persisted before the change; they are resolved immediately and are not
    stored on the model.

    To change a prompt, edit the `SeedPrompt` and then build a new `SeedSimulatedConversation`
    from it. Like the other fields, `value` is a snapshot taken when the configuration is
    validated, so mutating a prompt on an existing instance changes what executes without
    changing what is stored.

    Attributes:
        num_turns: Number of conversation turns to generate.
        adversarial_chat_system_prompt: System-prompt SeedPrompt for the adversarial chat.
        simulated_target_system_prompt: System-prompt SeedPrompt for the simulated target.
            Defaults to the compliant prompt if not specified.
        next_message_system_prompt: Optional system-prompt SeedPrompt for generating
            an additional user message after the simulated conversation. If provided, a single
            LLM call generates a final user message that attempts to get the target to fulfill
            the objective in their next response.

    """

    # Discriminator field for the polymorphic Seed union (see seed_group.SeedUnion).
    seed_type: Literal["simulated_conversation"] = "simulated_conversation"

    # Simulated conversations are always text. Narrowing the base field rejects non-text values
    # up-front rather than silently dropping them downstream.
    data_type: Literal["text"] = "text"

    # value is computed from the config in the after-validator. The base default of "" plus a
    # before-validator that strips any user-supplied value keeps round-trips clean: a dumped
    # value comes back in, is dropped, then is recomputed (and matches if the config matches).
    value: str = ""

    # Simulated conversations are general techniques by default.
    is_general_technique: bool = True

    num_turns: int = 3
    sequence: int = 0
    # A prompt supplied directly is trusted as-is. The declared-parameter contract for the
    # simulated target and next message is enforced where a template is loaded from a file,
    # by load_simulated_target_prompt and load_next_message_prompt.
    adversarial_chat_system_prompt: SystemPrompt
    simulated_target_system_prompt: SystemPrompt = Field(default_factory=_load_compliant_simulated_target_prompt)
    next_message_system_prompt: SystemPrompt | None = None
    pyrit_version: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _strip_user_value(cls, data: Any) -> Any:
        """
        Drop any user-supplied ``value`` from dict input; it is always recomputed in the
        after-validator. This keeps round-tripping clean and makes the API honest about the
        fact that ``value`` is a derived JSON serialization of the config.

        Returns:
            The data with ``value`` removed if it was a dict; otherwise the input unchanged.
        """
        if isinstance(data, dict) and "value" in data:
            data = dict(data)
            data.pop("value", None)
        return data

    @field_validator("simulated_target_system_prompt", mode="before")
    @classmethod
    def _default_simulated_target_prompt(cls, value: Any) -> Any:
        # Reconstruction from memory may pass an explicit None; fall back to the compliant default.
        if value is None:
            return _load_compliant_simulated_target_prompt()
        return value

    @model_validator(mode="before")
    @classmethod
    def _resolve_legacy_prompt_paths(cls, data: Any) -> Any:
        """
        Resolve deprecated ``*_system_prompt_path`` inputs into their canonical prompt fields.

        Runs in ``mode="before"`` so the path keys are removed from the input before Pydantic's
        ``extra="forbid"`` rejects them. The paths are therefore never model fields and never
        reach ``value`` or ``get_identifier()``.

        Args:
            data: Raw input passed to the model constructor.

        Returns:
            The input with any legacy path keys replaced by loaded prompts.

        Raises:
            ValueError: If a canonical prompt and its deprecated path are both supplied.
        """
        if not isinstance(data, dict):
            return data

        resolved = data
        for path_key, (prompt_key, load_prompt) in _LEGACY_PROMPT_PATH_INPUTS.items():
            if path_key not in resolved:
                continue
            if resolved is data:
                resolved = dict(data)
            resolved[prompt_key] = resolve_prompt_source(
                prompt=resolved.get(prompt_key),
                path=resolved.pop(path_key),
                prompt_name=f"SeedSimulatedConversation.{prompt_key}",
                path_name=f"SeedSimulatedConversation.{path_key}",
                load_prompt=load_prompt,
            )
        return resolved

    @model_validator(mode="after")
    def _validate_and_compute_value(self) -> SeedSimulatedConversation:
        if self.num_turns <= 0:
            raise ValueError("num_turns must be a positive integer")
        if self.sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if not self.pyrit_version:
            self.pyrit_version = importlib.metadata.version("pyrit")
        self.value = self._compute_value()
        return self

    def _config_dict(self) -> dict[str, Any]:
        """
        Build the canonical configuration mapping shared by ``value`` and ``get_identifier()``.

        Returns:
            dict[str, Any]: The configuration, with each prompt reduced to its behavioral fields.

        """
        return {
            "num_turns": self.num_turns,
            "sequence": self.sequence,
            "adversarial_chat_system_prompt": _prompt_identity(self.adversarial_chat_system_prompt),
            "simulated_target_system_prompt": _prompt_identity(self.simulated_target_system_prompt),
            "next_message_system_prompt": _prompt_identity(self.next_message_system_prompt),
            "pyrit_version": self.pyrit_version,
        }

    def _compute_value(self) -> str:
        """
        Compute the value field as JSON serialization of config.

        Returns:
            str: Deterministic JSON representation of this configuration.

        """
        return json.dumps(self._config_dict(), sort_keys=True, separators=(",", ":"))

    def get_identifier(self) -> dict[str, Any]:
        """
        Get an identifier dict capturing this configuration for comparison/storage.

        Returns:
            Dictionary with configuration details.

        """
        return {"__type__": "SeedSimulatedConversation", **self._config_dict()}

    def compute_hash(self) -> str:
        """
        Compute a deterministic hash of this configuration.

        Returns:
            A SHA256 hash string representing the configuration.

        """
        identifier = self.get_identifier()
        config_json = json.dumps(identifier, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(config_json.encode("utf-8")).hexdigest()

    @staticmethod
    def load_simulated_target_system_prompt(
        *,
        objective: str,
        num_turns: int,
        simulated_target_system_prompt_path: str | Path | None = None,
    ) -> str | None:
        """
        Load and render the simulated target system prompt.

        .. deprecated::
            Render ``SeedSimulatedConversation.simulated_target_system_prompt`` directly with
            ``SeedPrompt.render_template_value(objective=..., num_turns=...)`` instead. This
            helper reads from disk, so it must not be called from an async path.

        If no path is provided, returns None (no system prompt).
        Validates that the template has required `objective` and `num_turns` parameters.

        Args:
            objective: The objective to render into the template.
            num_turns: The number of turns to render into the template.
            simulated_target_system_prompt_path: Optional path to the prompt YAML file.
                If None, no system prompt is used.

        Returns:
            The rendered system prompt string, or None if no path is provided.

        Raises:
            ValueError: If the template doesn't have required parameters.

        """
        print_deprecation_message(
            old_item="SeedSimulatedConversation.load_simulated_target_system_prompt",
            new_item="SeedSimulatedConversation.simulated_target_system_prompt.render_template_value",
            removed_in=_PROMPT_PATH_REMOVED_IN,
        )
        if simulated_target_system_prompt_path is None:
            return None

        template = load_simulated_target_prompt(simulated_target_system_prompt_path)

        return template.render_template_value(
            objective=objective,
            num_turns=num_turns,
        )

    @property
    def sequence_range(self) -> range:
        """
        The range of sequence numbers this simulated conversation will occupy.

        Each turn generates 2 messages (user + assistant), so num_turns generates
        num_turns * 2 messages. If next_message_system_prompt is set, an additional
        user message is added at the end.

        Returns:
            A range object representing the sequence numbers.

        """
        message_count = self.num_turns * 2 + (1 if self.next_message_system_prompt else 0)
        return range(self.sequence, self.sequence + message_count)

    def __repr__(self) -> str:
        """
        Return a concise representation of this simulated conversation seed.

        Returns:
            str: Simulated conversation summary string.

        """
        has_next_msg = self.next_message_system_prompt is not None
        # ``name`` is descriptive metadata that _prompt_identity drops, so a seed rebuilt from a
        # persisted record has none. Omit the fragment rather than print a placeholder.
        prompt_name = self.adversarial_chat_system_prompt.name
        adversarial = f", adversarial_prompt={prompt_name}" if prompt_name else ""
        return (
            f"<SeedSimulatedConversation(num_turns={self.num_turns}, sequence={self.sequence}, "
            f"next_message={has_next_msg}{adversarial})>"
        )
