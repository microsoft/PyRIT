# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Configuration for generating a simulated tool-call history at attack time."""

from __future__ import annotations

import importlib.metadata
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import model_validator

from pyrit.models.seeds.seed import Seed
from pyrit.models.seeds.seed_simulated_conversation import SystemPrompt, _prompt_identity


class ToolHistoryShape(StrEnum):
    """How a simulated tool history is structured."""

    #: Tool calls and their outputs only, optionally after one user message that sets up the task.
    TOOL_LOOP = "tool_loop"
    #: User and assistant messages interleaved with tool calls.
    CONVERSATION = "conversation"


class SeedSimulatedToolHistory(Seed[Literal["text"]]):
    """
    Configuration for writing a fabricated tool-call history before each attack.

    When an attack is prepared from a seed group containing this seed, an adversarial chat writes a
    history of tool calls and their outputs for the objective, against the tools the objective target
    reports. A deterministic check rejects unknown tools, invalid arguments, and unanswered calls, and
    the adversarial chat revises the history until it passes. The history is then prepended to the
    conversation. Generation happens in the executor layer; this seed only holds the settings.

    The ``value`` is a JSON serialization of the configuration, for storage and deduplication.
    """

    # Discriminator field for the polymorphic Seed union (see seed_group.SeedUnion).
    seed_type: Literal["simulated_tool_history"] = "simulated_tool_history"
    data_type: Literal["text"] = "text"
    # Always recomputed from the configuration.
    value: str = ""
    is_general_technique: bool = True

    sequence: int = 0
    shape: ToolHistoryShape = ToolHistoryShape.TOOL_LOOP
    #: Maximum number of history messages; the generated history occupies at most this many sequences.
    max_messages: int = 12
    #: Writing attempts, including revisions after validation feedback.
    max_attempts: int = 3
    #: Priming strategy for the adversarial chat; None uses the bundled prompt.
    adversarial_chat_system_prompt: SystemPrompt | None = None
    pyrit_version: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _strip_user_value(cls, data: Any) -> Any:
        if isinstance(data, dict) and "value" in data:
            data = dict(data)
            data.pop("value", None)
        return data

    @model_validator(mode="after")
    def _validate_and_compute_value(self) -> SeedSimulatedToolHistory:
        if self.sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if self.max_messages < 2:
            raise ValueError("max_messages must be at least 2, enough for one tool call and its output")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not self.pyrit_version:
            self.pyrit_version = importlib.metadata.version("pyrit")
        self.value = json.dumps(
            {
                "sequence": self.sequence,
                "shape": self.shape.value,
                "max_messages": self.max_messages,
                "max_attempts": self.max_attempts,
                "adversarial_chat_system_prompt": _prompt_identity(self.adversarial_chat_system_prompt),
                "pyrit_version": self.pyrit_version,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return self

    @property
    def sequence_range(self) -> range:
        """The sequence numbers a generated history may occupy."""
        return range(self.sequence, self.sequence + self.max_messages)
