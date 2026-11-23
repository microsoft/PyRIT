# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Utility functions for generating simulated conversations using adversarial chat.

These utilities help create prepended_conversation content by running an adversarial chat
against a simulated (compliant) target before executing the actual attack.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from pyrit.common.path import EXECUTOR_SIMULATED_TARGET_PATH
from pyrit.exceptions import AdversarialChatResponseBlockedException, InvalidJsonException, remove_markdown_json
from pyrit.executor.attack.component import (
    _AdversarialConversationManager,
)
from pyrit.executor.attack.core.attack_config import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackScoringConfig,
)
from pyrit.executor.attack.core.attack_preparation import (
    AttackPreparationFailure,
    AttackPreparationFailureKind,
)
from pyrit.executor.attack.multi_turn.red_teaming import RedTeamingAttack
from pyrit.memory import CentralMemory
from pyrit.message_normalizer import ConversationContextNormalizer
from pyrit.models import (
    ConversationReference,
    ConversationType,
    JsonResponseConfig,
    Message,
    SeedGroup,
    SeedPrompt,
    SimulatedConversationMode,
    ToolDefinition,
    load_next_message_prompt,
    load_simulated_target_prompt,
    warn_prompt_path_deprecated,
)
from pyrit.prompt_normalizer import PromptNormalizer, send_json_with_retry_async
from pyrit.prompt_target import OpenAIResponseTarget, Tool
from pyrit.prompt_target.common.tool_call_history import check_simulated_tool_history

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from pyrit.prompt_target import PromptTarget
    from pyrit.score import TrueFalseScorer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimulatedConversationResult:
    """Generated prompts and the source conversations that produced them."""

    seed_prompts: list[SeedPrompt]
    related_conversations: frozenset[ConversationReference]
    preparation_failure: AttackPreparationFailure | None = None


async def _resolve_prompt_source_async(
    *,
    prompt: SeedPrompt | None,
    path: str | Path | None,
    prompt_name: str,
    path_name: str,
    load_prompt: Callable[[str | Path], SeedPrompt],
) -> SeedPrompt | None:
    """
    Resolve a prompt source, loading a deprecated path input off the event loop.

    The conflict check and the deprecation warning stay on the event loop so the warning points
    at the caller; only the file read is sent to a worker thread.

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
    return await asyncio.to_thread(load_prompt, path)


async def generate_simulated_conversation_async(
    *,
    objective: str,
    adversarial_chat: PromptTarget,
    objective_scorer: TrueFalseScorer | None,
    num_turns: int = 3,
    starting_sequence: int = 0,
    adversarial_chat_system_prompt: SeedPrompt | None = None,
    simulated_target_system_prompt: SeedPrompt | None = None,
    next_message_system_prompt: SeedPrompt | None = None,
    adversarial_chat_system_prompt_path: str | Path | None = None,
    simulated_target_system_prompt_path: str | Path | None = None,
    next_message_system_prompt_path: str | Path | None = None,
    attack_converter_config: AttackConverterConfig | None = None,
    memory_labels: dict[str, str] | None = None,
    mode: SimulatedConversationMode = SimulatedConversationMode.TURN_BY_TURN,
    tools: Sequence[ToolDefinition] | None = None,
    simulated_target: PromptTarget | None = None,
    max_attempts: int = 3,
) -> SimulatedConversationResult:
    """
    Generate a simulated conversation between an adversarial chat and a target.

    In ``TURN_BY_TURN`` mode this runs a RedTeamingAttack with `score_last_turn_only=True` against
    a simulated target (by default the same LLM as adversarial_chat, optionally configured with a
    system prompt). When ``tools`` are given, the simulated target can call them and the
    adversarial chat writes each result. In ``ONE_SHOT`` mode the adversarial chat instead writes
    a whole tool-call history against ``tools`` in one pass. A deterministic check lists every
    invalid call, argument, output, and shape problem, and the adversarial chat revises the
    history in the same conversation until it passes or the attempts run out.
    The resulting prompts and their source conversation references are returned together so
    downstream attacks can preserve the simulation lineage.

    Use cases:
    - Creating role-play scenarios dynamically (e.g., movie script, video game)
    - Establishing conversational context before attacking a real target
    - Generating multi-turn jailbreak setups without hardcoded responses

    Args:
        objective: The objective for the adversarial chat to work toward.
        adversarial_chat: The adversarial LLM that generates attack prompts.
            This same LLM is also used as the simulated target.
        objective_scorer: Scorer to evaluate the final turn. Required for ``TURN_BY_TURN``.
        num_turns: Number of conversation turns to generate. In ``ONE_SHOT`` mode, the exact number
            of tool calls in the history, each followed by its result. Defaults to 3.
        starting_sequence: The starting sequence number for the generated SeedPrompts.
            Each message gets an incrementing sequence number. Defaults to 0.
        adversarial_chat_system_prompt: System prompt for the adversarial chat. Required unless
            the deprecated path input is used.
        simulated_target_system_prompt: System prompt for the simulated target.
            If None, no system prompt is used for the simulated target.
        next_message_system_prompt: Optional system prompt for generating a final user message.
            If provided, after the simulated conversation, a single LLM call generates a user
            message that attempts to get the target to fulfill the objective in their next
            response. The prompt template receives `objective` and `conversation_context`
            parameters.
        adversarial_chat_system_prompt_path: Deprecated. Path to the adversarial chat system
            prompt YAML. Use ``adversarial_chat_system_prompt`` instead.
        simulated_target_system_prompt_path: Deprecated. Path to the simulated target system
            prompt YAML. Use ``simulated_target_system_prompt`` instead.
        next_message_system_prompt_path: Deprecated. Path to the next message system prompt
            YAML. Use ``next_message_system_prompt`` instead.
        attack_converter_config: Converter configuration for the attack. Defaults to None.
        memory_labels: Labels to associate with the conversation in memory. Defaults to None.
        mode: How the conversation is generated. Defaults to ``TURN_BY_TURN``.
        tools: Function tools the conversation uses, usually those the objective target reports.
            Required for ``ONE_SHOT``.
        simulated_target: The target that plays the assistant side in ``TURN_BY_TURN`` mode.
            Defaults to ``adversarial_chat``. It can carry its own tools (real, stubbed, or from an
            MCP server); their calls and results are kept in the generated history. Cannot be
            combined with ``tools``.
        max_attempts: ``ONE_SHOT`` only. Writing attempts, including revisions after validation
            feedback. Defaults to 3.

    Returns:
        The generated prompts and their source conversation references. Prompt sequence numbers
        start from ``starting_sequence`` and increment by 1 for each message.

    Raises:
        ValueError: If num_turns is not a positive integer, if no adversarial chat system prompt
            is supplied, if a prompt and its deprecated path input are both supplied, if
            ``ONE_SHOT`` mode has no tools or duplicate tool names, if both ``tools`` and
            ``simulated_target`` are given, if an adversarial chat cannot
            hold a multi-turn conversation with a system prompt, if ``TURN_BY_TURN`` mode has no
            objective scorer, or if a simulated target with tools cannot run them.
    """
    if simulated_target is not None and tools:
        raise ValueError(
            "Pass either tools, which give the adversarial chat's stand-in tools whose results it writes, or a "
            "simulated_target configured with its own tools, not both."
        )
    # By default the same LLM plays both roles, with different system prompts.
    simulated_target = simulated_target or adversarial_chat
    if num_turns <= 0:
        raise ValueError("num_turns must be a positive integer")

    adversarial_chat_system_prompt = await _resolve_prompt_source_async(
        prompt=adversarial_chat_system_prompt,
        path=adversarial_chat_system_prompt_path,
        prompt_name="adversarial_chat_system_prompt",
        path_name="adversarial_chat_system_prompt_path",
        load_prompt=SeedPrompt.from_yaml_file,
    )
    simulated_target_system_prompt = await _resolve_prompt_source_async(
        prompt=simulated_target_system_prompt,
        path=simulated_target_system_prompt_path,
        prompt_name="simulated_target_system_prompt",
        path_name="simulated_target_system_prompt_path",
        load_prompt=load_simulated_target_prompt,
    )
    next_message_system_prompt = await _resolve_prompt_source_async(
        prompt=next_message_system_prompt,
        path=next_message_system_prompt_path,
        prompt_name="next_message_system_prompt",
        path_name="next_message_system_prompt_path",
        load_prompt=load_next_message_prompt,
    )
    if adversarial_chat_system_prompt is None:
        raise ValueError("adversarial_chat_system_prompt is required")

    if mode is SimulatedConversationMode.ONE_SHOT:
        if not tools:
            raise ValueError("One-shot simulated conversations need the tools to write calls to")
        if len({tool.name for tool in tools}) != len(tools):
            raise ValueError("Tool names must be unique")
        return await _generate_one_shot_history_async(
            objective=objective,
            adversarial_chat=adversarial_chat,
            system_prompt=adversarial_chat_system_prompt,
            tools=tools,
            num_tool_calls=num_turns,
            max_attempts=max_attempts,
            starting_sequence=starting_sequence,
        )

    if objective_scorer is None:
        raise ValueError("objective_scorer is required for turn-by-turn simulated conversations")

    tool_result_writer: _ToolResultWriter | None = None
    if tools:
        tool_result_writer = _ToolResultWriter(objective=objective, adversarial_chat=adversarial_chat)
        simulated_target = _with_simulated_tools(target=simulated_target, tools=tools, writer=tool_result_writer)

    # Render the simulated target system prompt; None means the target gets no system prompt.
    simulated_target_system_prompt_value = (
        simulated_target_system_prompt.render_template_value(objective=objective, num_turns=num_turns)
        if simulated_target_system_prompt is not None
        else None
    )

    adversarial_config = AttackAdversarialConfig(
        target=adversarial_chat,
        system_prompt=adversarial_chat_system_prompt,
    )

    # Create scoring config
    scoring_config = AttackScoringConfig(
        objective_scorer=objective_scorer,
        use_score_as_feedback=False,  # Don't need feedback for last-turn-only scoring
    )

    # Create the RedTeamingAttack with simulated target and score_last_turn_only
    attack = RedTeamingAttack(
        objective_target=simulated_target,
        attack_adversarial_config=adversarial_config,
        attack_converter_config=attack_converter_config,
        attack_scoring_config=scoring_config,
        max_turns=num_turns,
        score_last_turn_only=True,
    )

    # Execute the simulated attack
    logger.info(f"Generating {num_turns}-turn simulated conversation for objective: {objective[:50]}...")

    # Build prepended_conversation - only include system message if prompt is provided
    prepended_conversation: list[Message] = []
    if simulated_target_system_prompt_value:
        prepended_conversation.append(Message.from_system_prompt(simulated_target_system_prompt_value))

    result = await attack.execute_async(
        objective=objective,
        prepended_conversation=prepended_conversation if prepended_conversation else None,
        memory_labels=memory_labels,
        persist_attack_result=False,
    )

    # Extract the conversation from memory and filter for prepended_conversation use
    memory = CentralMemory.get_memory_instance()
    raw_messages = list(await memory.get_conversation_messages_async(conversation_id=result.conversation_id))

    # Filter out system messages - keep the actual conversation
    # System prompts are set separately on each target during attack execution
    conversation_messages: list[Message] = [msg for msg in raw_messages if msg.api_role != "system"]
    related_conversations = {
        ConversationReference(
            conversation_id=result.conversation_id,
            conversation_type=ConversationType.PREPARATION,
            description="simulated preparation conversation",
        ),
        *result.related_conversations,
    }

    if tool_result_writer is not None:
        related_conversations.update(
            ConversationReference(
                conversation_id=conversation_id,
                conversation_type=ConversationType.ADVERSARIAL,
                description="simulated tool result",
            )
            for conversation_id in tool_result_writer.conversation_ids
        )

    preparation_failure = AttackPreparationFailure.from_result(result=result)
    # A stand-in whose tool loop stopped early (no rounds left, an invalid call, a tool error) leaves
    # an error piece, which the objective target would reject as history.
    error = next(
        (
            piece
            for message in conversation_messages
            for piece in message.message_pieces
            if piece.converted_value_data_type == "error" or piece.response_error != "none"
        ),
        None,
    )
    if preparation_failure is None and error is not None:
        kind = AttackPreparationFailureKind.SIMULATED_TARGET_ERROR
        preparation_failure = AttackPreparationFailure(
            kind=kind, reason=f"{kind.default_reason} Details: {error.converted_value[:500]}"
        )
        conversation_messages = []

    # If a next-message prompt is configured, generate a final user message
    if next_message_system_prompt and preparation_failure is None:
        next_message_conversation_id = str(uuid4())
        try:
            next_message = await _generate_next_message_async(
                objective=objective,
                conversation_messages=conversation_messages,
                adversarial_chat=adversarial_chat,
                conversation_id=next_message_conversation_id,
                next_message_system_prompt=next_message_system_prompt,
                prompt_normalizer=PromptNormalizer(),
                memory_labels=memory_labels,
            )
            conversation_messages.append(next_message)
        except AdversarialChatResponseBlockedException as blocked:
            kind = AttackPreparationFailureKind.from_exception(blocked)
            preparation_failure = AttackPreparationFailure(
                kind=kind,
                reason=f"{kind.default_reason} Details: {blocked}",
            )
        finally:
            related_conversations.add(
                ConversationReference(
                    conversation_id=next_message_conversation_id,
                    conversation_type=ConversationType.ADVERSARIAL,
                    description="simulated next-message generation",
                )
            )

    # Convert to SeedPrompts for the return value
    seed_prompts = SeedPrompt.from_messages(conversation_messages, starting_sequence=starting_sequence)

    logger.info(
        f"Generated simulated conversation with {len(seed_prompts)} SeedPrompts "
        f"(starting_sequence={starting_sequence}, outcome: {result.outcome.name})"
    )

    return SimulatedConversationResult(
        seed_prompts=seed_prompts,
        related_conversations=frozenset(related_conversations),
        preparation_failure=preparation_failure,
    )


async def _generate_next_message_async(
    *,
    objective: str,
    conversation_messages: list[Message],
    adversarial_chat: PromptTarget,
    conversation_id: str,
    next_message_system_prompt: SeedPrompt,
    prompt_normalizer: PromptNormalizer,
    memory_labels: dict[str, str] | None = None,
) -> Message:
    """
    Generate a single next message using the adversarial chat LLM.

    The next-message system prompt and the shared ``adversarial_chat`` JSON schema are a matched
    pair, so the whole contract — schema resolution, system-prompt setting, the send, JSON parsing,
    and retry — is delegated to the ``_AdversarialConversationManager``. This function renders the
    system prompt with the conversation so far, asks the adversarial chat for one reply, and returns
    the parsed ``next_message`` as a fresh user message ready to send to the target.

    Args:
        objective: The objective to work toward.
        conversation_messages: The conversation generated so far as Messages.
        adversarial_chat: The LLM to use for generation.
        conversation_id: The conversation ID for the adversarial generation exchange.
        next_message_system_prompt: The next-message system-prompt SeedPrompt, rendered with the
            conversation context.
        prompt_normalizer: The normalizer the manager sends the adversarial turn through.
        memory_labels: Optional memory labels to attach to the request.

    Returns:
        The generated next message, as a user message.

    Raises:
        ValueError: If no response is received from the adversarial chat.
        InvalidJsonException: If the reply cannot be parsed against the adversarial_chat schema.
    """
    # Format the conversation context using ConversationContextNormalizer
    normalizer = ConversationContextNormalizer()
    conversation_context = await normalizer.normalize_string_async(conversation_messages)

    # The manager owns schema resolution, setting the system prompt, the send, and JSON parse/retry.
    manager = _AdversarialConversationManager(
        adversarial_target=adversarial_chat,
        adversarial_system_prompt=next_message_system_prompt,
        prompt_normalizer=prompt_normalizer,
        conversation_id=conversation_id,
        objective=objective,
        attack_strategy_name="SimulatedConversation",
        memory_labels=memory_labels,
    )
    (await manager.set_adversarial_system_prompt_async(conversation_context=conversation_context))
    reply = await manager.generate_adversarial_reply_async(
        prompt_text="Generate the next user message based on the instructions above.",
    )
    return Message.from_prompt(role="user", prompt=reply.next_message)


class _InvalidHistoryError(ValueError):
    """The adversarial chat did not write a valid one-shot history within the allowed attempts."""


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


async def _generate_one_shot_history_async(
    *,
    objective: str,
    adversarial_chat: PromptTarget,
    system_prompt: SeedPrompt,
    tools: Sequence[ToolDefinition],
    num_tool_calls: int,
    max_attempts: int,
    starting_sequence: int,
) -> SimulatedConversationResult:
    """
    Have the adversarial chat write a whole tool-call history in one pass.

    Returns:
        SimulatedConversationResult: The history, or a preparation failure when the adversarial
            chat produced no usable history.

    Raises:
        ValueError: If the adversarial chat cannot hold a multi-turn conversation with a system
            prompt, or ``max_attempts`` is less than 1.
    """
    RedTeamingAttack.ADVERSARIAL_TARGET_REQUIREMENTS.validate(target=adversarial_chat)
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    conversation_id = str(uuid4())
    reference = ConversationReference(
        conversation_id=conversation_id,
        conversation_type=ConversationType.PREPARATION,
        description="simulated tool history generation",
    )
    try:
        history = await _write_one_shot_history_async(
            objective=objective,
            adversarial_chat=adversarial_chat,
            system_prompt=system_prompt,
            tools=tools,
            num_tool_calls=num_tool_calls,
            max_attempts=max_attempts,
            conversation_id=conversation_id,
        )
    except (InvalidJsonException, _InvalidHistoryError) as error:
        kind = AttackPreparationFailureKind.ADVERSARIAL_CHAT_INVALID_OUTPUT
        return SimulatedConversationResult(
            seed_prompts=[],
            related_conversations=frozenset({reference}),
            preparation_failure=AttackPreparationFailure(kind=kind, reason=f"{kind.default_reason} Details: {error}"),
        )
    history = [
        prompt.model_copy(update={"sequence": starting_sequence + index}) for index, prompt in enumerate(history)
    ]
    return SimulatedConversationResult(seed_prompts=history, related_conversations=frozenset({reference}))


async def _write_one_shot_history_async(
    *,
    objective: str,
    adversarial_chat: PromptTarget,
    system_prompt: SeedPrompt,
    tools: Sequence[ToolDefinition],
    num_tool_calls: int,
    max_attempts: int,
    conversation_id: str,
) -> list[SeedPrompt]:
    """
    Request a history and send validation feedback in the same conversation until it passes.

    Responses that are not JSON at all are retried on a clean turn through the shared JSON retry
    policy instead of spending an attempt.

    Returns:
        list[SeedPrompt]: The validated history, numbered from sequence 0.

    Raises:
        _InvalidHistoryError: If no attempt produced a valid history.
    """
    json_config = JsonResponseConfig(
        json_schema=system_prompt.response_json_schema, schema_name="SimulatedToolHistory", strict=False
    )

    def build_request(text: str) -> Message:
        message = Message.from_prompt(prompt=text, role="user")
        message.message_pieces[0].prompt_metadata = json_config.to_metadata()
        return message

    def parse(response: Message) -> tuple[list[SeedPrompt], list[str]]:
        return _read_one_shot_history(response=response, tools=tools, num_tool_calls=num_tool_calls)

    await adversarial_chat.set_system_prompt_async(system_prompt=system_prompt.value, conversation_id=conversation_id)
    normalizer = PromptNormalizer()
    request = build_request(
        json.dumps(
            {
                "objective": objective,
                "tools": [tool.model_dump(mode="json") for tool in tools],
                "num_tool_calls": num_tool_calls,
            },
            indent=2,
        )
    )
    issues: list[str] = []
    for attempt in range(1, max_attempts + 1):
        history, issues = await send_json_with_retry_async(
            normalizer=normalizer,
            target=adversarial_chat,
            message=request,
            conversation_id=conversation_id,
            parse=parse,
        )
        if not issues:
            return history
        logger.info("One-shot tool history attempt %d failed validation: %s", attempt, issues)
        request = build_request(
            "Your history failed validation. Fix every problem below and return the complete corrected "
            "history in the same JSON format. Message N refers to items[N].\n"
            + "\n".join(f"- {issue}" for issue in issues)
        )
    raise _InvalidHistoryError(
        f"No valid history after {max_attempts} attempt(s) in conversation {conversation_id}. "
        f"Remaining problems: {'; '.join(issues)}"
    )


def _read_one_shot_history(
    *, response: Message, tools: Sequence[ToolDefinition], num_tool_calls: int
) -> tuple[list[SeedPrompt], list[str]]:
    """
    Parse and check one response.

    Returns:
        tuple[list[SeedPrompt], list[str]]: The history and the problems to send back for revision.

    Raises:
        InvalidJsonException: If the response is blocked or is not a single JSON object, so the
            shared JSON retry resends the request instead of spending an attempt.
    """
    texts = [piece.converted_value for piece in response.message_pieces if piece.converted_value_data_type == "text"]
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
        details = "; ".join(
            f"{'.'.join(map(str, detail['loc'])) or 'value'}: {detail['msg']}" for detail in error.errors()
        )
        return [], [f"The response is not a valid history object: {details}"]
    history = [item.to_seed_prompt(sequence=index) for index, item in enumerate(authored.items)]
    messages = SeedGroup(seeds=[prompt.model_copy() for prompt in history]).user_messages
    return history, [
        *_check_one_shot_shape(history=history, num_tool_calls=num_tool_calls),
        *check_simulated_tool_history(messages=messages, tools=tools),
    ]


def _check_one_shot_shape(*, history: list[SeedPrompt], num_tool_calls: int) -> list[str]:
    issues: list[str] = []
    calls = sum(prompt.data_type == "function_call" for prompt in history)
    if calls != num_tool_calls:
        issues.append(
            f"The history has {calls} tool calls; write exactly {num_tool_calls}, each followed by its result."
        )
    if history[-1].role == "user":
        issues.append("Do not end with a user message; the objective is sent as the next user message.")
    texts = [(index, prompt.role) for index, prompt in enumerate(history) if prompt.data_type == "text"]
    if any(role == "assistant" for _, role in texts):
        issues.append("Write no assistant messages; use only tool calls and their results.")
    if any(role == "user" and index != 0 for index, role in texts):
        issues.append("Only one user message is allowed, as the first item.")
    return issues


@dataclass
class _ToolResultWriter:
    """Has the adversarial chat write the result of each tool call a simulated target makes."""

    objective: str
    adversarial_chat: PromptTarget
    conversation_ids: list[str] = field(default_factory=list)

    async def write_async(self, *, tool: ToolDefinition, arguments: dict[str, object]) -> str:
        conversation_id = str(uuid4())
        self.conversation_ids.append(conversation_id)
        system_prompt = SeedPrompt.from_yaml_file(EXECUTOR_SIMULATED_TARGET_PATH / "simulated_tool_result.yaml")
        await self.adversarial_chat.set_system_prompt_async(
            system_prompt=system_prompt.render_template_value(objective=self.objective),
            conversation_id=conversation_id,
        )
        request = json.dumps({"tool": tool.model_dump(mode="json"), "arguments": arguments}, indent=2)
        response = await PromptNormalizer().send_prompt_async(
            message=Message.from_prompt(prompt=request, role="user"),
            target=self.adversarial_chat,
            conversation_id=conversation_id,
        )
        return response.get_value() if response else ""


class _SimulatedTool(Tool):
    """A tool whose results are written by the adversarial chat instead of executed."""

    def __init__(self, *, definition: ToolDefinition, writer: _ToolResultWriter) -> None:
        super().__init__(
            name=definition.name, description=definition.description, parameters=dict(definition.parameters)
        )
        self._definition = definition
        self._writer = writer

    async def execute_async(self, *, arguments: dict[str, object]) -> object:
        return await self._writer.write_async(tool=self._definition, arguments=arguments)


def _with_simulated_tools(
    *, target: PromptTarget, tools: Sequence[ToolDefinition], writer: _ToolResultWriter
) -> PromptTarget:
    """
    Copy the simulated target with tools whose results the adversarial chat writes.

    Returns:
        PromptTarget: An independent copy of ``target`` that offers ``tools``.

    Raises:
        ValueError: If the target cannot run tool calls.
    """
    # Import here to avoid circular imports
    from pyrit.registry import TargetRegistry

    if not isinstance(target, OpenAIResponseTarget):
        raise ValueError(
            "A simulated conversation with tools needs an OpenAIResponseTarget simulated target, which runs "
            f"the tool calls; got {type(target).__name__}."
        )
    return TargetRegistry.get_registry_singleton().recreate_instance(
        source=target, params={"tools": [_SimulatedTool(definition=tool, writer=writer) for tool in tools]}
    )
