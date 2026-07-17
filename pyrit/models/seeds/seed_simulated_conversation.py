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
from typing import Any, Literal

from pydantic import field_validator, model_validator

from pyrit.common.path import EXECUTOR_SIMULATED_TARGET_PATH
from pyrit.models.seeds.seed import Seed
from pyrit.models.seeds.seed_prompt import SeedPrompt

logger = logging.getLogger(__name__)


class SimulatedTargetSystemPromptPaths(enum.Enum):
    """Enum for predefined simulated target system prompt paths."""

    COMPLIANT = Path(EXECUTOR_SIMULATED_TARGET_PATH, "compliant.yaml").resolve()


class NextMessageSystemPromptPaths(enum.Enum):
    """Enum for predefined next message generation system prompt paths."""

    DIRECT = Path(EXECUTOR_SIMULATED_TARGET_PATH, "direct_next_message.yaml").resolve()


class SeedSimulatedConversation(Seed):
    """
    Configuration for generating a simulated conversation dynamically.

    This class holds the paths and parameters needed to generate prepended conversation
    content by running an adversarial chat against a simulated (compliant) target.

    This is a pure configuration class. The actual generation is performed by
    `generate_simulated_conversation_async` in the executor layer, which accepts
    this config along with runtime dependencies (adversarial_chat target, scorer).

    The `value` property returns a JSON serialization of the config for database
    storage and deduplication.

    Attributes:
        num_turns: Number of conversation turns to generate.
        adversarial_chat_system_prompt_path: Path to the adversarial chat system prompt YAML.
        simulated_target_system_prompt_path: Path to the simulated target system prompt YAML.
            Defaults to the compliant prompt if not specified.
        next_message_system_prompt_path: Optional path to the system prompt for generating
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
    adversarial_chat_system_prompt_path: Path
    simulated_target_system_prompt_path: Path = SimulatedTargetSystemPromptPaths.COMPLIANT.value
    next_message_system_prompt_path: Path | None = None
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

    @field_validator("simulated_target_system_prompt_path", mode="before")
    @classmethod
    def _default_simulated_target_path(cls, value: Any) -> Any:
        # Reconstruction from memory may pass an explicit None; fall back to the compliant default.
        if value is None:
            return SimulatedTargetSystemPromptPaths.COMPLIANT.value
        return value

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

    def _compute_value(self) -> str:
        """
        Compute the value field as JSON serialization of config.

        Returns:
            str: Deterministic JSON representation of this configuration.

        """
        config = {
            "num_turns": self.num_turns,
            "sequence": self.sequence,
            "adversarial_chat_system_prompt_path": str(self.adversarial_chat_system_prompt_path),
            "simulated_target_system_prompt_path": str(self.simulated_target_system_prompt_path),
            "next_message_system_prompt_path": (
                str(self.next_message_system_prompt_path) if self.next_message_system_prompt_path else None
            ),
            "pyrit_version": self.pyrit_version,
        }
        return json.dumps(config, sort_keys=True, separators=(",", ":"))

    def get_identifier(self) -> dict[str, Any]:
        """
        Get an identifier dict capturing this configuration for comparison/storage.

        Returns:
            Dictionary with configuration details.

        """
        return {
            "__type__": "SeedSimulatedConversation",
            "num_turns": self.num_turns,
            "sequence": self.sequence,
            "adversarial_chat_system_prompt_path": str(self.adversarial_chat_system_prompt_path),
            "simulated_target_system_prompt_path": str(self.simulated_target_system_prompt_path),
            "next_message_system_prompt_path": (
                str(self.next_message_system_prompt_path) if self.next_message_system_prompt_path else None
            ),
            "pyrit_version": self.pyrit_version,
        }

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
        if simulated_target_system_prompt_path is None:
            return None

        template = SeedPrompt.from_yaml_with_required_parameters(
            template_path=simulated_target_system_prompt_path,
            required_parameters=["objective", "num_turns"],
            error_message="Simulated target system prompt must have objective and num_turns parameters",
        )

        return template.render_template_value(
            objective=objective,
            num_turns=num_turns,
        )

    @property
    def sequence_range(self) -> range:
        """
        The range of sequence numbers this simulated conversation will occupy.

        Each turn generates 2 messages (user + assistant), so num_turns generates
        num_turns * 2 messages. If next_message_system_prompt_path is set, an additional
        user message is added at the end.

        Returns:
            A range object representing the sequence numbers.

        """
        message_count = self.num_turns * 2 + (1 if self.next_message_system_prompt_path else 0)
        return range(self.sequence, self.sequence + message_count)

    def __repr__(self) -> str:
        """
        Return a concise representation of this simulated conversation seed.

        Returns:
            str: Simulated conversation summary string.

        """
        has_next_msg = self.next_message_system_prompt_path is not None
        return (
            f"<SeedSimulatedConversation(num_turns={self.num_turns}, sequence={self.sequence}, "
            f"next_message={has_next_msg}, "
            f"adversarial_path={self.adversarial_chat_system_prompt_path.name})>"
        )
