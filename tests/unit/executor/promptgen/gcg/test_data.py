# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Unit tests for torch-free GCG dataset adaptation."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.executor.promptgen.gcg.config import GCGDataConfig
from pyrit.executor.promptgen.gcg.data import create_gcg_data_config_async, load_goals_and_targets
from pyrit.models import Message, MessagePiece, Score, SeedDataset, SeedObjective, SeedPrompt
from pyrit.prompt_target import PromptTarget
from pyrit.score import TrueFalseScorer


def _mock_adversarial_target(*responses: str) -> MagicMock:
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock(
        side_effect=[[Message.from_prompt(prompt=response, role="assistant")] for response in responses]
    )
    return target


@pytest.fixture
def completion_scorer() -> MagicMock:
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_text_async = AsyncMock(
        return_value=[
            Score(score_value="true", score_type="true_false", message_piece_id=uuid.uuid4()),
        ]
    )
    return scorer


async def test_create_config_preserves_provided_targets_without_adversarial_target() -> None:
    dataset = SeedDataset(
        seeds=[
            SeedObjective(value="goal a", metadata={"gcg_target": "Provided a"}),
            SeedObjective(value="goal b", metadata={"gcg_target": "Provided b"}),
        ]
    )

    config = await create_gcg_data_config_async(dataset=dataset)

    assert config.train_goals == ["goal a", "goal b"]
    assert config.train_targets == ["Provided a", "Provided b"]


async def test_create_config_generates_only_missing_targets(completion_scorer: MagicMock) -> None:
    dataset = SeedDataset(
        seeds=[
            SeedObjective(value="goal a", metadata={"gcg_target": "Provided a"}),
            SeedObjective(value="goal b"),
            SeedObjective(value="goal c", metadata={"gcg_target": "  "}),
        ]
    )
    target = _mock_adversarial_target("  Generated b  ", "Generated c")

    config = await create_gcg_data_config_async(
        dataset=dataset, adversarial_target=target, completion_scorer=completion_scorer
    )

    assert config.train_goals == ["goal a", "goal b", "goal c"]
    assert config.train_targets == ["Provided a", "Generated b", "Generated c"]
    assert target.send_prompt_async.await_count == 2
    sent_prompts = [call.kwargs["message"].get_value() for call in target.send_prompt_async.await_args_list]
    assert "goal b" in sent_prompts[0]
    assert "goal c" in sent_prompts[1]
    assert completion_scorer.score_text_async.await_count == 2
    assert completion_scorer.score_text_async.await_args_list[0].args == ("Generated b",)
    assert completion_scorer.score_text_async.await_args_list[0].kwargs == {"objective": "goal b"}
    restored = GCGDataConfig.from_json(config.to_json())
    assert load_goals_and_targets(data=restored) == (
        ["goal a", "goal b", "goal c"],
        ["Provided a", "Generated b", "Generated c"],
        [],
        [],
    )


async def test_create_config_resolves_test_dataset(completion_scorer: MagicMock) -> None:
    train = SeedDataset(seeds=[SeedObjective(value="train", metadata={"gcg_target": "Provided"})])
    test = SeedDataset(seeds=[SeedObjective(value="test")])
    target = _mock_adversarial_target("Generated test")

    config = await create_gcg_data_config_async(
        dataset=train,
        test_dataset=test,
        adversarial_target=target,
        completion_scorer=completion_scorer,
    )

    assert config.test_goals == ["test"]
    assert config.test_targets == ["Generated test"]
    assert GCGDataConfig.from_json(config.to_json()) == config


async def test_create_config_missing_target_without_adversarial_target_raises() -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value="goal")])

    with pytest.raises(ValueError, match="no adversarial_target"):
        await create_gcg_data_config_async(dataset=dataset)


@pytest.mark.parametrize("response", ["", "   "])
async def test_create_config_empty_generated_target_raises(response: str, completion_scorer: MagicMock) -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value="goal")])
    target = _mock_adversarial_target(response)

    with pytest.raises(ValueError, match="empty target completion"):
        await create_gcg_data_config_async(
            dataset=dataset, adversarial_target=target, completion_scorer=completion_scorer
        )


async def test_create_config_missing_generated_response_raises(completion_scorer: MagicMock) -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value="goal")])
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock(return_value=[])

    with pytest.raises(ValueError, match="returned no target completion"):
        await create_gcg_data_config_async(
            dataset=dataset, adversarial_target=target, completion_scorer=completion_scorer
        )


async def test_create_config_overlong_generated_target_raises(completion_scorer: MagicMock) -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value="goal")])
    target = _mock_adversarial_target("too long")

    with pytest.raises(ValueError, match="maximum is 3"):
        await create_gcg_data_config_async(
            dataset=dataset,
            adversarial_target=target,
            completion_scorer=completion_scorer,
            max_generated_target_length=3,
        )


@pytest.mark.parametrize("maximum_length", [0, -1, True, 1.5, "256", None])
async def test_create_config_rejects_invalid_maximum_length(maximum_length: Any) -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value="goal", metadata={"gcg_target": "Provided"})])

    with pytest.raises(ValueError, match="must be positive"):
        await create_gcg_data_config_async(dataset=dataset, max_generated_target_length=maximum_length)


async def test_create_config_requires_scorer_before_generating() -> None:
    target = _mock_adversarial_target("Generated")

    with pytest.raises(ValueError, match="completion_scorer is required"):
        await create_gcg_data_config_async(
            dataset=SeedDataset(seeds=[SeedObjective(value="goal")]), adversarial_target=target
        )

    target.send_prompt_async.assert_not_awaited()


@pytest.mark.parametrize("target_metadata", [False, 1, [], {"wrong": "schema"}])
async def test_create_config_rejects_nonstring_metadata(target_metadata: object) -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value="goal", metadata={"gcg_target": target_metadata})])
    target = _mock_adversarial_target("Generated")

    with pytest.raises(ValueError, match="must be a string"):
        await create_gcg_data_config_async(dataset=dataset, adversarial_target=target)

    target.send_prompt_async.assert_not_awaited()


async def test_create_config_rejects_blank_goal() -> None:
    dataset = SeedDataset(seeds=[SeedObjective(value=" ", metadata={"gcg_target": "Provided"})])

    with pytest.raises(ValueError, match="Seed goal must be a nonblank string"):
        await create_gcg_data_config_async(dataset=dataset)


async def test_create_config_generates_for_none_metadata(completion_scorer: MagicMock) -> None:
    config = await create_gcg_data_config_async(
        dataset=SeedDataset(seeds=[SeedObjective(value="goal", metadata={"gcg_target": None})]),
        adversarial_target=_mock_adversarial_target("Prefix"),
        completion_scorer=completion_scorer,
    )

    assert config.train_targets == ["Prefix"]


@pytest.mark.parametrize(
    ("role", "data_type", "error"),
    [
        ("assistant", "error", "blocked"),
        ("assistant", "text", "processing"),
        ("assistant", "function_call", "none"),
        ("tool", "text", "none"),
        ("user", "text", "none"),
    ],
)
async def test_create_config_rejects_invalid_final_response(
    role: str, data_type: str, error: str, completion_scorer: MagicMock
) -> None:
    piece = MessagePiece.model_validate(
        {"role": role, "original_value": "Response", "original_value_data_type": data_type, "response_error": error}
    )
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock(return_value=[piece.to_message()])

    with pytest.raises(ValueError, match="error-free assistant text"):
        await create_gcg_data_config_async(
            dataset=SeedDataset(seeds=[SeedObjective(value="goal")]),
            adversarial_target=target,
            completion_scorer=completion_scorer,
        )

    completion_scorer.score_text_async.assert_not_awaited()


async def test_create_config_uses_final_text_after_tool_interaction(completion_scorer: MagicMock) -> None:
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock(
        return_value=[
            MessagePiece(role="assistant", original_value="{}", original_value_data_type="function_call").to_message(),
            Message.from_prompt(prompt="Tool output", role="tool"),
            Message.from_prompt(prompt="Final prefix", role="assistant"),
        ]
    )

    config = await create_gcg_data_config_async(
        dataset=SeedDataset(seeds=[SeedObjective(value="goal")]),
        adversarial_target=target,
        completion_scorer=completion_scorer,
    )

    assert config.train_targets == ["Final prefix"]
    assert completion_scorer.score_text_async.await_args.args == ("Final prefix",)


async def test_create_config_rejects_multiple_final_pieces(completion_scorer: MagicMock) -> None:
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock(
        return_value=[
            Message(
                message_pieces=[
                    MessagePiece(role="assistant", original_value="First"),
                    MessagePiece(role="assistant", original_value="Second"),
                ]
            )
        ]
    )

    with pytest.raises(ValueError, match="single text piece"):
        await create_gcg_data_config_async(
            dataset=SeedDataset(seeds=[SeedObjective(value="goal")]),
            adversarial_target=target,
            completion_scorer=completion_scorer,
        )


async def test_create_config_rejects_refusal_via_scorer(completion_scorer: MagicMock) -> None:
    completion_scorer.score_text_async.return_value[0].score_value = "false"

    with pytest.raises(ValueError, match="rejected by completion_scorer"):
        await create_gcg_data_config_async(
            dataset=SeedDataset(seeds=[SeedObjective(value="goal")]),
            adversarial_target=_mock_adversarial_target("I cannot help with that."),
            completion_scorer=completion_scorer,
        )


@pytest.mark.parametrize("score_count", [0, 2])
async def test_create_config_rejects_invalid_scorer_result(score_count: int, completion_scorer: MagicMock) -> None:
    completion_scorer.score_text_async.return_value *= score_count

    with pytest.raises(ValueError, match="exactly one true/false score"):
        await create_gcg_data_config_async(
            dataset=SeedDataset(seeds=[SeedObjective(value="goal")]),
            adversarial_target=_mock_adversarial_target("Prefix"),
            completion_scorer=completion_scorer,
        )


async def test_create_config_renders_custom_prompt(completion_scorer: MagicMock) -> None:
    target = _mock_adversarial_target("Prefix")

    config = await create_gcg_data_config_async(
        dataset=SeedDataset(seeds=[SeedObjective(value="goal")]),
        adversarial_target=target,
        completion_scorer=completion_scorer,
        completion_prompt=SeedPrompt(
            value="Custom: {{ objective }} (limit {{ max_length }})",
            parameters=["objective", "max_length"],
            data_type="text",
        ),
        max_generated_target_length=6,
    )

    assert target.send_prompt_async.await_args.kwargs["message"].get_value() == "Custom: goal (limit 6)"
    assert config.train_targets == ["Prefix"]


@pytest.mark.parametrize("prompt_value", [" ", "{{ missing_variable }}"])
async def test_create_config_rejects_invalid_prompt_before_sending(
    prompt_value: str, completion_scorer: MagicMock
) -> None:
    target = _mock_adversarial_target("Prefix")

    with pytest.raises(ValueError, match="nonblank string|Error rendering template"):
        await create_gcg_data_config_async(
            dataset=SeedDataset(seeds=[SeedObjective(value="goal")]),
            adversarial_target=target,
            completion_scorer=completion_scorer,
            completion_prompt=SeedPrompt(value=prompt_value, data_type="text"),
        )

    target.send_prompt_async.assert_not_awaited()


async def test_create_config_propagates_scoring_failure(completion_scorer: MagicMock) -> None:
    completion_scorer.score_text_async.side_effect = RuntimeError("Scoring failed")

    with pytest.raises(RuntimeError, match="Scoring failed"):
        await create_gcg_data_config_async(
            dataset=SeedDataset(seeds=[SeedObjective(value="goal")]),
            adversarial_target=_mock_adversarial_target("Prefix"),
            completion_scorer=completion_scorer,
        )
