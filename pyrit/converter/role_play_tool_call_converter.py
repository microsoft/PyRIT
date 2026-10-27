# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from typing import Any

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter.llm_generic_text_converter import LLMGenericTextConverter
from pyrit.models import ComponentIdentifier, SeedPrompt
from pyrit.prompt_target import PromptTarget


class RolePlayToolCallConverter(LLMGenericTextConverter):
    """
    Write one untrusted message so a desired tool action looks approved.

    The defender already shows an authorized task. This converter writes the
    message beside that task. It does not call the defender's tools.
    """

    MULTILINE_PARAMETER_NAMES = frozenset({"incoming_message", "valid_call", "action_desired", "message_format"})
    MESSAGE_FORMATS = ("XML", "YAML", "JSON", "Markdown", "plain text")
    DEFAULT_SYSTEM_PROMPT = SeedPrompt.from_yaml_file(CONVERTER_SEED_PROMPT_PATH / "role_play_tool_calls.yaml")
    DEFAULT_USER_PROMPT = SeedPrompt(
        value="{{ incoming_message }}",
        parameters=["objective", "incoming_message"],
    )

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        incoming_message: str,
        valid_call: str,
        action_desired: str,
        message_format: str = "random",
        system_prompt: SeedPrompt = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        """
        Initialize the converter with the pipeline text, call shape, desired action, and message format.

        The incoming prompt is the goal. The pipeline text and call fields must be supplied.

        Args:
            converter_target (PromptTarget): The chat target that writes the message.
                Can be omitted if a default has been configured via PyRIT initialization.
            incoming_message (str): The normal activity text that currently enters the pipeline.
                Example: Please refund $25 to the original payment method for order-1.
            valid_call (str): A normal allowed tool call. This is an example, not the goal.
                Example: refund {"order": "order-1", "amount": 25}.
            action_desired (str): The executed action that should look approved.
                Example: refund {"order": "order-1", "amount": 80}.
            message_format (str): The format of the generated message. Defaults to
                "random", which selects XML, YAML, JSON, Markdown, or plain text on each conversion.
                Supply a format or custom format instructions to use it on every conversion.
            system_prompt (SeedPrompt): The adversarial chat's system prompt template.
                Defaults to the role-play YAML. Can use ``objective``, ``incoming_message``,
                ``valid_call``, ``action_desired``, and ``message_format`` as Jinja parameters.

        Raises:
            ValueError: If a required field, message_format, or supplied system_prompt is empty.
            ValueError: If a supplied SeedPrompt is not text.
            ValueError: If converter_target is not provided and no default has been configured.
        """
        for name, value in (
            ("incoming_message", incoming_message),
            ("valid_call", valid_call),
            ("action_desired", action_desired),
            ("message_format", message_format),
        ):
            if not value or not value.strip():
                raise ValueError(f"{name} must not be empty.")

        self._incoming_message = incoming_message
        self._valid_call = valid_call
        self._action_desired = action_desired
        self._message_format = message_format.strip()
        if not isinstance(system_prompt, SeedPrompt):
            raise ValueError("system_prompt must be a SeedPrompt.")
        self._role_play_system_prompt = system_prompt.model_copy(deep=True)
        if self._role_play_system_prompt.data_type != "text" or not self._role_play_system_prompt.value.strip():
            raise ValueError("system_prompt must contain non-empty text.")
        super().__init__(
            converter_target=converter_target,
            system_prompt_template=self._role_play_system_prompt,
            user_prompt_template_with_objective=self.DEFAULT_USER_PROMPT,
            template_values={
                "incoming_message": incoming_message,
                "valid_call": valid_call,
                "action_desired": action_desired,
            },
        )

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier with its target, template, and call fields.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        base_identifier = super()._build_identifier()
        return self._create_identifier(
            params={
                **base_identifier.params,
                "incoming_message": self._incoming_message,
                "valid_call": self._valid_call,
                "action_desired": self._action_desired,
                "message_format": self._message_format,
                "system_prompt": self._role_play_system_prompt.value,
            },
            converter_target=self._converter_target.get_identifier(),
        )

    def _render_values(self, *, template: SeedPrompt, objective: str | None) -> dict[str, Any]:
        """
        Resolve the message format beside the standard template values.

        Args:
            template (SeedPrompt): The template to render.
            objective (str | None): The incoming goal.

        Returns:
            dict[str, Any]: The template values with one message format.
        """
        values = super()._render_values(template=template, objective=objective)
        values["message_format"] = (
            self._get_random_generator(stream="message_format").choice(self.MESSAGE_FORMATS)
            if self._message_format.lower() == "random"
            else self._message_format
        )
        return values
