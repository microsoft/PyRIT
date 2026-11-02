# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import ast
import asyncio
import inspect
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

import jupytext
import pytest
from unit.assert_mocks import (
    assert_generation_response_async,
    make_assert_config,
    make_assert_generation_target,
)
from unit.async_utils import settle_tasks_async, wait_for_completion_async, wait_for_event_async

from pyrit.datasets import AssertProvider
from pyrit.exceptions import InvalidJsonException
from pyrit.executor.promptgen import AssertGenerator, AssertGeneratorContext
from pyrit.memory import MemoryInterface
from pyrit.models import (
    AssertGroupMetadata,
    JsonResponseConfig,
    Message,
    MessagePiece,
    SeedObjective,
    SeedOrigin,
    WebSearchConfig,
    WebSearchMode,
)
from pyrit.scenario import DatasetAttackConfiguration, DatasetSource


@pytest.fixture
def no_retry_wait() -> Iterator[None]:
    with patch.dict(
        "os.environ",
        {"RETRY_MAX_NUM_ATTEMPTS": "2", "RETRY_WAIT_MIN_SECONDS": "0", "RETRY_WAIT_MAX_SECONDS": "0"},
    ):
        yield


@pytest.mark.usefixtures("patch_central_database", "no_retry_wait")
class TestAssertGenerator:
    async def test_complete_pipeline_retains_native_evidence_async(self, sqlite_instance: MemoryInterface) -> None:
        target = make_assert_generation_target()
        config = make_assert_config(dimensions=[{"name": "channel", "description": "Where the user asks."}])
        generator = AssertGenerator(target=target)
        with (
            patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async) as send,
            patch.object(target, "reset_conversation_async", wraps=target.reset_conversation_async) as reset,
        ):
            result = await generator.execute_async(config=config)
        assert len(result.seed_groups) == 6
        assert all(
            len([prompt for prompt in group.prompts if prompt.role == "user"]) == 1 for group in result.seed_groups
        )
        assert all(group.adversarial_prompts for group in result.seed_groups)
        assert len(result.dimensions[0].levels) == 3
        assert result.generation_conversation_ids
        assert result.generation_targets == {
            "generation": target.get_identifier().model_dump(mode="json"),
            "research": target.get_identifier().model_dump(mode="json"),
        }
        assert send.call_count == len(result.generation_conversation_ids)
        assert reset.await_args_list == [
            call(conversation_id=conversation_id) for conversation_id in result.generation_conversation_ids
        ]
        for conversation_id in result.generation_conversation_ids:
            pieces = await sqlite_instance.get_message_pieces_async(conversation_id=conversation_id)
            assert any(piece.role == "assistant" for piece in pieces)
        assert not await sqlite_instance.get_seeds_async(dataset_name="assert")
        assert any("{{ literal }}" in prompt.value for group in result.seed_groups for prompt in group.prompts)
        for group in result.seed_groups:
            metadata = AssertGroupMetadata.from_metadata(group.objective.metadata)
            assert metadata.config.behavior_name == config.behavior_name
            assert len(metadata.provenance.conversation_ids) == 4
            pieces = await sqlite_instance.get_message_pieces_async(
                conversation_id=metadata.provenance.group_generation_conversation_id
            )
            request = next(piece for piece in pieces if piece.role == "user")
            assert json.loads(request.original_value)["assignment"] == metadata.assignment

    async def test_documented_generation_example_stores_directly_async(self, sqlite_instance: MemoryInterface) -> None:
        path = Path(__file__).parents[4] / "doc" / "code" / "executor" / "7_promptgen.py"
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        notebook = jupytext.reads(text, fmt="py:percent")
        sources = []
        in_assert_section = False
        for cell in notebook.cells:
            if cell.cell_type == "markdown" and cell.source.startswith("## "):
                in_assert_section = cell.source.startswith("## ASSERT Generator")
            elif in_assert_section and cell.cell_type == "code":
                sources.append(cell.source)
        namespace = {}
        target = make_assert_generation_target()
        with (
            patch("pyrit.setup.initialize_pyrit_async", new_callable=AsyncMock),
            patch("pyrit.prompt_target.OpenAIChatTarget", return_value=target) as construct,
            patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async) as send,
            patch.object(sqlite_instance, "get_seeds_async", wraps=sqlite_instance.get_seeds_async) as fetch_stored,
        ):
            for source in sources:
                result = eval(compile(source, str(path), "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT), namespace)
                if inspect.isawaitable(result):
                    await result
        construct.assert_called_once()
        fetch_stored.assert_not_called()
        assert send.call_count > 0
        stored = await sqlite_instance.get_seeds_async(dataset_name="assert_support")
        assert sum(isinstance(seed, SeedObjective) for seed in stored) == 2

    async def test_json_schema_adaptation_uses_the_existing_normalizer_async(self) -> None:
        target = make_assert_generation_target()
        target.apply_capabilities(
            capabilities=target.capabilities.model_copy(
                update={"supports_json_schema": False, "supports_json_output": False}
            )
        )

        async def adapted_async(*, normalized_conversation: list[Message]) -> list[Message]:
            request = normalized_conversation[-1].get_piece()
            assert "### Response format" in request.converted_value
            value, _ = json.JSONDecoder().raw_decode(request.converted_value)
            copied = [message.model_copy(deep=True) for message in normalized_conversation]
            copied[-1].get_piece().converted_value = json.dumps(value)
            return await assert_generation_response_async(normalized_conversation=copied)

        with patch.object(target, "_send_prompt_to_target_async", side_effect=adapted_async):
            result = await AssertGenerator(target=target).execute_async(config=make_assert_config())
        assert len(result.seed_groups) == 6

    @pytest.mark.parametrize("dimensions", [None, []])
    async def test_explicit_or_absent_dimensions_skip_level_generation_async(
        self, dimensions: list[object] | None
    ) -> None:
        target = make_assert_generation_target()
        config = make_assert_config(**({"dimensions": dimensions} if dimensions is not None else {}))
        with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async) as send:
            await AssertGenerator(target=target).execute_async(config=config)
        stages = [
            call.kwargs["normalized_conversation"][-1].get_piece().prompt_metadata["assert_stage"]
            for call in send.call_args_list
        ]
        assert "dimensions" not in stages

    async def test_generated_dimensions_preserve_leading_underscores_async(self) -> None:
        target = make_assert_generation_target()
        config = make_assert_config(
            group_count=1, dimensions=[{"name": "_channel", "description": "Where the customer asks."}]
        )
        with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async):
            result = await AssertGenerator(target=target).execute_async(config=config)
        metadata = AssertGroupMetadata.from_metadata(result.seed_groups[0].objective.metadata)
        assert result.dimensions[0].name == "_channel"
        assert set(metadata.dimension_values) == {"_channel"}

    async def test_original_requirement_and_context_reach_each_stage_as_data_async(self) -> None:
        target = make_assert_generation_target()
        config = make_assert_config(
            behavior_description="Allow public policy help, except {{ literal_exception }}.",
            context="Support on {% raw %}a forum{% endraw %}.",
        )
        with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async) as send:
            await AssertGenerator(target=target).execute_async(config=config)
        for call in send.call_args_list:
            request = call.kwargs["normalized_conversation"][-1].get_piece()
            payload = json.loads(request.converted_value)
            assert payload["behavior_description"] == config.behavior_description
            assert payload["context"] == config.context
            if request.prompt_metadata["assert_stage"] == "seed_groups":
                schema = JsonResponseConfig.from_metadata(metadata=request.prompt_metadata).json_schema
                assert schema["properties"]["seed_groups"]["minItems"] == payload["count"]
                assert schema["properties"]["seed_groups"]["maxItems"] == payload["count"]

    @pytest.mark.parametrize(
        "updates",
        [{"target_system_prompt": "Use the fixed support policy."}, {"target_owns_instructions": True}],
    )
    async def test_generation_respects_target_instruction_ownership_async(self, updates: dict[str, object]) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async):
            result = await generator.execute_async(config=make_assert_config(**updates))
        expected = [updates["target_system_prompt"]] if "target_system_prompt" in updates else []
        assert all(
            [prompt.value for prompt in group.prompts if prompt.role == "system"] == expected
            for group in result.seed_groups
        )

    async def test_generated_target_instructions_are_retained_async(self) -> None:
        target = make_assert_generation_target()
        with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async):
            result = await AssertGenerator(target=target).execute_async(config=make_assert_config())
        assert all(
            [prompt.value for prompt in group.prompts if prompt.role == "system"]
            == ["You are a customer-support assistant."]
            for group in result.seed_groups
        )

    @pytest.mark.parametrize("system_prompt", ["", "Use the published support policy."])
    async def test_whitespace_system_prompt_retries_then_retains_all_groups_async(self, system_prompt: str) -> None:
        target = make_assert_generation_target()
        batch_attempts: dict[str, int] = {}

        async def whitespace_first_async(*, normalized_conversation: list[Message]) -> list[Message]:
            responses = await assert_generation_response_async(normalized_conversation=normalized_conversation)
            request = normalized_conversation[-1].get_piece()
            if request.prompt_metadata["assert_stage"] == "seed_groups":
                conversation_id = request.conversation_id
                batch_attempts[conversation_id] = batch_attempts.get(conversation_id, 0) + 1
                invalid = len(batch_attempts) == 2 and batch_attempts[conversation_id] == 1
                assert all(message.get_piece().role != "assistant" for message in normalized_conversation)
                value = json.loads(responses[0].get_value())
                for group in value["seed_groups"]:
                    group["system_prompt"] = " \n " if invalid else system_prompt
                responses[0].get_piece().converted_value = json.dumps(value)
            return responses

        with patch.object(target, "_send_prompt_to_target_async", side_effect=whitespace_first_async):
            result = await AssertGenerator(target=target).execute_async(config=make_assert_config())

        assert len(batch_attempts) > 1
        assert sum(batch_attempts.values()) == len(batch_attempts) + 1
        assert len(result.seed_groups) == 6
        assert all(
            [prompt.value for prompt in group.prompts if prompt.role == "system"]
            == ([system_prompt] if system_prompt else [])
            for group in result.seed_groups
        )

    @pytest.mark.parametrize("include_user, include_adversarial", [(True, False), (False, True), (True, True)])
    async def test_requested_inputs_share_the_generated_objective_async(
        self, include_user: bool, include_adversarial: bool
    ) -> None:
        target = make_assert_generation_target()
        config = make_assert_config(
            group_count=1, include_user_prompt=include_user, include_adversarial_prompt=include_adversarial
        )
        with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async):
            result = await AssertGenerator(target=target).execute_async(config=config)
        group = result.seed_groups[0]
        assert len([prompt for prompt in group.prompts if prompt.role == "user"]) == int(include_user)
        assert bool(group.adversarial_prompts) == include_adversarial
        if group.adversarial_prompts is not None:
            prompt = group.adversarial_prompts.system_prompt
            assert prompt is not None
            rendered = prompt.render_template_value(objective=group.objective.value)
            assert group.objective.value in rendered
            assert "You are a customer returning a purchase." in rendered
            assert prompt.parameters == ["objective"]
            assert prompt.origin == SeedOrigin.GENERATED
            assert set(prompt.response_json_schema["required"]) == {
                "next_message",
                "rationale",
                "last_response_summary",
            }

    async def test_generated_adversarial_instructions_remain_literal_async(self) -> None:
        target = make_assert_generation_target()
        instructions = "A customer who says {% endraw %}{% invalid %}{{ 7 * 7 }}."

        async def literal_instructions_async(*, normalized_conversation: list[Message]) -> list[Message]:
            messages = await assert_generation_response_async(normalized_conversation=normalized_conversation)
            if normalized_conversation[-1].get_piece().prompt_metadata["assert_stage"] == "seed_groups":
                value = json.loads(messages[0].get_value())
                value["seed_groups"][0]["adversarial_instructions"] = instructions
                messages[0].get_piece().converted_value = json.dumps(value)
            return messages

        with patch.object(target, "_send_prompt_to_target_async", side_effect=literal_instructions_async):
            result = await AssertGenerator(target=target).execute_async(config=make_assert_config(group_count=1))
        group = result.seed_groups[0]
        assert group.adversarial_prompts is not None
        prompt = group.adversarial_prompts.system_prompt
        assert prompt is not None
        assert instructions in prompt.render_template_value(objective=group.objective.value)
        assert not prompt.is_jinja_template

    async def test_generated_file_path_is_a_literal_text_message_async(self, tmp_path: Path) -> None:
        path = tmp_path / "image.png"
        await asyncio.to_thread(path.write_bytes, b"fixture")
        target = make_assert_generation_target()

        async def literal_path_async(*, normalized_conversation: list[Message]) -> list[Message]:
            messages = await assert_generation_response_async(normalized_conversation=normalized_conversation)
            if normalized_conversation[-1].get_piece().prompt_metadata["assert_stage"] == "seed_groups":
                value = json.loads(messages[0].get_value())
                value["seed_groups"][0]["user_prompt"] = str(path)
                messages[0].get_piece().converted_value = json.dumps(value)
            return messages

        with patch.object(target, "_send_prompt_to_target_async", side_effect=literal_path_async):
            result = await AssertGenerator(target=target).execute_async(config=make_assert_config(group_count=1))
        prompt = next(prompt for prompt in result.seed_groups[0].prompts if prompt.role == "user")
        assert prompt.value == str(path)
        assert prompt.data_type == "text"

    @pytest.mark.parametrize("field", ["user_prompt", "adversarial_instructions"])
    async def test_missing_requested_input_is_retried_and_rejected_async(self, field: str) -> None:
        target = make_assert_generation_target()

        async def missing_input_async(*, normalized_conversation: list[Message]) -> list[Message]:
            messages = await assert_generation_response_async(normalized_conversation=normalized_conversation)
            if normalized_conversation[-1].get_piece().prompt_metadata["assert_stage"] == "seed_groups":
                value = json.loads(messages[0].get_value())
                value["seed_groups"][0][field] = None
                messages[0].get_piece().converted_value = json.dumps(value)
            return messages

        with patch.object(target, "_send_prompt_to_target_async", side_effect=missing_input_async):
            with pytest.raises(RuntimeError, match="exactly the requested"):
                await AssertGenerator(target=target).execute_async(config=make_assert_config(group_count=1))

    @pytest.mark.parametrize("received", [0, 2])
    async def test_wrong_batch_count_raises_after_bounded_retries_async(self, received: int) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        count = 0

        async def incomplete_async(*, normalized_conversation: list[Message]) -> list[Message]:
            nonlocal count
            request = normalized_conversation[-1].get_piece()
            if request.prompt_metadata["assert_stage"] == "seed_groups":
                count += 1
                return [
                    MessagePiece(
                        role="assistant",
                        original_value=json.dumps(
                            {
                                "seed_groups": [
                                    {
                                        "title": "Returns",
                                        "objective": "Clarify returns.",
                                        "user_prompt": "A test request.",
                                    }
                                    for _ in range(received)
                                ]
                            }
                        ),
                        conversation_id=request.conversation_id,
                    ).to_message()
                ]
            return await assert_generation_response_async(normalized_conversation=normalized_conversation)

        with patch.object(target, "_send_prompt_to_target_async", side_effect=incomplete_async):
            with pytest.raises(RuntimeError, match="Requested 1 valid seed groups") as error:
                await generator.execute_async(config=make_assert_config(group_count=1))
        assert count == 2
        assert isinstance(error.value.__cause__, InvalidJsonException)

    async def test_malformed_json_retries_on_clean_history_async(self) -> None:
        target = make_assert_generation_target()
        attempts = 0

        async def malformed_first_async(*, normalized_conversation: list[Message]) -> list[Message]:
            nonlocal attempts
            request = normalized_conversation[-1].get_piece()
            if request.prompt_metadata["assert_stage"] == "taxonomy":
                attempts += 1
                assert not any(
                    piece.role == "assistant" for message in normalized_conversation for piece in message.message_pieces
                )
                if attempts == 1:
                    return [
                        MessagePiece(
                            role="assistant",
                            original_value="not JSON",
                            conversation_id=request.conversation_id,
                        ).to_message()
                    ]
            return await assert_generation_response_async(normalized_conversation=normalized_conversation)

        with patch.object(target, "_send_prompt_to_target_async", side_effect=malformed_first_async):
            result = await AssertGenerator(target=target).execute_async(config=make_assert_config())
        assert attempts == 2
        assert len(result.seed_groups) == 6

    @pytest.mark.parametrize("stage", ["systematization", "taxonomy", "dimensions"])
    async def test_invalid_stage_contract_is_retried_then_rejected_async(self, stage: str) -> None:
        target = make_assert_generation_target()
        attempts = 0

        async def invalid_async(*, normalized_conversation: list[Message]) -> list[Message]:
            nonlocal attempts
            responses = await assert_generation_response_async(normalized_conversation=normalized_conversation)
            request = normalized_conversation[-1].get_piece()
            if request.prompt_metadata["assert_stage"] == stage:
                attempts += 1
                value = json.loads(responses[0].get_value())
                if stage == "systematization":
                    value["behavior"] = value["concept_spec"]["behavior"] = "changed"
                elif stage == "taxonomy":
                    value["behavior"]["name"] = "changed"
                else:
                    value["dimensions"][0]["levels"].pop()
                responses[0].get_piece().converted_value = json.dumps(value)
            return responses

        config = make_assert_config(dimensions=[{"name": "channel", "description": "Where the user asks."}])
        with (
            patch.object(target, "_send_prompt_to_target_async", side_effect=invalid_async) as send,
            patch.object(target, "reset_conversation_async", wraps=target.reset_conversation_async) as reset,
        ):
            with pytest.raises(RuntimeError, match=f"ASSERT {stage} validation failed"):
                await AssertGenerator(target=target).execute_async(config=config)
        assert attempts == 2
        conversation_ids = dict.fromkeys(
            send_call.kwargs["normalized_conversation"][-1].get_piece().conversation_id
            for send_call in send.call_args_list
        )
        assert reset.await_args_list == [call(conversation_id=conversation_id) for conversation_id in conversation_ids]

    async def test_partial_generation_does_not_store_successful_batches_async(
        self, sqlite_instance: MemoryInterface
    ) -> None:
        target = make_assert_generation_target()
        provider = AssertProvider(
            dataset_name="incomplete_generated",
            generator=AssertGenerator(target=target),
            config=make_assert_config(group_count=2),
        )
        dataset_config = DatasetAttackConfiguration(
            sources=[DatasetSource(name=provider.dataset_name, provider=provider)]
        )
        conversations = []
        batches = 0

        async def partial_async(*, normalized_conversation: list[Message]) -> list[Message]:
            nonlocal batches
            request = normalized_conversation[-1].get_piece()
            conversations.append(request.conversation_id)
            if request.prompt_metadata["assert_stage"] == "seed_groups":
                batches += 1
                if batches > 1:
                    return [
                        MessagePiece(
                            role="assistant",
                            original_value='{"seed_groups": []}',
                            conversation_id=request.conversation_id,
                        ).to_message()
                    ]
            return await assert_generation_response_async(normalized_conversation=normalized_conversation)

        with patch.object(target, "_send_prompt_to_target_async", side_effect=partial_async):
            with pytest.raises(RuntimeError, match="seed_groups validation failed"):
                await dataset_config.prepare_async()
        assert not await sqlite_instance.get_seeds_async(dataset_name=provider.dataset_name)
        retained = await sqlite_instance.get_message_pieces_async(conversation_id=conversations[0])
        assert any(piece.role == "assistant" for piece in retained)

    async def test_context_is_single_use_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        context = AssertGeneratorContext(config=make_assert_config(group_count=1))
        with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async):
            await generator.execute_with_context_async(context=context)
        with pytest.raises(ValueError, match="single-use"):
            await generator.execute_with_context_async(context=context)

    async def test_research_requires_declared_web_search_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        with patch.object(target, "_send_prompt_to_target_async") as send:
            with pytest.raises(ValueError, match="supports_web_search"):
                await generator.execute_async(config=make_assert_config(web_search=True))
        send.assert_not_called()

    async def test_separate_research_target_receives_research_stages_async(self) -> None:
        target = make_assert_generation_target()
        research = make_assert_generation_target(web_search=True)
        config = make_assert_config(
            web_search=True, dimensions=[{"name": "channel", "description": "Where the user asks."}]
        )
        with (
            patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async) as send,
            patch.object(
                research, "_send_prompt_to_target_async", side_effect=assert_generation_response_async
            ) as search,
            patch.object(target, "reset_conversation_async", wraps=target.reset_conversation_async) as reset_target,
            patch.object(
                research, "reset_conversation_async", wraps=research.reset_conversation_async
            ) as reset_research,
        ):
            await AssertGenerator(target=target, research_target=research).execute_async(config=config)
        research_stages = {
            call.kwargs["normalized_conversation"][-1].get_piece().prompt_metadata["assert_stage"]
            for call in search.call_args_list
        }
        generation_stages = {
            call.kwargs["normalized_conversation"][-1].get_piece().prompt_metadata["assert_stage"]
            for call in send.call_args_list
        }
        assert research_stages == {"systematization", "dimensions"}
        assert generation_stages == {"taxonomy", "seed_groups"}
        for send_call in search.call_args_list:
            request = send_call.kwargs["normalized_conversation"][-1].get_piece()
            assert WebSearchConfig.from_metadata(metadata=request.prompt_metadata).mode == WebSearchMode.REQUIRED
        for send_call in send.call_args_list:
            request = send_call.kwargs["normalized_conversation"][-1].get_piece()
            assert WebSearchConfig.from_metadata(metadata=request.prompt_metadata).mode == WebSearchMode.DISABLED
        for transport, reset in ((send, reset_target), (search, reset_research)):
            assert reset.await_args_list == [
                call(conversation_id=send_call.kwargs["normalized_conversation"][-1].get_piece().conversation_id)
                for send_call in transport.call_args_list
            ]

    async def test_blocked_json_is_not_accepted_or_retried_as_malformed_json_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)

        async def blocked_async(*, normalized_conversation: list[Message]) -> list[Message]:
            responses = await assert_generation_response_async(normalized_conversation=normalized_conversation)
            responses[0].get_piece().response_error = "blocked"
            return responses

        with patch.object(target, "_send_prompt_to_target_async", side_effect=blocked_async) as send:
            with pytest.raises(RuntimeError, match="blocked"):
                await generator.execute_async(config=make_assert_config())
        send.assert_called_once()

    async def test_research_accepts_tool_evidence_with_json_text_async(self) -> None:
        target = make_assert_generation_target(web_search=True)
        generator = AssertGenerator(target=target)

        async def with_evidence_async(*, normalized_conversation: list[Message]) -> list[Message]:
            messages = await assert_generation_response_async(normalized_conversation=normalized_conversation)
            if normalized_conversation[-1].get_piece().prompt_metadata["assert_stage"] == "systematization":
                messages[0].message_pieces.insert(
                    0,
                    MessagePiece(
                        role="assistant",
                        original_value=json.dumps({"type": "web_search_call"}),
                        original_value_data_type="tool_call",
                        conversation_id=messages[0].get_piece().conversation_id,
                    ),
                )
            return messages

        with patch.object(target, "_send_prompt_to_target_async", side_effect=with_evidence_async):
            result = await generator.execute_async(config=make_assert_config(web_search=True, group_count=1))
        assert len(result.seed_groups) == 1
        assert not result.coverage.pairwise_complete

    async def test_group_generation_is_sequential_and_preserves_batch_counts_async(self) -> None:
        target = make_assert_generation_target()
        active = peak = 0

        async def delayed_async(*, normalized_conversation: list[Message]) -> list[Message]:
            nonlocal active, peak
            request = normalized_conversation[-1].get_piece()
            if request.prompt_metadata["assert_stage"] == "seed_groups":
                assert json.loads(request.converted_value)["count"] <= 5
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0)
                active -= 1
            return await assert_generation_response_async(normalized_conversation=normalized_conversation)

        with patch.object(target, "_send_prompt_to_target_async", side_effect=delayed_async):
            result = await AssertGenerator(target=target).execute_async(config=make_assert_config(group_count=40))
        assert peak == 1
        assert len(result.seed_groups) == 40
        assert (
            len({AssertGroupMetadata.from_metadata(group.objective.metadata).group_id for group in result.seed_groups})
            == 40
        )

    @pytest.mark.parametrize(
        "stage, expected_sends",
        [("setup", 0), ("systematization", 1), ("taxonomy", 2), ("dimensions", 3), ("seed_groups", 4)],
    )
    async def test_cancellation_releases_target_conversation_async(self, *, stage: str, expected_sends: int) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        started = asyncio.Event()
        cancelled = asyncio.Event()
        conversation_ids: list[str] = []
        set_system_prompt_async = target.set_system_prompt_async

        async def block_async() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def setup_async(*, system_prompt: str, conversation_id: str) -> None:
            conversation_ids.append(conversation_id)
            if stage == "setup":
                await block_async()
            await set_system_prompt_async(system_prompt=system_prompt, conversation_id=conversation_id)

        async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
            if normalized_conversation[-1].get_piece().prompt_metadata["assert_stage"] == stage:
                await block_async()
            return await assert_generation_response_async(normalized_conversation=normalized_conversation)

        with (
            patch.object(target, "set_system_prompt_async", side_effect=setup_async),
            patch.object(target, "_send_prompt_to_target_async", side_effect=respond_async) as send,
            patch.object(target, "reset_conversation_async", new_callable=AsyncMock) as reset,
        ):
            config = make_assert_config(dimensions=[{"name": "channel", "description": "Where the user asks."}])
            task = asyncio.create_task(generator.execute_async(config=config))
            try:
                await wait_for_event_async(event=started, tasks=[task])
                reset.assert_not_awaited()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await wait_for_completion_async(future=task)
            finally:
                if not task.done() and not task.cancelling():
                    task.cancel()
                await settle_tasks_async(task)
        assert task.cancelled()
        assert cancelled.is_set()
        assert send.await_count == expected_sends
        assert len(conversation_ids) == max(1, expected_sends)
        assert reset.await_args_list == [call(conversation_id=conversation_id) for conversation_id in conversation_ids]

    async def test_target_timeout_propagates_without_a_generation_retry_async(self) -> None:
        target = make_assert_generation_target()
        timeout = TimeoutError("Request timeout")
        with (
            patch.object(target, "_send_prompt_to_target_async", side_effect=timeout) as send,
            patch.object(target, "reset_conversation_async", new_callable=AsyncMock) as reset,
        ):
            with pytest.raises(RuntimeError, match="Error sending prompt") as error:
                await AssertGenerator(target=target).execute_async(config=make_assert_config())
        send.assert_called_once()
        reset.assert_awaited_once()
        request = send.call_args.kwargs["normalized_conversation"][-1].get_piece()
        assert reset.await_args.kwargs["conversation_id"] == request.conversation_id
        cause = error.value
        while cause.__cause__ is not None:
            cause = cause.__cause__
        assert cause is timeout

    def test_immutable_target_history_is_rejected(self) -> None:
        target = make_assert_generation_target()
        target.apply_capabilities(
            capabilities=target.capabilities.model_copy(update={"supports_editable_history": False})
        )
        with pytest.raises(ValueError, match="supports_editable_history"):
            AssertGenerator(target=target)
