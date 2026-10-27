# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Named prompt inputs for an attack's adversarial chat."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, field_serializer, model_validator

from pyrit.models.seeds.seed_prompt import SeedPrompt  # noqa: TC001 - Pydantic field type

if TYPE_CHECKING:
    from collections.abc import Mapping


class AdversarialPrompts(BaseModel):
    """Text prompts associated with a seed group, not its application conversation."""

    METADATA_KEY: ClassVar[str] = "adversarial_prompts"
    model_config = ConfigDict(extra="forbid", frozen=True)

    system_prompt: SeedPrompt | None = None
    first_message: SeedPrompt | None = None
    feedback_template: SeedPrompt | None = None

    @field_serializer("system_prompt", "first_message", "feedback_template")
    def _serialize_prompt(self, prompt: SeedPrompt | None) -> dict[str, Any] | None:
        if prompt is None:
            return None
        value = prompt.model_dump(mode="json")
        value["is_jinja_template"] = False
        return value

    @model_validator(mode="after")
    def _validate_prompts(self) -> Self:
        prompts = (self.system_prompt, self.first_message, self.feedback_template)
        if all(prompt is None for prompt in prompts):
            raise ValueError("AdversarialPrompts requires at least one prompt.")
        if any(prompt is not None and (prompt.data_type != "text" or not prompt.value.strip()) for prompt in prompts):
            raise ValueError("Adversarial prompts must contain non-empty text.")
        if self.feedback_template is not None and self.feedback_template.response_json_schema is not None:
            raise ValueError("Declare the adversarial response schema on the system prompt or first message.")
        if (
            self.system_prompt is not None
            and self.first_message is not None
            and (
                self.system_prompt.response_json_schema is not None
                and self.first_message.response_json_schema is not None
            )
        ):
            raise ValueError("Declare the adversarial response schema on only one prompt.")
        return self

    @property
    def identity(self) -> dict[str, Any]:
        """The named prompt values, parameters, and response contracts that affect execution."""
        return {
            name: {
                "value": prompt.value,
                "data_type": prompt.data_type,
                "parameters": list(prompt.parameters or []),
                "response_json_schema": prompt.response_json_schema,
            }
            for name, prompt in (
                ("system_prompt", self.system_prompt),
                ("first_message", self.first_message),
                ("feedback_template", self.feedback_template),
            )
            if prompt is not None
        }

    def to_metadata(self) -> dict[str, Any]:
        """
        Serialize the group association on its objective seed.

        Returns:
            dict[str, Any]: Canonical prompt data, with no load-time template rendering.
        """
        return {self.METADATA_KEY: self.model_dump(mode="json", exclude_none=True)}

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> AdversarialPrompts | None:
        """
        Read the association without changing seed metadata.

        Returns:
            AdversarialPrompts | None: The saved prompts, or None when no association exists.
        """
        if metadata is None or cls.METADATA_KEY not in metadata:
            return None
        return cls.model_validate(metadata[cls.METADATA_KEY])
