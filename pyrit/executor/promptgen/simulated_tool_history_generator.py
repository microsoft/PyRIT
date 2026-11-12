# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Author fabricated tool-call histories that prime a target toward an objective."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from pyrit.common.path import EXECUTOR_SEED_PROMPT_PATH
from pyrit.exceptions import InvalidJsonException, remove_markdown_json
from pyrit.executor.promptgen.core import (
    PromptGeneratorStrategy,
    PromptGeneratorStrategyContext,
    PromptGeneratorStrategyResult,
)
from pyrit.models import (
    JsonResponseConfig,
    Message,
    SeedGroup,
    SeedObjective,
    SeedPrompt,
    ToolDefinition,
    ToolHistoryShape,
)
from pyrit.prompt_normalizer import PromptNormalizer, send_json_with_retry_async
from pyrit.prompt_target import CapabilityName, TargetRequirements
from pyrit.prompt_target.common.tool_call_history import check_simulated_tool_history

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyrit.prompt_target import PromptTarget


class _AuthoredMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["message"]
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

    def to_seed_prompt(self, *, sequence: int) -> SeedPrompt:
        return SeedPrompt(value=self.content, data_type="text", role=self.role, sequence=sequence)


class _AuthoredCall(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["function_call"]
    call_id: str
    name: str
    arguments: dict[str, JsonValue] | str

    def to_seed_prompt(self, *, sequence: int) -> SeedPrompt:
        arguments = self.arguments if isinstance(self.arguments, str) else json.dumps(self.arguments)
        value = {"type": "function_call", "call_id": self.call_id, "name": self.name, "arguments": arguments}
        return SeedPrompt(value=json.dumps(value), data_type="function_call", role="assistant", sequence=sequence)


class _AuthoredOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: Literal["function_call_output"]
    call_id: str
    output: JsonValue

    def to_seed_prompt(self, *, sequence: int) -> SeedPrompt:
        output = self.output if isinstance(self.output, str) else json.dumps(self.output)
        value = {"type": "function_call_output", "call_id": self.call_id, "output": output}
        return SeedPrompt(value=json.dumps(value), data_type="function_call_output", role="tool", sequence=sequence)


class _AuthoredHistory(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: list[Annotated[_AuthoredMessage | _AuthoredCall | _AuthoredOutput, Field(discriminator="type")]] = Field(
        min_length=1
    )


@dataclass
class SimulatedToolHistoryGeneratorContext(PromptGeneratorStrategyContext):
    """Single-use inputs for authoring one simulated tool history."""

    objective: SeedObjective
    tools: Sequence[ToolDefinition]
    shape: ToolHistoryShape = ToolHistoryShape.TOOL_LOOP
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _used: bool = field(default=False, init=False, repr=False)


class SimulatedToolHistoryGeneratorResult(PromptGeneratorStrategyResult):
    """A validated history and the adversarial conversation that authored it."""

    #: The simulated history, numbered from sequence 0, to send before the objective.
    history: list[SeedPrompt]
    conversation_id: str
    attempts: int


class SimulatedToolHistoryGenerator(
    PromptGeneratorStrategy[SimulatedToolHistoryGeneratorContext, SimulatedToolHistoryGeneratorResult]
):
    """
    Author a fabricated tool-call history that primes a target toward an objective.

    The adversarial chat writes the history against the target's tool definitions. A deterministic
    check lists every invalid call, argument, output, and shape problem, and the adversarial chat
    revises the history in the same conversation until it passes or the attempts run out.
    Responses that are not JSON at all are retried on a clean turn through the shared JSON
    retry policy instead of spending a revision. No tool executes and the objective target is
    never contacted.
    """

    DEFAULT_SYSTEM_PROMPT_PATH = EXECUTOR_SEED_PROMPT_PATH / "promptgen" / "simulated_tool_history.yaml"
    #: The adversarial chat gets a system prompt and revises its history in the same conversation.
    ADVERSARIAL_TARGET_REQUIREMENTS: ClassVar[TargetRequirements] = TargetRequirements(
        native_required=frozenset({CapabilityName.MULTI_TURN, CapabilityName.SYSTEM_PROMPT}),
    )
    _CLEANUP_TIMEOUT_SECONDS = 5.0

    def __init__(
        self,
        *,
        adversarial_chat: PromptTarget,
        system_prompt: SeedPrompt | None = None,
        max_attempts: int = 3,
        max_messages: int = 12,
    ) -> None:
        """
        Configure the adversarial chat and the generation budget.

        Args:
            adversarial_chat (PromptTarget): Writes the history. It must support JSON output.
            system_prompt (SeedPrompt | None): The priming strategy given to the adversarial chat.
                Defaults to the bundled prompt, whose response schema is used when this one has none.
            max_attempts (int): Authoring attempts, including revisions after validation feedback.
                Defaults to 3.
            max_messages (int): Maximum number of history messages. Defaults to 12.

        Raises:
            ValueError: If the adversarial chat cannot hold a multi-turn conversation with a system
                prompt, the system prompt is empty, or a budget is too small.
        """
        type(self).ADVERSARIAL_TARGET_REQUIREMENTS.validate(target=adversarial_chat)
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        if max_messages < 2:
            raise ValueError("max_messages must be at least 2, enough for one tool call and its output.")
        default_prompt = SeedPrompt.from_yaml_file(self.DEFAULT_SYSTEM_PROMPT_PATH)
        resolved_prompt = system_prompt or default_prompt
        if resolved_prompt.data_type != "text" or not resolved_prompt.value.strip():
            raise ValueError("system_prompt must contain non-empty text.")
        super().__init__(context_type=SimulatedToolHistoryGeneratorContext)
        self._adversarial_chat = adversarial_chat
        self._system_prompt = resolved_prompt
        self._json_response_config = JsonResponseConfig(
            json_schema=resolved_prompt.response_json_schema or default_prompt.response_json_schema,
            schema_name="SimulatedToolHistory",
            strict=False,
        )
        self._max_attempts = max_attempts
        self._max_messages = max_messages
        self._normalizer = PromptNormalizer()

    async def execute_with_context_async(
        self, *, context: SimulatedToolHistoryGeneratorContext
    ) -> SimulatedToolHistoryGeneratorResult:
        """
        Generate one history through the standard strategy lifecycle.

        Returns:
            SimulatedToolHistoryGeneratorResult: The validated case.

        Raises:
            ValueError: If the context has already been used.
        """
        if context._used:
            raise ValueError("SimulatedToolHistoryGeneratorContext is single-use; create a new context.")
        context._used = True
        return await super().execute_with_context_async(context=context)

    def _validate_context(self, *, context: SimulatedToolHistoryGeneratorContext) -> None:
        if not context.objective.value.strip():
            raise ValueError("The objective must not be empty.")
        if not context.tools:
            raise ValueError("At least one tool is required to author a tool history.")
        if len({tool.name for tool in context.tools}) != len(context.tools):
            raise ValueError("Tool names must be unique.")
        if not isinstance(context.shape, ToolHistoryShape):
            raise ValueError("shape must be a ToolHistoryShape.")

    async def _setup_async(self, *, context: SimulatedToolHistoryGeneratorContext) -> None:
        await self._adversarial_chat.set_system_prompt_async(
            system_prompt=self._system_prompt.value, conversation_id=context.conversation_id
        )

    async def _perform_async(
        self, *, context: SimulatedToolHistoryGeneratorContext
    ) -> SimulatedToolHistoryGeneratorResult:
        request = self._build_request(context=context)
        issues: list[str] = []
        for attempt in range(1, self._max_attempts + 1):

            def parse(response: Message) -> tuple[list[SeedPrompt], list[str]]:
                return self._read_history(response=response, context=context)

            history, issues = await send_json_with_retry_async(
                normalizer=self._normalizer,
                target=self._adversarial_chat,
                message=request,
                conversation_id=context.conversation_id,
                parse=parse,
            )
            if not issues:
                return SimulatedToolHistoryGeneratorResult(
                    history=history, conversation_id=context.conversation_id, attempts=attempt
                )
            self._logger.info("Simulated tool history attempt %d failed validation: %s", attempt, issues)
            request = self._build_feedback(issues=issues)
        raise ValueError(
            f"No valid history after {self._max_attempts} attempt(s) in conversation {context.conversation_id}. "
            f"Remaining problems: {'; '.join(issues)}"
        )

    async def _teardown_async(self, *, context: SimulatedToolHistoryGeneratorContext) -> None:
        try:
            async with asyncio.timeout(self._CLEANUP_TIMEOUT_SECONDS):
                await self._adversarial_chat.reset_conversation_async(conversation_id=context.conversation_id)
        except Exception as error:  # noqa: BLE001 - cleanup must not replace the generation outcome
            self._logger.warning("Failed to reset generation conversation %s: %s", context.conversation_id, error)

    def _build_request(self, *, context: SimulatedToolHistoryGeneratorContext) -> Message:
        return self._build_message(
            json.dumps(
                {
                    "objective": context.objective.value,
                    "tools": [tool.model_dump(mode="json") for tool in context.tools],
                    "shape": context.shape.value,
                    "max_messages": self._max_messages,
                },
                indent=2,
            )
        )

    def _build_message(self, text: str) -> Message:
        message = Message.from_prompt(prompt=text, role="user")
        message.message_pieces[0].prompt_metadata = self._json_response_config.to_metadata()
        return message

    def _build_feedback(self, *, issues: list[str]) -> Message:
        return self._build_message(
            "Your history failed validation. Fix every problem below and return the complete corrected "
            "history in the same JSON format. Message N refers to items[N].\n"
            + "\n".join(f"- {issue}" for issue in issues)
        )

    def _read_history(
        self, *, response: Message, context: SimulatedToolHistoryGeneratorContext
    ) -> tuple[list[SeedPrompt], list[str]]:
        """
        Parse and check one response.

        Returns:
            tuple[list[SeedPrompt], list[str]]: The history and the problems to send back for revision.

        Raises:
            InvalidJsonException: If the response is blocked or is not a single JSON object, so the
                shared JSON retry resends the request instead of spending a revision.
        """
        texts = [
            piece.converted_value for piece in response.message_pieces if piece.converted_value_data_type == "text"
        ]
        blocked = any(piece.response_error != "none" for piece in response.message_pieces)
        if blocked or len(texts) != 1:
            raise InvalidJsonException(message="Expected one text response containing a history object.")
        payload = remove_markdown_json(texts[0])
        try:
            json.loads(payload)
        except json.JSONDecodeError as error:
            raise InvalidJsonException(message="The response is not a JSON object.") from error
        try:
            authored = _AuthoredHistory.model_validate_json(payload)
        except ValidationError as error:
            return [], [f"The response is not a valid history object: {_describe_validation_error(error)}"]
        history = [item.to_seed_prompt(sequence=index) for index, item in enumerate(authored.items)]
        messages = SeedGroup(seeds=[prompt.model_copy() for prompt in history]).user_messages
        return history, [
            *self._check_shape(history=history, shape=context.shape),
            *check_simulated_tool_history(messages=messages, tools=context.tools),
        ]

    def _check_shape(self, *, history: list[SeedPrompt], shape: ToolHistoryShape) -> list[str]:
        issues: list[str] = []
        if len(history) > self._max_messages:
            issues.append(f"The history has {len(history)} items; use at most {self._max_messages}.")
        if history[-1].role == "user":
            issues.append("Do not end with a user message; the objective is sent as the next user message.")
        texts = [(index, prompt.role) for index, prompt in enumerate(history) if prompt.data_type == "text"]
        if shape is ToolHistoryShape.TOOL_LOOP:
            if any(role == "assistant" for _, role in texts):
                issues.append('Shape "tool_loop" allows no assistant messages.')
            if any(role == "user" and index != 0 for index, role in texts):
                issues.append('Shape "tool_loop" allows only one user message, as the first item.')
        elif not any(role == "assistant" for _, role in texts):
            issues.append('Shape "conversation" needs at least one assistant message.')
        return issues


def _describe_validation_error(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, detail['loc'])) or 'value'}: {detail['msg']}" for detail in error.errors())
