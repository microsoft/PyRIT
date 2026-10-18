# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from __future__ import annotations

import asyncio
import json
import math
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.exceptions import InvalidJsonException
from pyrit.executor.promptgen.core import (
    PromptGeneratorStrategy,
    PromptGeneratorStrategyContext,
    PromptGeneratorStrategyResult,
)
from pyrit.models import JsonResponseConfig, JsonSchemaDefinition, Message, SeedPrompt
from pyrit.prompt_normalizer import PromptNormalizer, send_json_with_retry_async

if TYPE_CHECKING:
    from jsonschema.protocols import Validator

    from pyrit.prompt_target import PromptTarget


@dataclass
class TargetObjectiveGeneratorContext(PromptGeneratorStrategyContext):
    """Single-use inputs and conversation identity for one generation operation."""

    instructions: str
    count: int
    harm_categories: list[str] = field(default_factory=list)
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _used: bool = field(default=False, init=False, repr=False)
    _conversation_initialized: bool = field(default=False, init=False, repr=False)


class TargetObjectiveGeneratorResult(PromptGeneratorStrategyResult):
    """Validated objectives and their generation conversation ID."""

    objectives: list[str]
    conversation_id: str


class TargetObjectiveGenerator(
    PromptGeneratorStrategy[TargetObjectiveGeneratorContext, TargetObjectiveGeneratorResult]
):
    """Generate a complete batch of text objectives through the standard strategy execution entry point."""

    DEFAULT_SYSTEM_PROMPT_PATH = EXECUTOR_SEED_PROMPT_PATH / "promptgen" / "target_objective_generator.yaml"
    _CLEANUP_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        *,
        target: PromptTarget,
        system_prompt: SeedPrompt | None = None,
        timeout_seconds: float = 120,
    ) -> None:
        """
        Configure the generation target and execution deadline.

        Args:
            target: Target used to generate objectives.
            system_prompt: Text seed prompt; defaults to the bundled generation YAML.
                Its response_json_schema is used for both target output and local validation.
                If omitted on a custom prompt, the bundled schema is used. The schema must
                require an objectives array of strings. Trusted templates can use instructions,
                count, and harm_categories parameters.
            timeout_seconds: Positive finite deadline, including retry waits.
                Cleanup can take up to five additional seconds after cancellation.

        Raises:
            TypeError: If system_prompt is not a SeedPrompt.
            ValueError: If the timeout, system prompt, or response schema is invalid.
        """
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive and finite.")
        if system_prompt is not None and not isinstance(system_prompt, SeedPrompt):
            raise TypeError("system_prompt must be a SeedPrompt or None.")
        default_prompt = SeedPrompt.from_yaml_file(self.DEFAULT_SYSTEM_PROMPT_PATH)
        resolved_prompt = system_prompt.model_copy(deep=True) if system_prompt is not None else default_prompt
        if resolved_prompt.data_type != "text" or not resolved_prompt.value.strip():
            raise ValueError("system_prompt must contain non-empty text.")
        super().__init__(context_type=TargetObjectiveGeneratorContext)
        self._target = target
        self._system_prompt = resolved_prompt
        schema = resolved_prompt.response_json_schema
        schema = schema if schema is not None else default_prompt.response_json_schema
        self._response_validator = self._build_response_validator(schema)
        self._json_response_config = JsonResponseConfig(
            json_schema=schema,
            schema_name="ObjectiveBatch",
        )
        self._timeout_seconds = timeout_seconds
        self._normalizer = PromptNormalizer()

    async def execute_with_context_async(
        self, *, context: TargetObjectiveGeneratorContext
    ) -> TargetObjectiveGeneratorResult:
        """
        Execute once within the deadline, with bounded cleanup after cancellation.

        Returns:
            The complete validated batch and retained evidence references.

        Raises:
            ValueError: If this context has already been used, including a failed execution.
        """
        if context._used:
            raise ValueError("TargetObjectiveGeneratorContext is single-use; create a new context for each execution.")
        context._used = True
        async with asyncio.timeout(self._timeout_seconds):
            return await super().execute_with_context_async(context=context)

    def _validate_context(self, *, context: TargetObjectiveGeneratorContext) -> None:
        if not isinstance(context.instructions, str) or not context.instructions.strip():
            raise ValueError("instructions must be a non-empty string.")
        if isinstance(context.count, bool) or not isinstance(context.count, int) or context.count < 1:
            raise ValueError("count must be a positive integer.")
        if any(not category.strip() for category in context.harm_categories):
            raise ValueError("harm_categories must contain non-empty strings.")

    async def _setup_async(self, *, context: TargetObjectiveGeneratorContext) -> None:
        prompt = self._system_prompt.value
        if self._system_prompt.is_jinja_template:
            prompt = self._system_prompt.render_template_value(
                instructions=context.instructions, count=context.count, harm_categories=context.harm_categories
            )
        await self._target.set_system_prompt_async(system_prompt=prompt, conversation_id=context.conversation_id)
        context._conversation_initialized = True

    async def _perform_async(self, *, context: TargetObjectiveGeneratorContext) -> TargetObjectiveGeneratorResult:
        prompt = json.dumps(
            {
                "instructions": context.instructions,
                "count": context.count,
                "harm_categories": context.harm_categories,
            }
        )
        message = Message.from_prompt(prompt=prompt, role="user")
        message.message_pieces[0].prompt_metadata = self._json_response_config.to_metadata()

        def parse(response: Message) -> TargetObjectiveGeneratorResult:
            return self._parse_response(response=response, context=context)

        return await send_json_with_retry_async(
            normalizer=self._normalizer,
            target=self._target,
            message=message,
            conversation_id=context.conversation_id,
            parse=parse,
        )

    async def _teardown_async(self, *, context: TargetObjectiveGeneratorContext) -> None:
        if context._conversation_initialized:
            try:
                async with asyncio.timeout(self._CLEANUP_TIMEOUT_SECONDS):
                    await self._target.reset_conversation_async(conversation_id=context.conversation_id)
            except TimeoutError:
                self._logger.warning(
                    "Timed out resetting generation conversation %s after %s seconds.",
                    context.conversation_id,
                    self._CLEANUP_TIMEOUT_SECONDS,
                )
            except Exception as error:  # noqa: BLE001 - cleanup must not replace the generation outcome
                self._logger.warning("Failed to reset generation conversation %s: %s", context.conversation_id, error)

    def _parse_response(
        self, *, response: Message, context: TargetObjectiveGeneratorContext
    ) -> TargetObjectiveGeneratorResult:
        if len(response.message_pieces) != 1 or response.message_pieces[0].converted_value_data_type != "text":
            raise InvalidJsonException(message="Expected one text response containing an objectives object.")
        try:
            batch = json.loads(response.get_value())
            self._response_validator.validate(batch)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise InvalidJsonException(message="Response does not match the generation prompt's JSON schema.") from exc
        objectives = [value.strip() for value in batch["objectives"]]
        try:
            self._response_validator.validate({**batch, "objectives": objectives})
        except ValidationError as exc:
            raise InvalidJsonException(message="Trimmed objectives do not match the generation JSON schema.") from exc
        if (
            len(objectives) != context.count
            or any(not value for value in objectives)
            or len(set(objectives)) != context.count
        ):
            raise InvalidJsonException(
                message=f"Expected exactly {context.count} distinct, non-empty objectives after trimming."
            )
        return TargetObjectiveGeneratorResult(
            objectives=objectives,
            conversation_id=context.conversation_id,
        )

    @staticmethod
    def _build_response_validator(schema: JsonSchemaDefinition | None) -> Validator:
        if schema is None:
            raise ValueError("The generation prompt must define a response_json_schema.")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise ValueError("Invalid generation response_json_schema.") from exc
        objectives = schema.get("properties", {}).get("objectives", {})
        items = objectives.get("items", {}) if isinstance(objectives, dict) else {}
        if (
            schema.get("type") != "object"
            or "objectives" not in schema.get("required", [])
            or not isinstance(objectives, dict)
            or objectives.get("type") != "array"
            or not isinstance(items, dict)
            or items.get("type") != "string"
        ):
            raise ValueError("response_json_schema must require an objectives array with string items.")
        return Draft202012Validator(schema)
