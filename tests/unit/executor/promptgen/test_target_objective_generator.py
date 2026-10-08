# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from jsonschema import Draft202012Validator
from unit.mocks import MockPromptTarget

from pyrit.exceptions import InvalidJsonException
from pyrit.executor.promptgen import TargetObjectiveGenerator
from pyrit.executor.promptgen.target_objective_generator import TargetObjectiveGeneratorContext
from pyrit.memory import MemoryInterface
from pyrit.models import JsonResponseConfig, Message, MessagePiece, SeedPrompt


@contextmanager
def _isolate_pre_send_io(generator: TargetObjectiveGenerator) -> Iterator[AsyncMock]:
    """Mock setup and retry-history I/O so timeout tests reach their intended await."""
    with (
        patch.object(generator._target, "set_system_prompt_async", new_callable=AsyncMock) as setup,
        patch.object(generator._normalizer.memory, "get_message_pieces_async", new_callable=AsyncMock, return_value=[]),
        patch.object(
            generator._normalizer.memory,
            "delete_conversation_pieces_after_sequence_async",
            new_callable=AsyncMock,
            return_value=0,
        ),
    ):
        yield setup


@contextmanager
def _controlled_timeouts() -> Iterator[list[tuple[float | None, asyncio.Timeout]]]:
    """Expire real asyncio deadlines at the intended await, not during unrelated setup."""
    timeouts: list[tuple[float | None, asyncio.Timeout]] = []
    original_timeout = asyncio.timeout

    def capture_timeout(delay: float | None) -> asyncio.Timeout:
        timeout = original_timeout(None)
        timeouts.append((delay, timeout))
        return timeout

    with patch("pyrit.executor.promptgen.target_objective_generator.asyncio.timeout", new=capture_timeout):
        yield timeouts


@pytest.mark.usefixtures("patch_central_database")
class TestTargetObjectiveGenerator:
    async def test_valid_batch_and_evidence_async(self, sqlite_instance: MemoryInterface) -> None:
        target = MockPromptTarget()
        generator = TargetObjectiveGenerator(target=target, system_prompt=SeedPrompt(value="Generate test objectives."))

        async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
            request = normalized_conversation[-1].message_pieces[0]
            return [
                MessagePiece(
                    role="assistant",
                    original_value=json.dumps({"objectives": [f" Objective {index} " for index in range(10)]}),
                    conversation_id=request.conversation_id,
                ).to_message()
            ]

        with patch.object(target, "_send_prompt_to_target_async", side_effect=respond_async):
            result = await generator.execute_async(instructions="Test formatting.", count=10)
        assert result.objectives == [f"Objective {index}" for index in range(10)]
        stored = await sqlite_instance.get_message_pieces_async(conversation_id=result.conversation_id)
        responses = [piece for piece in stored if piece.role == "assistant"]
        assert len(responses) == 1
        assert [value.strip() for value in json.loads(responses[0].converted_value)["objectives"]] == result.objectives
        assert not await sqlite_instance.get_seeds_async()

    async def test_default_yaml_controls_prompt_and_schema_async(self) -> None:
        target = MockPromptTarget()
        generator = TargetObjectiveGenerator(target=target)
        prompt = generator._system_prompt
        assert prompt.response_json_schema is not None
        Draft202012Validator.check_schema(prompt.response_json_schema)
        response = Message.from_prompt(prompt='{"objectives": ["A goal"]}', role="assistant")

        with patch.object(
            generator._normalizer, "send_prompt_async", new_callable=AsyncMock, return_value=response
        ) as send:
            await generator.execute_async(instructions="Astronomy", count=1, harm_categories=["test"])

        assert target.system_prompt == prompt.value
        message = send.call_args.kwargs["message"]
        assert json.loads(message.get_value()) == {
            "instructions": "Astronomy",
            "count": 1,
            "harm_categories": ["test"],
        }
        config = JsonResponseConfig.from_metadata(metadata=message.message_pieces[0].prompt_metadata)
        assert config.json_schema == prompt.response_json_schema
        assert config.enabled
        Draft202012Validator(config.json_schema).validate({"objectives": ["A goal"]})

    async def test_custom_yaml_template_and_schema_async(self, tmp_path: Path) -> None:
        path = tmp_path / "generation.yaml"
        await asyncio.to_thread(
            path.write_text,
            """data_type: text
parameters: [instructions, count, harm_categories]
response_json_schema:
  type: object
  properties:
    objectives:
      type: array
      items:
        type: string
        minLength: 3
  required: [objectives]
  additionalProperties: false
value: |
  Generate {{ count }}: {{ instructions }}
  Categories: {{ harm_categories }}
""",
            encoding="utf-8",
        )
        prompt = await asyncio.to_thread(SeedPrompt.from_yaml_file, path)
        original = prompt.model_dump()
        target = MockPromptTarget()
        generator = TargetObjectiveGenerator(target=target, system_prompt=prompt)
        response = Message.from_prompt(prompt='{"objectives": ["A goal"]}', role="assistant")
        with patch.object(
            generator._normalizer, "send_prompt_async", new_callable=AsyncMock, return_value=response
        ) as send:
            await generator.execute_async(instructions="Keep {{ literal }}", count=1, harm_categories=["test"])

        assert target.system_prompt == "Generate 1: Keep {{ literal }}\nCategories: ['test']"
        config = JsonResponseConfig.from_metadata(
            metadata=send.call_args.kwargs["message"].message_pieces[0].prompt_metadata
        )
        assert config.json_schema == prompt.response_json_schema
        assert prompt.model_dump() == original

    async def test_literal_seed_uses_default_schema_without_rendering_async(self) -> None:
        target = MockPromptTarget()
        prompt = SeedPrompt(value="Keep {{ literal }}")
        generator = TargetObjectiveGenerator(target=target, system_prompt=prompt)
        response = Message.from_prompt(prompt='{"objectives": ["A goal"]}', role="assistant")
        with patch.object(
            generator._normalizer, "send_prompt_async", new_callable=AsyncMock, return_value=response
        ) as send:
            await generator.execute_async(instructions="Test", count=1)

        assert target.system_prompt == prompt.value
        config = JsonResponseConfig.from_metadata(
            metadata=send.call_args.kwargs["message"].message_pieces[0].prompt_metadata
        )
        assert config.json_schema is not None
        assert config.json_schema["required"] == ["objectives"]
        assert prompt.response_json_schema is None

    @pytest.mark.parametrize(
        "text",
        [
            "not json",
            "[]",
            '{"objectives": [1, 2]}',
            '{"objectives": ["a"]}',
            '{"objectives": ["a", "b", "c"]}',
            '{"objectives": ["a", " a "]}',
            '{"objectives": ["a", "  "]}',
            '{"objectives": ["a", "b"], "extra": true}',
        ],
    )
    async def test_invalid_batches_exhaust_retry_budget_async(self, text: str) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget())
        response = Message.from_prompt(prompt=text, role="assistant")
        with (
            patch.object(
                generator._normalizer, "send_prompt_async", new_callable=AsyncMock, return_value=response
            ) as send,
            pytest.raises(RuntimeError) as error,
        ):
            await generator.execute_async(instructions="Test", count=2)
        assert isinstance(error.value.__cause__, InvalidJsonException)
        assert send.call_count == 2

    async def test_retries_complete_batch_async(self) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget())
        responses = [
            Message.from_prompt(prompt='{"objectives": ["discard"]}', role="assistant"),
            Message.from_prompt(prompt='{"objectives": ["valid", "VALID"]}', role="assistant"),
        ]
        with patch.object(generator._normalizer, "send_prompt_async", new_callable=AsyncMock, side_effect=responses):
            result = await generator.execute_async(instructions="Test", count=2)
        assert result.objectives == ["valid", "VALID"]

    @pytest.mark.parametrize("count", [0, -1, True, 1.5])
    async def test_invalid_count_precedes_send_async(self, count: int) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget())
        with (
            patch.object(generator._normalizer, "send_prompt_async", new_callable=AsyncMock) as send,
            pytest.raises(ValueError, match="count"),
        ):
            await generator.execute_async(instructions="Test", count=count)
        send.assert_not_called()

    @pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
    def test_invalid_timeout(self, timeout: float) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            TargetObjectiveGenerator(target=MockPromptTarget(), timeout_seconds=timeout)

    @pytest.mark.parametrize("instructions", ["", " \n "])
    async def test_blank_instructions_precede_send_async(self, instructions: str) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget())
        with (
            patch.object(generator._normalizer, "send_prompt_async", new_callable=AsyncMock) as send,
            pytest.raises(ValueError, match="instructions"),
        ):
            await generator.execute_async(instructions=instructions, count=1)
        send.assert_not_called()

    async def test_blank_category_precedes_send_async(self) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget())
        with (
            patch.object(generator._normalizer, "send_prompt_async", new_callable=AsyncMock) as send,
            pytest.raises(ValueError, match="harm_categories"),
        ):
            await generator.execute_async(instructions="Test", count=1, harm_categories=[" "])
        send.assert_not_called()

    @pytest.mark.parametrize("system_prompt", ["", " \n "])
    def test_blank_system_prompt(self, system_prompt: str) -> None:
        with pytest.raises(ValueError, match="system_prompt"):
            TargetObjectiveGenerator(target=MockPromptTarget(), system_prompt=SeedPrompt(value=system_prompt))

    def test_string_system_prompt_rejected(self) -> None:
        with pytest.raises(TypeError, match="SeedPrompt"):
            TargetObjectiveGenerator(
                target=MockPromptTarget(),
                system_prompt="Generate objectives.",  # type: ignore[arg-type]
            )

    def test_nontext_system_prompt_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty text"):
            TargetObjectiveGenerator(
                target=MockPromptTarget(), system_prompt=SeedPrompt(value="image.png", data_type="image_path")
            )

    @pytest.mark.timeout(30)
    @pytest.mark.parametrize("setup_delay", [0, 1.1])
    async def test_timeout_bounds_pending_send_and_cleanup_async(
        self, *, setup_delay: float, caplog: pytest.LogCaptureFixture
    ) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget(), timeout_seconds=1)
        cancelled = asyncio.Event()
        cleanup_cancelled = asyncio.Event()

        async def setup_async(*, system_prompt: str, conversation_id: str) -> None:
            await asyncio.sleep(setup_delay)

        async def wait_forever_async(**kwargs: object) -> None:
            try:
                timeouts[0][1].reschedule(asyncio.get_running_loop().time())
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def reset_async(*, conversation_id: str) -> None:
            try:
                timeouts[-1][1].reschedule(asyncio.get_running_loop().time())
                await asyncio.Event().wait()
            finally:
                cleanup_cancelled.set()

        with (
            _isolate_pre_send_io(generator) as setup,
            _controlled_timeouts() as timeouts,
            patch.object(generator, "_CLEANUP_TIMEOUT_SECONDS", 0.01),
            patch.object(
                generator._normalizer, "send_prompt_async", new_callable=AsyncMock, side_effect=wait_forever_async
            ) as send,
            patch.object(generator._target, "reset_conversation_async", side_effect=reset_async) as reset,
        ):
            setup.side_effect = setup_async
            with pytest.raises(TimeoutError):
                await generator.execute_async(instructions="Test", count=2)
        assert [delay for delay, _ in timeouts] == [1, 0.01]
        assert all(timeout.expired() for _, timeout in timeouts)
        send.assert_awaited_once()
        assert cancelled.is_set()
        assert cleanup_cancelled.is_set()
        setup.assert_awaited_once()
        reset.assert_awaited_once()
        assert "Timed out resetting generation conversation" in caplog.text

    @pytest.mark.timeout(30)
    @pytest.mark.parametrize(
        ("pending_method", "conversation_initialized"),
        [
            ("set_system_prompt_async", False),
            ("get_message_pieces_async", True),
            ("delete_conversation_pieces_after_sequence_async", True),
        ],
    )
    async def test_timeout_before_send_preserves_cleanup_contract_async(
        self, *, pending_method: str, conversation_initialized: bool
    ) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget(), timeout_seconds=1)
        context = TargetObjectiveGeneratorContext(instructions="Test", count=2)
        cancelled = asyncio.Event()
        pending_owner = generator._normalizer.memory if conversation_initialized else generator._target

        async def wait_forever_async(**kwargs: object) -> None:
            try:
                timeouts[0][1].reschedule(asyncio.get_running_loop().time())
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with (
            _isolate_pre_send_io(generator),
            _controlled_timeouts() as timeouts,
            patch.object(
                pending_owner, pending_method, new_callable=AsyncMock, side_effect=wait_forever_async
            ) as pending,
            patch.object(generator._normalizer, "send_prompt_async", new_callable=AsyncMock) as send,
            patch.object(generator._target, "reset_conversation_async", new_callable=AsyncMock) as reset,
        ):
            with pytest.raises(TimeoutError):
                await generator.execute_with_context_async(context=context)
        assert timeouts[0][0] == 1
        assert timeouts[0][1].expired()
        pending.assert_awaited_once()
        assert cancelled.is_set()
        assert context._used
        assert context._conversation_initialized is conversation_initialized
        send.assert_not_awaited()
        if conversation_initialized:
            reset.assert_awaited_once_with(conversation_id=context.conversation_id)
        else:
            reset.assert_not_awaited()

    @pytest.mark.timeout(30)
    @pytest.mark.parametrize("failure", [None, ConnectionError("Generation failed"), asyncio.CancelledError()])
    async def test_cleanup_timeout_preserves_outcome_async(
        self, *, failure: BaseException | None, caplog: pytest.LogCaptureFixture
    ) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget())
        response = Message.from_prompt(prompt='{"objectives": ["A goal"]}', role="assistant")
        cleanup_cancelled = asyncio.Event()

        async def reset_async(*, conversation_id: str) -> None:
            try:
                timeouts[-1][1].reschedule(asyncio.get_running_loop().time())
                await asyncio.Event().wait()
            finally:
                cleanup_cancelled.set()

        with (
            _isolate_pre_send_io(generator) as setup,
            _controlled_timeouts() as timeouts,
            patch.object(generator, "_CLEANUP_TIMEOUT_SECONDS", 0.01),
            patch.object(
                generator._normalizer,
                "send_prompt_async",
                new_callable=AsyncMock,
                return_value=response,
                side_effect=failure,
            ) as send,
            patch.object(generator._target, "reset_conversation_async", side_effect=reset_async) as reset,
        ):
            if failure is None:
                result = await generator.execute_async(instructions="Test", count=1)
                assert result.objectives == ["A goal"]
            elif isinstance(failure, asyncio.CancelledError):
                with pytest.raises(asyncio.CancelledError) as error:
                    await generator.execute_async(instructions="Test", count=1)
                assert error.value is failure
            else:
                with pytest.raises(RuntimeError) as generation_error:
                    await generator.execute_async(instructions="Test", count=1)
                assert generation_error.value.__cause__ is failure
        assert [delay for delay, _ in timeouts] == [generator._timeout_seconds, 0.01]
        assert not timeouts[0][1].expired()
        assert timeouts[1][1].expired()
        reset.assert_awaited_once()
        assert cleanup_cancelled.is_set()
        setup.assert_awaited_once()
        send.assert_awaited_once()
        assert "Timed out resetting generation conversation" in caplog.text

    async def test_cancellation_propagates_async(self) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget())
        with (
            patch.object(generator._normalizer, "send_prompt_async", side_effect=asyncio.CancelledError),
            pytest.raises(asyncio.CancelledError),
        ):
            await generator.execute_async(instructions="Test", count=2)

    async def test_target_failure_does_not_retry_async(self) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget())
        failure = ConnectionError("Target unavailable")
        with (
            patch.object(generator._normalizer, "send_prompt_async", side_effect=failure) as send,
            pytest.raises(RuntimeError) as error,
        ):
            await generator.execute_async(instructions="Test", count=2)
        assert error.value.__cause__ is failure
        send.assert_called_once()

    @pytest.mark.parametrize("invalid_objective", ["tiny", " tiny "])
    async def test_custom_schema_controls_required_fields_and_constraints_async(self, invalid_objective: str) -> None:
        prompt = SeedPrompt(
            value="Generate objectives and a rationale.",
            response_json_schema={
                "type": "object",
                "properties": {
                    "objectives": {"type": "array", "items": {"type": "string", "minLength": 5}},
                    "rationale": {"type": "string"},
                },
                "required": ["objectives", "rationale"],
                "additionalProperties": False,
            },
        )
        generator = TargetObjectiveGenerator(target=MockPromptTarget(), system_prompt=prompt)
        responses = [
            Message.from_prompt(
                prompt=json.dumps({"objectives": [invalid_objective], "rationale": "test"}), role="assistant"
            ),
            Message.from_prompt(prompt='{"objectives": ["Valid goal"], "rationale": "test"}', role="assistant"),
        ]
        with patch.object(
            generator._normalizer, "send_prompt_async", new_callable=AsyncMock, side_effect=responses
        ) as send:
            result = await generator.execute_async(instructions="Test", count=1)
        assert result.objectives == ["Valid goal"]
        assert send.call_count == 2

    @pytest.mark.parametrize(
        "schema",
        [
            {"type": "not-a-json-type"},
            {"type": "object", "properties": {"prompts": {"type": "array"}}, "required": ["prompts"]},
            {"type": "object", "properties": {"objectives": True}, "required": ["objectives"]},
            {
                "type": "object",
                "properties": {"objectives": {"type": "array", "items": {"type": "integer"}}},
                "required": ["objectives"],
            },
        ],
    )
    def test_invalid_schema_rejected_before_execution(self, schema: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="response_json_schema"):
            TargetObjectiveGenerator(
                target=MockPromptTarget(),
                system_prompt=SeedPrompt(value="Test", response_json_schema=schema),
            )

    async def test_custom_schema_missing_required_field_retries_async(self) -> None:
        prompt = await asyncio.to_thread(SeedPrompt.from_yaml_file, TargetObjectiveGenerator.DEFAULT_SYSTEM_PROMPT_PATH)
        assert prompt.response_json_schema is not None
        prompt.response_json_schema["properties"]["rationale"] = {"type": "string"}
        prompt.response_json_schema["required"].append("rationale")
        generator = TargetObjectiveGenerator(target=MockPromptTarget(), system_prompt=prompt)
        responses = [
            Message.from_prompt(prompt='{"objectives": ["A goal"]}', role="assistant"),
            Message.from_prompt(prompt='{"objectives": ["A goal"], "rationale": "test"}', role="assistant"),
        ]
        with patch.object(
            generator._normalizer, "send_prompt_async", new_callable=AsyncMock, side_effect=responses
        ) as send:
            result = await generator.execute_async(instructions="Test", count=1)
        assert result.objectives == ["A goal"]
        assert send.call_count == 2

    async def test_prompt_configuration_is_snapshotted_async(self) -> None:
        prompt = await asyncio.to_thread(SeedPrompt.from_yaml_file, TargetObjectiveGenerator.DEFAULT_SYSTEM_PROMPT_PATH)
        original = prompt.model_dump()
        target = MockPromptTarget()
        generator = TargetObjectiveGenerator(target=target, system_prompt=prompt)
        prompt.value = "Changed"
        assert prompt.response_json_schema is not None
        prompt.response_json_schema["required"].append("unexpected")
        response = Message.from_prompt(prompt='{"objectives": ["A goal"]}', role="assistant")
        with patch.object(
            generator._normalizer, "send_prompt_async", new_callable=AsyncMock, return_value=response
        ) as send:
            await generator.execute_async(instructions="Test", count=1)
        assert target.system_prompt == original["value"]
        config = JsonResponseConfig.from_metadata(
            metadata=send.call_args.kwargs["message"].message_pieces[0].prompt_metadata
        )
        assert config.json_schema == original["response_json_schema"]

    @pytest.mark.parametrize("failure", [None, ConnectionError("Unavailable"), asyncio.CancelledError()])
    async def test_cleanup_on_success_failure_and_cancellation_async(self, failure: BaseException | None) -> None:
        target = MockPromptTarget()
        generator = TargetObjectiveGenerator(target=target)
        context = TargetObjectiveGeneratorContext(instructions="Test", count=1)
        response = Message.from_prompt(prompt='{"objectives": ["A goal"]}', role="assistant")
        with (
            patch.object(
                generator._normalizer,
                "send_prompt_async",
                new_callable=AsyncMock,
                return_value=response,
                side_effect=failure,
            ),
            patch.object(target, "reset_conversation_async", new_callable=AsyncMock) as reset,
        ):
            if failure is None:
                await generator.execute_with_context_async(context=context)
            else:
                expected_error = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else RuntimeError
                with pytest.raises(expected_error):
                    await generator.execute_with_context_async(context=context)
        reset.assert_awaited_once_with(conversation_id=context.conversation_id)

    async def test_cleanup_failure_does_not_replace_generation_failure_async(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        target = MockPromptTarget()
        generator = TargetObjectiveGenerator(target=target)
        failure = ConnectionError("Generation failed")
        with (
            patch.object(generator._normalizer, "send_prompt_async", side_effect=failure),
            patch.object(target, "reset_conversation_async", side_effect=RuntimeError("Cleanup failed")),
            pytest.raises(RuntimeError) as error,
        ):
            await generator.execute_async(instructions="Test", count=1)
        assert error.value.__cause__ is failure
        assert "Failed to reset generation conversation" in caplog.text

    async def test_setup_failure_does_not_reset_existing_conversation_async(self) -> None:
        target = MockPromptTarget()
        generator = TargetObjectiveGenerator(target=target)
        with (
            patch.object(target, "set_system_prompt_async", side_effect=RuntimeError("Conversation already exists")),
            patch.object(target, "reset_conversation_async", new_callable=AsyncMock) as reset,
            pytest.raises(RuntimeError),
        ):
            await generator.execute_async(instructions="Test", count=1)
        reset.assert_not_awaited()

    @pytest.mark.parametrize("failure", [None, ConnectionError("Unavailable")])
    async def test_context_cannot_be_reused_async(self, failure: Exception | None) -> None:
        generator = TargetObjectiveGenerator(target=MockPromptTarget())
        context = TargetObjectiveGeneratorContext(instructions="Test", count=1)
        response = Message.from_prompt(prompt='{"objectives": ["A goal"]}', role="assistant")
        with patch.object(
            generator._normalizer,
            "send_prompt_async",
            new_callable=AsyncMock,
            return_value=response,
            side_effect=failure,
        ) as send:
            if failure is None:
                await generator.execute_with_context_async(context=context)
            else:
                with pytest.raises(RuntimeError):
                    await generator.execute_with_context_async(context=context)
            for used_context in (context, context.duplicate()):
                with pytest.raises(ValueError, match="single-use"):
                    await generator.execute_with_context_async(context=used_context)
        send.assert_awaited_once()

    async def test_execute_creates_fresh_contexts_async(self) -> None:
        target = MockPromptTarget()
        generator = TargetObjectiveGenerator(target=target)
        response = Message.from_prompt(prompt='{"objectives": ["A goal"]}', role="assistant")
        with patch.object(generator._normalizer, "send_prompt_async", new_callable=AsyncMock, return_value=response):
            first = await generator.execute_async(instructions="Test", count=1)
            second = await generator.execute_async(instructions="Test", count=1)
        assert first.conversation_id != second.conversation_id
        assert not hasattr(generator, "generate_async")
