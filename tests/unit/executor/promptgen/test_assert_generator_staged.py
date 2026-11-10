# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import math
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from unit.assert_mocks import (
    assert_generation_response_async,
    make_assert_checkpoint,
    make_assert_config,
    make_assert_generation_target,
    make_assert_group_content,
    make_assert_taxonomy,
)

from pyrit.executor.promptgen import AssertGenerator, AssertGeneratorResult
from pyrit.models import (
    AssertDimension,
    AssertGenerationCheckpoint,
    AssertGenerationConfig,
    AssertGenerationStage,
    AssertGroupMetadata,
    AssertTaxonomy,
    Message,
    PromptGenerationEstimate,
)

_POLICY_STAGES = ["systematization", "taxonomy", "dimensions"]


@pytest.fixture
def no_retry_wait() -> Iterator[None]:
    with patch.dict(
        "os.environ",
        {"RETRY_MAX_NUM_ATTEMPTS": "2", "RETRY_WAIT_MIN_SECONDS": "0", "RETRY_WAIT_MAX_SECONDS": "0"},
    ):
        yield


def _described_config(**overrides: Any) -> AssertGenerationConfig:
    return make_assert_config(dimensions=[{"name": "channel", "description": "Where the user asks."}], **overrides)


def _sent_stages(send: Mock) -> list[str]:
    return [
        sent.kwargs["normalized_conversation"][-1].get_piece().prompt_metadata["assert_stage"]
        for sent in send.call_args_list
    ]


def _sent_payloads(send: Mock, *, stage: str) -> list[dict[str, Any]]:
    requests = [sent.kwargs["normalized_conversation"][-1].get_piece() for sent in send.call_args_list]
    return [
        json.loads(request.converted_value) for request in requests if request.prompt_metadata["assert_stage"] == stage
    ]


def _assignments(result: AssertGeneratorResult) -> list[dict[str, str]]:
    return [AssertGroupMetadata.from_metadata(group.objective.metadata).assignment for group in result.seed_groups]


def _saved_checkpoints(on_checkpoint: AsyncMock) -> list[AssertGenerationCheckpoint]:
    return [saved.args[0] for saved in on_checkpoint.await_args_list]


def _reviewed_taxonomy() -> AssertTaxonomy:
    data = make_assert_taxonomy().model_dump(mode="json")
    data["behavior_categories"][0]["definition"] = "Explains the published return and exchange policy."
    data["behavior_categories"][1]["permissible"] = True
    return AssertTaxonomy.model_validate(data)


def _reviewed_dimensions(dimensions: tuple[AssertDimension, ...]) -> list[AssertDimension]:
    data = [dimension.model_dump(mode="json") for dimension in dimensions]
    data[0]["levels"][1]["definition"] = "A reviewer-written application condition."
    return [AssertDimension.model_validate(item) for item in data]


async def _advance_async(
    *, generator: AssertGenerator, target: Any, checkpoint: AssertGenerationCheckpoint, **kwargs: Any
) -> tuple[AssertGenerationCheckpoint, list[str]]:
    with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async) as send:
        advanced = await generator.advance_async(checkpoint=checkpoint, **kwargs)
    return advanced, _sent_stages(send)


@pytest.mark.usefixtures("patch_central_database", "no_retry_wait")
class TestAdvance:
    @pytest.mark.parametrize(
        "stop_after, call_count, expected_next",
        [
            (AssertGenerationStage.SYSTEMATIZATION, 1, AssertGenerationStage.TAXONOMY),
            (AssertGenerationStage.TAXONOMY, 2, AssertGenerationStage.DIMENSIONS),
            (AssertGenerationStage.DIMENSIONS, 3, AssertGenerationStage.SEED_GROUPS),
            (AssertGenerationStage.SEED_GROUPS, 9, None),
            (None, 9, None),
        ],
    )
    async def test_advance_async_stops_after_requested_stage_async(
        self, stop_after: AssertGenerationStage | None, call_count: int, expected_next: AssertGenerationStage | None
    ) -> None:
        target = make_assert_generation_target()
        checkpoint, stages = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=_described_config()),
            stop_after=stop_after,
        )
        assert stages == (_POLICY_STAGES + ["seed_groups"] * 6)[:call_count]
        assert checkpoint.next_stage == expected_next
        assert len(checkpoint.provenance.conversation_ids) == min(call_count, 3)
        assert len(checkpoint.groups) == (6 if expected_next is None else 0)
        assert checkpoint.completed_batches == (6 if expected_next is None else 0)

    async def test_advance_async_explicit_dimensions_complete_without_a_call_async(self) -> None:
        target = make_assert_generation_target()
        config = make_assert_config()
        checkpoint, stages = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=config),
            stop_after=AssertGenerationStage.DIMENSIONS,
        )
        assert stages == ["systematization", "taxonomy"]
        assert checkpoint.dimensions == config.dimensions
        assert checkpoint.provenance.dimensions_conversation_id is None
        assert checkpoint.next_stage == AssertGenerationStage.SEED_GROUPS

    async def test_advance_async_complete_checkpoint_makes_no_calls_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        complete, _ = await _advance_async(
            generator=generator, target=target, checkpoint=AssertGenerationCheckpoint(config=make_assert_config())
        )
        on_checkpoint = AsyncMock()
        again, stages = await _advance_async(
            generator=generator, target=target, checkpoint=complete, on_checkpoint=on_checkpoint
        )
        assert stages == []
        assert again == complete
        on_checkpoint.assert_not_awaited()

    @pytest.mark.parametrize(
        "stop_after",
        [AssertGenerationStage.SYSTEMATIZATION, AssertGenerationStage.TAXONOMY, AssertGenerationStage.DIMENSIONS],
    )
    async def test_advance_async_checkpoint_past_stop_after_is_unchanged_async(
        self, stop_after: AssertGenerationStage
    ) -> None:
        target = make_assert_generation_target()
        checkpoint = make_assert_checkpoint()
        on_checkpoint = AsyncMock()
        advanced, stages = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=checkpoint,
            stop_after=stop_after,
            on_checkpoint=on_checkpoint,
        )
        assert stages == []
        assert advanced == checkpoint
        on_checkpoint.assert_not_awaited()

    async def test_advance_async_partial_batches_past_stop_after_are_unchanged_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        on_checkpoint = AsyncMock()
        await _advance_async(
            generator=generator,
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config()),
            on_checkpoint=on_checkpoint,
        )
        partial = next(saved for saved in _saved_checkpoints(on_checkpoint) if saved.completed_batches == 1)
        unchanged, skipped = await _advance_async(
            generator=generator, target=target, checkpoint=partial, stop_after=AssertGenerationStage.DIMENSIONS
        )
        resumed, remaining = await _advance_async(
            generator=generator, target=target, checkpoint=partial, stop_after=AssertGenerationStage.SEED_GROUPS
        )
        assert skipped == []
        assert unchanged == partial
        assert remaining == ["seed_groups"] * (resumed.completed_batches - 1)
        assert resumed.next_stage is None

    async def test_advance_async_stops_at_stop_after_when_resuming_mid_policy_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        systematized, _ = await _advance_async(
            generator=generator,
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=_described_config()),
            stop_after=AssertGenerationStage.SYSTEMATIZATION,
        )
        same, none_sent = await _advance_async(
            generator=generator,
            target=target,
            checkpoint=systematized,
            stop_after=AssertGenerationStage.SYSTEMATIZATION,
        )
        policy, sent = await _advance_async(
            generator=generator,
            target=target,
            checkpoint=systematized,
            stop_after=AssertGenerationStage.DIMENSIONS,
        )
        assert none_sent == []
        assert same == systematized
        assert sent == ["taxonomy", "dimensions"]
        assert policy.next_stage == AssertGenerationStage.SEED_GROUPS
        assert (
            policy.provenance.systematization_conversation_id == systematized.provenance.systematization_conversation_id
        )

    @pytest.mark.parametrize(
        "stop_after",
        [AssertGenerationStage.SYSTEMATIZATION, AssertGenerationStage.TAXONOMY, AssertGenerationStage.DIMENSIONS],
    )
    async def test_resumed_generation_matches_one_shot_result_async(self, stop_after: AssertGenerationStage) -> None:
        target = make_assert_generation_target()
        config = _described_config()
        with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async) as send:
            one_shot = await AssertGenerator(target=target).execute_async(config=config)
        one_shot_calls = send.call_count

        stopped, first_stages = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=config),
            stop_after=stop_after,
        )
        stored = AssertGenerationCheckpoint.model_validate_json(stopped.model_dump_json())
        resumed_generator = AssertGenerator(target=target)
        complete, resumed_stages = await _advance_async(generator=resumed_generator, target=target, checkpoint=stored)
        result = resumed_generator.build_result(checkpoint=complete)

        assert first_stages + resumed_stages == _POLICY_STAGES + ["seed_groups"] * 6
        assert len(first_stages) + len(resumed_stages) == one_shot_calls
        assert len(result.seed_groups) == len(one_shot.seed_groups) == config.group_count
        assert result.coverage == one_shot.coverage
        assert _assignments(result) == _assignments(one_shot)
        assert len(result.generation_conversation_ids) == len(one_shot.generation_conversation_ids)
        assert result.generation_conversation_ids[: len(stopped.conversation_ids)] == stopped.conversation_ids
        for group in result.seed_groups:
            provenance = AssertGroupMetadata.from_metadata(group.objective.metadata).provenance
            assert set(provenance.conversation_ids) <= set(result.generation_conversation_ids)
            assert provenance.systematization_conversation_id == stopped.provenance.systematization_conversation_id

    async def test_advance_async_resumes_from_checkpoint_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        stopped, _ = await _advance_async(
            generator=generator,
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=_described_config()),
            stop_after=AssertGenerationStage.TAXONOMY,
        )
        with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async) as send:
            result = generator.build_result(checkpoint=await generator.advance_async(checkpoint=stopped))
        assert _sent_stages(send) == ["dimensions"] + ["seed_groups"] * 6
        assert result.systematization == stopped.systematization
        assert result.taxonomy == stopped.taxonomy
        assert set(stopped.conversation_ids) <= set(result.generation_conversation_ids)

    async def test_on_checkpoint_receives_each_stage_and_batch_async(self) -> None:
        target = make_assert_generation_target()
        on_checkpoint = AsyncMock()
        final, stages = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=_described_config()),
            on_checkpoint=on_checkpoint,
        )
        saved = _saved_checkpoints(on_checkpoint)
        assert len(saved) == len(stages) == 9
        assert [checkpoint.next_stage for checkpoint in saved] == [
            AssertGenerationStage.TAXONOMY,
            AssertGenerationStage.DIMENSIONS,
            *[AssertGenerationStage.SEED_GROUPS] * 6,
            None,
        ]
        assert [checkpoint.completed_batches for checkpoint in saved] == [0, 0, 0, 1, 2, 3, 4, 5, 6]
        for previous, current in zip(saved, saved[1:], strict=False):
            assert current.groups[: len(previous.groups)] == previous.groups
            assert len(current.groups) >= len(previous.groups)
        assert saved[-1] == final

    async def test_seed_group_batches_share_one_assignment_and_hold_at_most_five_groups_async(self) -> None:
        target = make_assert_generation_target()
        on_checkpoint = AsyncMock()
        config = make_assert_config(group_count=24)
        final, stages = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=config),
            on_checkpoint=on_checkpoint,
        )
        batches = [checkpoint for checkpoint in _saved_checkpoints(on_checkpoint) if checkpoint.completed_batches]
        assert stages.count("seed_groups") == len(batches) == final.completed_batches == 8
        previous_count = 0
        for checkpoint in batches:
            batch = checkpoint.groups[previous_count:]
            previous_count = len(checkpoint.groups)
            assert 1 <= len(batch) <= 5
            assert len({json.dumps(group.assignment, sort_keys=True) for group in batch}) == 1
            assert len({group.conversation_id for group in batch}) == 1
        assert [group.group_id for group in final.groups] == [f"group_{index:06d}" for index in range(24)]

    async def test_interrupted_batches_resume_without_duplicates_async(self) -> None:
        target = make_assert_generation_target()
        on_checkpoint = AsyncMock()
        batch_calls = 0

        async def fail_second_batch_async(*, normalized_conversation: list[Message]) -> list[Message]:
            nonlocal batch_calls
            if normalized_conversation[-1].get_piece().prompt_metadata["assert_stage"] == "seed_groups":
                batch_calls += 1
                if batch_calls == 2:
                    raise ConnectionError("Connection dropped")
            return await assert_generation_response_async(normalized_conversation=normalized_conversation)

        with patch.object(target, "_send_prompt_to_target_async", side_effect=fail_second_batch_async):
            with pytest.raises(Exception, match="Error sending prompt"):
                await AssertGenerator(target=target).advance_async(
                    checkpoint=AssertGenerationCheckpoint(config=_described_config()), on_checkpoint=on_checkpoint
                )
        last = _saved_checkpoints(on_checkpoint)[-1]
        assert last.completed_batches == 1
        assert len(last.groups) == 1

        generator = AssertGenerator(target=target)
        complete, stages = await _advance_async(generator=generator, target=target, checkpoint=last)
        assert stages == ["seed_groups"] * 5
        assert complete.groups[0] == last.groups[0]
        assert [group.group_id for group in complete.groups] == [f"group_{index:06d}" for index in range(6)]
        assert len({group.conversation_id for group in complete.groups}) == 6
        assert len(generator.build_result(checkpoint=complete).seed_groups) == 6


@pytest.mark.usefixtures("patch_central_database", "no_retry_wait")
class TestReviewedPolicy:
    async def test_reviewed_policy_flows_into_every_seed_group_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        stopped, _ = await _advance_async(
            generator=generator,
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=_described_config()),
            stop_after=AssertGenerationStage.DIMENSIONS,
        )
        assert stopped.dimensions is not None
        taxonomy = _reviewed_taxonomy()
        dimensions = tuple(_reviewed_dimensions(stopped.dimensions))
        reviewed = stopped.with_reviewed_policy(taxonomy=taxonomy, dimensions=dimensions)

        with patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async) as send:
            complete = await generator.advance_async(checkpoint=reviewed)
        result = generator.build_result(checkpoint=complete)

        assert set(_sent_stages(send)) == {"seed_groups"}
        for payload in _sent_payloads(send, stage="seed_groups"):
            assert payload["taxonomy"] == taxonomy.model_dump(mode="json")
            assert payload["dimensions"] == [item.model_dump(mode="json") for item in dimensions]
        assert result.config == reviewed.config
        assert result.taxonomy == taxonomy
        assert len(result.seed_groups) == 6
        for group in result.seed_groups:
            metadata = AssertGroupMetadata.from_metadata(group.objective.metadata)
            assert metadata.taxonomy == taxonomy
            assert metadata.dimensions == dimensions
            assert metadata.config.dimensions == dimensions
            assert all(dimension.levels for dimension in metadata.config.dimensions)
            assert metadata.provenance.edited_stages == (
                AssertGenerationStage.TAXONOMY,
                AssertGenerationStage.DIMENSIONS,
            )
            assert metadata.provenance.dimensions_conversation_id == stopped.provenance.dimensions_conversation_id

    async def test_unreviewed_policy_records_no_edits_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        complete, _ = await _advance_async(
            generator=generator, target=target, checkpoint=AssertGenerationCheckpoint(config=make_assert_config())
        )
        for group in generator.build_result(checkpoint=complete).seed_groups:
            assert AssertGroupMetadata.from_metadata(group.objective.metadata).provenance.edited_stages == ()


@pytest.mark.usefixtures("patch_central_database", "no_retry_wait")
class TestCheckpointRejection:
    @pytest.mark.parametrize("group_count, completed_batches", [(0, 1), (1, 0), (0, 5)])
    async def test_inconsistent_checkpoint_batches_are_rejected_async(
        self, group_count: int, completed_batches: int
    ) -> None:
        target = make_assert_generation_target()
        groups = tuple(make_assert_group_content(index=index) for index in range(group_count))
        checkpoint = make_assert_checkpoint(groups=groups, completed_batches=completed_batches)
        with patch.object(target, "_send_prompt_to_target_async") as send:
            with pytest.raises(ValueError, match="don't match the planned coverage assignments"):
                await AssertGenerator(target=target).advance_async(checkpoint=checkpoint)
        send.assert_not_called()

    @pytest.mark.parametrize(
        "checkpoint, next_stage",
        [
            (AssertGenerationCheckpoint(config=make_assert_config()), "systematization"),
            (make_assert_checkpoint(), "seed_groups"),
            (make_assert_checkpoint(groups=(make_assert_group_content(),)), "seed_groups"),
        ],
    )
    def test_build_result_rejects_incomplete_checkpoint(
        self, checkpoint: AssertGenerationCheckpoint, next_stage: str
    ) -> None:
        generator = AssertGenerator(target=make_assert_generation_target())
        with pytest.raises(ValueError, match=f"incomplete; the next stage is '{next_stage}'"):
            generator.build_result(checkpoint=checkpoint)

    def test_build_result_packages_complete_checkpoint_without_calls(self) -> None:
        target = make_assert_generation_target()
        groups = (
            make_assert_group_content(index=0),
            make_assert_group_content(index=1, assignment={"behavior": "private_disclosure", "channel": "forum"}),
        )
        checkpoint = make_assert_checkpoint(
            config=make_assert_config(group_count=2), groups=groups, completed_batches=2
        )
        with patch.object(target, "_send_prompt_to_target_async") as send:
            result = AssertGenerator(target=target).build_result(checkpoint=checkpoint)
        send.assert_not_called()
        assert [group.objective.value for group in result.seed_groups] == [group.objective for group in groups]
        assert result.generation_conversation_ids == checkpoint.conversation_ids
        assert set(result.generation_targets) == {AssertGenerator.GENERATION_ROLE, AssertGenerator.RESEARCH_ROLE}
        assert result.coverage.sample_size == 2


@pytest.mark.usefixtures("patch_central_database")
class TestValidateConfig:
    def test_validate_config_rejects_web_search_without_search_target(self) -> None:
        generator = AssertGenerator(target=make_assert_generation_target())
        with pytest.raises(ValueError, match="supports_web_search"):
            generator.validate_config(config=make_assert_config(web_search=True))

    def test_validate_config_accepts_web_search_on_search_research_target(self) -> None:
        generator = AssertGenerator(
            target=make_assert_generation_target(), research_target=make_assert_generation_target(web_search=True)
        )
        generator.validate_config(config=make_assert_config(web_search=True))
        generator.validate_config(config=make_assert_config())

    async def test_advance_async_validates_config_before_calls_async(self) -> None:
        target = make_assert_generation_target()
        with patch.object(target, "_send_prompt_to_target_async") as send:
            with pytest.raises(ValueError, match="supports_web_search"):
                await AssertGenerator(target=target).advance_async(
                    checkpoint=AssertGenerationCheckpoint(config=make_assert_config(web_search=True))
                )
        send.assert_not_called()


def _stage_names(checkpoint: AssertGenerationCheckpoint) -> list[str]:
    return [stage.stage for stage in AssertGenerator.estimate(checkpoint=checkpoint).stages]


@pytest.mark.parametrize("group_count", [1, 5, 6, 24])
def test_estimate_new_request_bounds_seed_group_calls(group_count: int) -> None:
    estimate = AssertGenerator.estimate(
        checkpoint=AssertGenerationCheckpoint(config=make_assert_config(group_count=group_count))
    )
    stages = {stage.stage: stage for stage in estimate.stages}
    assert list(stages) == ["systematization", "taxonomy", "seed_groups"]
    assert not estimate.exact_calls
    assert stages["seed_groups"].min_calls == math.ceil(group_count / 5)
    assert stages["seed_groups"].max_calls == group_count
    assert estimate.min_calls == 2 + math.ceil(group_count / 5)
    assert estimate.max_calls == 2 + group_count
    assert stages["systematization"].target_role == AssertGenerator.RESEARCH_ROLE
    assert stages["taxonomy"].target_role == AssertGenerator.GENERATION_ROLE
    assert stages["seed_groups"].target_role == AssertGenerator.GENERATION_ROLE
    assert 0 < estimate.low_tokens < estimate.high_tokens


@pytest.mark.parametrize(
    "dimensions, includes_dimensions",
    [
        ([{"name": "channel", "description": "Where the user asks."}], True),
        (None, False),
        ([], False),
    ],
)
def test_estimate_includes_dimensions_only_when_levels_are_generated(
    dimensions: list[dict[str, str]] | None, includes_dimensions: bool
) -> None:
    config = make_assert_config(**({"dimensions": dimensions} if dimensions is not None else {}))
    stages = AssertGenerator.estimate(checkpoint=AssertGenerationCheckpoint(config=config)).stages
    names = [stage.stage for stage in stages]
    assert ("dimensions" in names) == includes_dimensions
    if includes_dimensions:
        assert names == ["systematization", "taxonomy", "dimensions", "seed_groups"]
        assert stages[2].target_role == AssertGenerator.RESEARCH_ROLE


def test_estimate_notes_web_search() -> None:
    plain = AssertGenerator.estimate(checkpoint=AssertGenerationCheckpoint(config=make_assert_config()))
    searched = AssertGenerator.estimate(
        checkpoint=AssertGenerationCheckpoint(config=make_assert_config(web_search=True))
    )
    assert not any("Web search" in note for note in plain.notes)
    assert any("Web search" in note for note in searched.notes)
    assert searched.stages == plain.stages


def test_estimate_skips_completed_policy_stages() -> None:
    checkpoint = make_assert_checkpoint()
    assert _stage_names(checkpoint) == ["seed_groups"]
    partial = AssertGenerationCheckpoint(
        config=checkpoint.config, systematization=checkpoint.systematization, provenance=checkpoint.provenance
    )
    assert _stage_names(partial) == ["taxonomy", "seed_groups"]


def test_estimate_complete_checkpoint_needs_no_calls() -> None:
    groups = (make_assert_group_content(index=0), make_assert_group_content(index=1))
    checkpoint = make_assert_checkpoint(config=make_assert_config(group_count=2), groups=groups, completed_batches=2)
    estimate = AssertGenerator.estimate(checkpoint=checkpoint)
    assert estimate.stages == ()
    assert estimate.exact_calls
    assert (estimate.min_calls, estimate.max_calls, estimate.low_tokens, estimate.high_tokens) == (0, 0, 0, 0)


@pytest.mark.usefixtures("patch_central_database", "no_retry_wait")
class TestEstimateMatchesGeneration:
    @pytest.mark.parametrize(
        "config",
        [make_assert_config(), make_assert_config(group_count=24), _described_config()],
    )
    async def test_estimate_after_policy_is_exact_async(self, config: AssertGenerationConfig) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        policy, _ = await _advance_async(
            generator=generator,
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=config),
            stop_after=AssertGenerationStage.DIMENSIONS,
        )
        estimate = AssertGenerator.estimate(checkpoint=policy)
        _, stages = await _advance_async(generator=generator, target=target, checkpoint=policy)
        assert estimate.exact_calls
        assert [stage.stage for stage in estimate.stages] == ["seed_groups"]
        assert estimate.min_calls == estimate.max_calls == len(stages)
        assert set(stages) == {"seed_groups"}

    async def test_estimate_of_partial_batches_counts_remaining_calls_async(self) -> None:
        target = make_assert_generation_target()
        on_checkpoint = AsyncMock()
        final, stages = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config(group_count=12)),
            on_checkpoint=on_checkpoint,
        )
        total_batches = stages.count("seed_groups")
        for checkpoint in _saved_checkpoints(on_checkpoint):
            if checkpoint.next_stage != AssertGenerationStage.SEED_GROUPS:
                continue
            estimate = AssertGenerator.estimate(checkpoint=checkpoint)
            assert estimate.exact_calls
            assert estimate.max_calls == total_batches - checkpoint.completed_batches
        assert AssertGenerator.estimate(checkpoint=final).max_calls == 0


@pytest.mark.parametrize(
    "checkpoint", [AssertGenerationCheckpoint(config=_described_config(web_search=True)), make_assert_checkpoint()]
)
def test_estimate_round_trips_through_json(checkpoint: AssertGenerationCheckpoint) -> None:
    estimate = AssertGenerator.estimate(checkpoint=checkpoint)
    assert PromptGenerationEstimate.model_validate_json(estimate.model_dump_json()) == estimate
