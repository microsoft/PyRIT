# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import math
import re
from collections.abc import Callable, Iterator
from itertools import product
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from unit.assert_mocks import (
    assert_generation_response_async,
    make_assert_checkpoint,
    make_assert_config,
    make_assert_generation_target,
    make_assert_group_content,
    make_assert_systematization,
    make_assert_taxonomy,
)

from pyrit.executor.core import StrategyEvent
from pyrit.executor.promptgen import AssertGenerator, AssertGeneratorResult
from pyrit.models import (
    AssertDimension,
    AssertGenerationCheckpoint,
    AssertGenerationConfig,
    AssertGenerationStage,
    AssertGroupMetadata,
    AssertSystematization,
    AssertTaxonomy,
    ComponentIdentifier,
    Message,
    PromptGenerationEstimate,
    TargetIdentifier,
)
from pyrit.prompt_target import OpenAIResponseTarget, PromptTarget, RoundRobinTarget

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
            assert metadata.config.taxonomy == taxonomy
            assert metadata.config.dimensions == dimensions
            assert all(dimension.levels for dimension in metadata.config.dimensions)
            assert metadata.provenance.edited_stages == (
                AssertGenerationStage.TAXONOMY,
                AssertGenerationStage.DIMENSIONS,
            )
            assert metadata.provenance.dimensions_conversation_id == stopped.provenance.dimensions_conversation_id

    async def test_rerun_from_a_stored_group_request_reproduces_the_reviewed_policy_async(self) -> None:
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
        reviewed, _ = await _advance_async(
            generator=generator,
            target=target,
            checkpoint=stopped.with_reviewed_policy(taxonomy=taxonomy, dimensions=dimensions),
        )
        stored = generator.build_result(checkpoint=reviewed).seed_groups[0].objective.metadata
        stored_config = AssertGroupMetadata.from_metadata(stored).config

        rerun, stages = await _advance_async(
            generator=generator, target=target, checkpoint=AssertGenerationCheckpoint(config=stored_config)
        )
        result = generator.build_result(checkpoint=rerun)

        assert set(stages) == {"seed_groups"}
        assert (result.taxonomy, result.dimensions, result.systematization) == (taxonomy, dimensions, None)
        assert set(rerun.targets) == {AssertGenerator.GENERATION_ROLE}
        for group in result.seed_groups:
            metadata = AssertGroupMetadata.from_metadata(group.objective.metadata)
            assert (metadata.config, metadata.taxonomy, metadata.dimensions) == (stored_config, taxonomy, dimensions)
            assert metadata.provenance.edited_stages == ()
            assert metadata.provenance.conversation_ids == (metadata.provenance.group_generation_conversation_id,)

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
        assert result.generation_targets == {}
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

    async def test_resume_without_research_stages_accepts_a_research_target_without_web_search_async(self) -> None:
        generation, stopped = await _stop_web_search_generation_async(
            config=make_assert_config(web_search=True), stop_after=AssertGenerationStage.DIMENSIONS
        )
        with patch.object(
            generation, "_send_prompt_to_target_async", side_effect=assert_generation_response_async
        ) as send:
            complete = await AssertGenerator(target=generation).advance_async(checkpoint=stopped)
        assert set(_sent_stages(send)) == {"seed_groups"}
        assert complete.next_stage is None

    async def test_resume_with_research_stages_left_rejects_a_research_target_without_web_search_async(
        self,
    ) -> None:
        generation, stopped = await _stop_web_search_generation_async(
            config=_described_config(web_search=True), stop_after=AssertGenerationStage.TAXONOMY
        )
        with (
            patch.object(generation, "_send_prompt_to_target_async") as send,
            pytest.raises(ValueError, match="supports_web_search"),
        ):
            await AssertGenerator(target=generation).advance_async(checkpoint=stopped)
        send.assert_not_called()


async def _stop_web_search_generation_async(
    *, config: AssertGenerationConfig, stop_after: AssertGenerationStage
) -> tuple[Any, AssertGenerationCheckpoint]:
    generation, research = make_assert_generation_target(), make_assert_generation_target(web_search=True)
    with (
        patch.object(generation, "_send_prompt_to_target_async", side_effect=assert_generation_response_async),
        patch.object(research, "_send_prompt_to_target_async", side_effect=assert_generation_response_async),
    ):
        stopped = await AssertGenerator(target=generation, research_target=research).advance_async(
            checkpoint=AssertGenerationCheckpoint(config=config), stop_after=stop_after
        )
    return generation, stopped


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
    assert [(stage.stage, stage.min_calls, stage.max_calls) for stage in searched.stages] == [
        (stage.stage, stage.min_calls, stage.max_calls) for stage in plain.stages
    ]


@pytest.mark.parametrize(
    "checkpoint",
    [
        AssertGenerationCheckpoint(config=make_assert_config(web_search=True, taxonomy=make_assert_taxonomy())),
        make_assert_checkpoint(config=make_assert_config(web_search=True)),
    ],
)
def test_estimate_omits_the_web_search_note_without_research_stages(checkpoint: AssertGenerationCheckpoint) -> None:
    estimate = AssertGenerator.estimate(checkpoint=checkpoint)
    assert {stage.target_role for stage in estimate.stages} == {AssertGenerator.GENERATION_ROLE}
    assert not any("Web search" in note for note in estimate.notes)


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

    async def test_estimate_with_a_supplied_policy_is_exact_before_any_call_async(self) -> None:
        target = make_assert_generation_target()
        checkpoint = AssertGenerationCheckpoint(
            config=make_assert_config(taxonomy=make_assert_taxonomy(), group_count=24)
        )
        estimate = AssertGenerator.estimate(checkpoint=checkpoint)
        _, stages = await _advance_async(generator=AssertGenerator(target=target), target=target, checkpoint=checkpoint)
        assert estimate.exact_calls
        assert [stage.stage for stage in estimate.stages] == ["seed_groups"]
        assert estimate.min_calls == estimate.max_calls == len(stages)


@pytest.mark.parametrize(
    "checkpoint", [AssertGenerationCheckpoint(config=_described_config(web_search=True)), make_assert_checkpoint()]
)
def test_estimate_round_trips_through_json(checkpoint: AssertGenerationCheckpoint) -> None:
    estimate = AssertGenerator.estimate(checkpoint=checkpoint)
    assert PromptGenerationEstimate.model_validate_json(estimate.model_dump_json()) == estimate


_OTHER_TARGET = ComponentIdentifier(class_name="OtherTarget", class_module="tests.other", params={"model_name": "x"})


@pytest.mark.usefixtures("patch_central_database", "no_retry_wait")
class TestCheckpointIntegrity:
    async def test_resume_rejects_a_different_producer_before_calls_async(self) -> None:
        target = make_assert_generation_target()
        stopped, _ = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=_described_config()),
            stop_after=AssertGenerationStage.TAXONOMY,
        )
        assert set(stopped.targets) == {AssertGenerator.RESEARCH_ROLE, AssertGenerator.GENERATION_ROLE}

        other = make_assert_generation_target()
        with (
            patch.object(other, "get_identifier", return_value=_OTHER_TARGET),
            patch.object(other, "_send_prompt_to_target_async") as send,
            pytest.raises(ValueError, match="research work was produced by a different target"),
        ):
            await AssertGenerator(target=other).advance_async(checkpoint=stopped)
        send.assert_not_called()

    async def test_resume_with_an_equivalent_target_continues_async(self) -> None:
        target = make_assert_generation_target()
        stopped, _ = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config()),
            stop_after=AssertGenerationStage.DIMENSIONS,
        )
        equivalent = make_assert_generation_target()
        complete, stages = await _advance_async(
            generator=AssertGenerator(target=equivalent), target=equivalent, checkpoint=stopped
        )
        assert set(stages) == {"seed_groups"}
        assert complete.next_stage is None

    async def test_build_result_attributes_work_to_the_recorded_producers_async(self) -> None:
        target = make_assert_generation_target()
        complete, _ = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config()),
        )
        packager = make_assert_generation_target()
        with patch.object(packager, "get_identifier", return_value=_OTHER_TARGET):
            result = AssertGenerator(target=packager).build_result(checkpoint=complete)
        assert result.generation_targets == {
            role: identifier.model_dump(mode="json") for role, identifier in complete.targets.items()
        }
        assert result.generation_targets[AssertGenerator.GENERATION_ROLE]["hash"] == target.get_identifier().hash

    async def test_resume_rejects_groups_with_unplanned_assignments_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        on_checkpoint = AsyncMock()
        await _advance_async(
            generator=generator,
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config(group_count=12)),
            on_checkpoint=on_checkpoint,
        )
        partial = next(saved for saved in _saved_checkpoints(on_checkpoint) if saved.completed_batches == 1)
        assert partial.taxonomy is not None and partial.dimensions is not None
        first = partial.groups[0]
        levels = partial.taxonomy.get_coverage_levels(partial.dimensions)
        other = next(
            assignment
            for assignment in (dict(zip(levels, values, strict=True)) for values in product(*levels.values()))
            if assignment != first.assignment
        )
        altered = AssertGenerationCheckpoint.model_validate(
            {
                **partial.model_dump(),
                "groups": [{**first.model_dump(), "assignment": other}, *[g.model_dump() for g in partial.groups[1:]]],
            }
        )

        with (
            patch.object(target, "_send_prompt_to_target_async") as send,
            pytest.raises(ValueError, match="don't match the planned coverage assignments"),
        ):
            await generator.advance_async(checkpoint=altered)
        send.assert_not_called()

    def test_checkpoint_rejects_another_format_version(self) -> None:
        data = AssertGenerationCheckpoint(config=make_assert_config()).model_dump(mode="json")
        assert data["schema_version"] == AssertGenerationCheckpoint.SCHEMA_VERSION
        with pytest.raises(ValueError, match="format version 2.*Start a new generation"):
            AssertGenerationCheckpoint.model_validate({**data, "schema_version": 2})

    async def test_only_execute_async_emits_strategy_events_async(self) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        with (
            patch.object(target, "_send_prompt_to_target_async", side_effect=assert_generation_response_async),
            patch.object(generator, "_handle_event_async", new_callable=AsyncMock) as events,
        ):
            await generator.advance_async(checkpoint=AssertGenerationCheckpoint(config=make_assert_config()))
            advanced = [call.kwargs["event"] for call in events.await_args_list]
            events.reset_mock()
            await generator.execute_async(config=make_assert_config())
        executed = [call.kwargs["event"] for call in events.await_args_list]
        assert set(advanced) == {
            StrategyEvent.ON_PRE_SETUP,
            StrategyEvent.ON_POST_SETUP,
            StrategyEvent.ON_PRE_TEARDOWN,
            StrategyEvent.ON_POST_TEARDOWN,
        }
        assert {StrategyEvent.ON_PRE_VALIDATE, StrategyEvent.ON_PRE_EXECUTE, StrategyEvent.ON_POST_EXECUTE} <= set(
            executed
        )

    async def test_resume_rejects_a_stored_producer_whose_content_does_not_match_its_hash_async(self) -> None:
        target = make_assert_generation_target()
        stopped, _ = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config()),
            stop_after=AssertGenerationStage.TAXONOMY,
        )
        assert AssertGenerationCheckpoint.model_validate_json(stopped.model_dump_json()) == stopped
        data = stopped.model_dump(mode="json")
        data["targets"][AssertGenerator.GENERATION_ROLE]["class_name"] = "SomeOtherTarget"
        edited = AssertGenerationCheckpoint.model_validate(data)

        with (
            patch.object(target, "_send_prompt_to_target_async") as send,
            pytest.raises(ValueError, match=r"generation work was produced by a different target \(SomeOtherTarget"),
        ):
            await AssertGenerator(target=target).advance_async(checkpoint=edited)
        send.assert_not_called()

    async def test_resume_rejects_stored_group_ids_that_a_remaining_batch_assigns_before_any_call_async(
        self,
    ) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        on_checkpoint = AsyncMock()
        await _advance_async(
            generator=generator,
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config(group_count=12)),
            on_checkpoint=on_checkpoint,
        )
        partial = next(saved for saved in _saved_checkpoints(on_checkpoint) if saved.completed_batches == 1)
        first, *others = partial.groups
        reserved = f"group_{len(partial.groups):06d}"
        renamed = AssertGenerationCheckpoint.model_validate(
            {**partial.model_dump(), "groups": [first.model_copy(update={"group_id": reserved}), *others]}
        )
        rejection = re.escape(f"use IDs ['{reserved}'] that the remaining batches assign")
        with pytest.raises(ValueError, match=rejection):
            AssertGenerator.estimate(checkpoint=renamed)
        saved = AsyncMock()
        with (
            patch.object(target, "_send_prompt_to_target_async") as send,
            pytest.raises(ValueError, match=rejection),
        ):
            await generator.advance_async(checkpoint=renamed, on_checkpoint=saved)
        send.assert_not_called()
        saved.assert_not_awaited()


_RESEARCH_TARGET = ComponentIdentifier(
    class_name="ResearchTarget", class_module="tests.research", params={"model_name": "research"}
)


def _openai_target(**settings: Any) -> OpenAIResponseTarget:
    """Build a real target, whose identifier records its settings as a deployed target's does."""
    return OpenAIResponseTarget(
        **{
            "endpoint": "https://example.org/openai/v1",
            "model_name": "gpt-x",
            "api_key": "test-key",
            "custom_configuration": make_assert_generation_target().configuration,
            **settings,
        }
    )


def _round_robin_target(**settings: Any) -> RoundRobinTarget:
    """Build a round-robin target whose inner targets share the given settings."""
    return RoundRobinTarget(targets=[_openai_target(**settings), _openai_target(**settings)])


@pytest.mark.usefixtures("patch_central_database", "no_retry_wait")
class TestRoleProducers:
    async def test_checkpoint_records_each_role_with_its_own_target_async(self) -> None:
        generation, research = make_assert_generation_target(), make_assert_generation_target()
        with (
            patch.object(research, "get_identifier", return_value=_RESEARCH_TARGET),
            patch.object(
                generation, "_send_prompt_to_target_async", side_effect=assert_generation_response_async
            ) as generation_send,
            patch.object(
                research, "_send_prompt_to_target_async", side_effect=assert_generation_response_async
            ) as research_send,
        ):
            complete = await AssertGenerator(target=generation, research_target=research).advance_async(
                checkpoint=AssertGenerationCheckpoint(config=_described_config())
            )
        assert complete.targets[AssertGenerator.RESEARCH_ROLE].hash == _RESEARCH_TARGET.hash
        assert complete.targets[AssertGenerator.GENERATION_ROLE].hash == generation.get_identifier().hash
        assert _sent_stages(research_send) == ["systematization", "dimensions"]
        assert set(_sent_stages(generation_send)) == {"taxonomy", "seed_groups"}

    @pytest.mark.parametrize(
        "stop_after, changed_role",
        [
            (AssertGenerationStage.SYSTEMATIZATION, AssertGenerator.RESEARCH_ROLE),
            (AssertGenerationStage.TAXONOMY, AssertGenerator.GENERATION_ROLE),
            (AssertGenerationStage.DIMENSIONS, AssertGenerator.GENERATION_ROLE),
        ],
    )
    async def test_resume_rejects_a_changed_role_target_before_any_call_async(
        self, stop_after: AssertGenerationStage, changed_role: str
    ) -> None:
        generation, research, replacement = (make_assert_generation_target() for _ in range(3))
        with (
            patch.object(research, "get_identifier", return_value=_RESEARCH_TARGET),
            patch.object(generation, "_send_prompt_to_target_async", side_effect=assert_generation_response_async),
            patch.object(research, "_send_prompt_to_target_async", side_effect=assert_generation_response_async),
        ):
            stopped = await AssertGenerator(target=generation, research_target=research).advance_async(
                checkpoint=AssertGenerationCheckpoint(config=_described_config()), stop_after=stop_after
            )
        targets = {AssertGenerator.GENERATION_ROLE: generation, AssertGenerator.RESEARCH_ROLE: research}
        targets[changed_role] = replacement
        on_checkpoint = AsyncMock()
        with (
            patch.object(research, "get_identifier", return_value=_RESEARCH_TARGET),
            patch.object(replacement, "get_identifier", return_value=_OTHER_TARGET),
            patch.object(generation, "_send_prompt_to_target_async") as generation_send,
            patch.object(research, "_send_prompt_to_target_async") as research_send,
            patch.object(replacement, "_send_prompt_to_target_async") as replacement_send,
            pytest.raises(ValueError, match=f"{changed_role} work was produced by a different target"),
        ):
            await AssertGenerator(
                target=targets[AssertGenerator.GENERATION_ROLE], research_target=targets[AssertGenerator.RESEARCH_ROLE]
            ).advance_async(checkpoint=stopped, on_checkpoint=on_checkpoint)
        for send in (generation_send, research_send, replacement_send):
            send.assert_not_called()
        on_checkpoint.assert_not_awaited()

    async def test_resume_without_research_stages_accepts_a_new_research_target_async(self) -> None:
        target = make_assert_generation_target()
        stopped, _ = await _advance_async(
            generator=AssertGenerator(target=target),
            target=target,
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config()),
            stop_after=AssertGenerationStage.TAXONOMY,
        )
        research = make_assert_generation_target()
        with (
            patch.object(research, "get_identifier", return_value=_OTHER_TARGET),
            patch.object(research, "_send_prompt_to_target_async") as research_send,
        ):
            complete, stages = await _advance_async(
                generator=AssertGenerator(target=target, research_target=research), target=target, checkpoint=stopped
            )
        research_send.assert_not_called()
        assert set(stages) == {"seed_groups"}
        assert complete.next_stage is None
        assert complete.targets[AssertGenerator.RESEARCH_ROLE] == stopped.targets[AssertGenerator.RESEARCH_ROLE]

    @pytest.mark.parametrize(
        "is_stored",
        [
            pytest.param(lambda saved: saved.next_stage == AssertGenerationStage.TAXONOMY, id="after-systematization"),
            pytest.param(lambda saved: saved.completed_batches == 1, id="after-one-batch"),
        ],
    )
    async def test_resume_with_only_a_changed_rate_limit_continues_async(
        self, is_stored: Callable[[AssertGenerationCheckpoint], bool]
    ) -> None:
        original = _openai_target()
        on_checkpoint = AsyncMock()
        await _advance_async(
            generator=AssertGenerator(target=original),
            target=original,
            checkpoint=AssertGenerationCheckpoint(config=_described_config(group_count=12)),
            on_checkpoint=on_checkpoint,
        )
        saved = next(checkpoint for checkpoint in _saved_checkpoints(on_checkpoint) if is_stored(checkpoint))
        stored = AssertGenerationCheckpoint.model_validate_json(saved.model_dump_json())

        slower = _openai_target(max_requests_per_minute=10)
        complete, stages = await _advance_async(
            generator=AssertGenerator(target=slower), target=slower, checkpoint=stored
        )

        assert complete.next_stage is None
        assert complete.groups[: len(stored.groups)] == stored.groups
        assert stages.count("seed_groups") == complete.completed_batches - stored.completed_batches
        live = TargetIdentifier.from_component_identifier(slower.get_identifier())
        roles = (AssertGenerator.RESEARCH_ROLE, AssertGenerator.GENERATION_ROLE)
        assert complete.targets == {role: stored.targets.get(role, live) for role in roles}

    async def test_resume_with_slower_round_robin_targets_continues_async(self) -> None:
        original = _round_robin_target()
        stopped, _ = await _advance_async(
            generator=AssertGenerator(target=original),
            target=original,
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config()),
            stop_after=AssertGenerationStage.DIMENSIONS,
        )
        stored = AssertGenerationCheckpoint.model_validate_json(stopped.model_dump_json())

        slower = _round_robin_target(max_requests_per_minute=10)
        complete, stages = await _advance_async(
            generator=AssertGenerator(target=slower), target=slower, checkpoint=stored
        )

        assert complete.next_stage is None
        assert set(stages) == {"seed_groups"}
        assert complete.targets == stored.targets

    @pytest.mark.parametrize("build", [_openai_target, _round_robin_target], ids=["direct", "round-robin"])
    @pytest.mark.parametrize("changes", [{"model_name": "gpt-y"}, {"temperature": 0.5}])
    async def test_resume_with_a_changed_generation_setting_is_rejected_before_any_call_async(
        self, build: Callable[..., PromptTarget], changes: dict[str, Any]
    ) -> None:
        original = build()
        stopped, _ = await _advance_async(
            generator=AssertGenerator(target=original),
            target=original,
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config()),
            stop_after=AssertGenerationStage.DIMENSIONS,
        )
        changed = build(max_requests_per_minute=10, **changes)
        with (
            patch.object(changed, "_send_prompt_to_target_async") as send,
            pytest.raises(ValueError, match="generation work was produced by a different target"),
        ):
            await AssertGenerator(target=changed).advance_async(checkpoint=stopped)
        send.assert_not_called()


@pytest.mark.usefixtures("patch_central_database", "no_retry_wait")
class TestSuppliedTaxonomy:
    @pytest.mark.parametrize(
        "config, policy_stages",
        [
            (make_assert_config(taxonomy=make_assert_taxonomy()), []),
            (_described_config(taxonomy=make_assert_taxonomy()), ["dimensions"]),
        ],
    )
    async def test_supplied_taxonomy_skips_the_research_and_taxonomy_calls_async(
        self, config: AssertGenerationConfig, policy_stages: list[str]
    ) -> None:
        target = make_assert_generation_target()
        generator = AssertGenerator(target=target)
        complete, stages = await _advance_async(
            generator=generator, target=target, checkpoint=AssertGenerationCheckpoint(config=config)
        )
        assert stages == policy_stages + ["seed_groups"] * complete.completed_batches
        assert (complete.systematization, complete.taxonomy) == (None, config.taxonomy)
        provenance = complete.provenance
        assert (provenance.systematization_conversation_id, provenance.taxonomy_conversation_id) == (None, None)
        assert generator.build_result(checkpoint=complete).taxonomy == config.taxonomy

    def test_validate_config_skips_the_research_target_when_no_research_stage_runs(self) -> None:
        generator = AssertGenerator(target=make_assert_generation_target())
        generator.validate_config(config=make_assert_config(web_search=True, taxonomy=make_assert_taxonomy()))
        with pytest.raises(ValueError, match="supports_web_search"):
            generator.validate_config(config=_described_config(web_search=True, taxonomy=make_assert_taxonomy()))


def _seed_group_stage(estimate: PromptGenerationEstimate) -> Any:
    return next(stage for stage in estimate.stages if stage.stage == AssertGenerationStage.SEED_GROUPS.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"target_system_prompt": "x" * 40_000},
        {"dimensions": [{"name": "channel", "description": "y" * 40_000}]},
    ],
)
def test_estimate_counts_the_seed_group_payload(changes: dict[str, Any]) -> None:
    described = {"dimensions": [{"name": "channel", "description": "Where the user asks."}]}
    plain = _seed_group_stage(
        AssertGenerator.estimate(checkpoint=AssertGenerationCheckpoint(config=make_assert_config(**described)))
    )
    heavy = _seed_group_stage(
        AssertGenerator.estimate(
            checkpoint=AssertGenerationCheckpoint(config=make_assert_config(**{**described, **changes}))
        )
    )
    added = PromptGenerationEstimate.tokens_from_characters(39_000) * plain.max_calls
    assert heavy.max_tokens - plain.max_tokens >= added


def test_estimate_counts_dimension_descriptions_in_the_dimensions_stage() -> None:
    def dimensions_stage_tokens(description: str) -> int:
        config = make_assert_config(dimensions=[{"name": "channel", "description": description}])
        estimate = AssertGenerator.estimate(checkpoint=AssertGenerationCheckpoint(config=config))
        return next(stage.max_tokens for stage in estimate.stages if stage.stage == "dimensions")

    assert dimensions_stage_tokens("d" * 40_020) - dimensions_stage_tokens("d" * 20) == 10_000


# A multiple of CHARACTERS_PER_TOKEN, so each request that carries it grows by an exact token count.
_PADDING = "z" * 40_000


def _systematization(padding: str) -> AssertSystematization:
    data = make_assert_systematization().model_dump(mode="json")
    data["scope"] += padding
    return AssertSystematization.model_validate(data)


def _taxonomy(padding: str) -> AssertTaxonomy:
    data = make_assert_taxonomy().model_dump(mode="json")
    data["behavior_categories"][0]["definition"] += padding
    return AssertTaxonomy.model_validate(data)


def _dimensions(padding: str) -> tuple[AssertDimension, ...]:
    config = _described_config()
    levels = [
        {"name": f"level_{index}", "definition": f"Condition {index}."} for index in range(config.dimension_level_count)
    ]
    levels[0]["definition"] += padding
    return tuple(
        AssertDimension.model_validate({**item.model_dump(mode="json"), "levels": levels}) for item in config.dimensions
    )


@pytest.mark.parametrize(
    "build, stages",
    [
        pytest.param(
            lambda padding: AssertGenerationCheckpoint(
                config=_described_config(), systematization=_systematization(padding)
            ),
            ["taxonomy"],
            id="stored-systematization",
        ),
        pytest.param(
            lambda padding: AssertGenerationCheckpoint(
                config=_described_config(), systematization=make_assert_systematization(), taxonomy=_taxonomy(padding)
            ),
            ["dimensions", "seed_groups"],
            id="stored-taxonomy",
        ),
        pytest.param(
            lambda padding: AssertGenerationCheckpoint(config=make_assert_config(taxonomy=_taxonomy(padding))),
            ["seed_groups"],
            id="supplied-taxonomy",
        ),
        pytest.param(
            lambda padding: AssertGenerationCheckpoint(
                config=_described_config(),
                systematization=make_assert_systematization(),
                taxonomy=make_assert_taxonomy(),
                dimensions=_dimensions(padding),
            ),
            ["seed_groups"],
            id="stored-dimension-levels",
        ),
    ],
)
def test_estimate_counts_the_stored_or_supplied_policy(
    build: Callable[[str], AssertGenerationCheckpoint], stages: list[str]
) -> None:
    small = {stage.stage: stage for stage in AssertGenerator.estimate(checkpoint=build("")).stages}
    large = {stage.stage: stage for stage in AssertGenerator.estimate(checkpoint=build(_PADDING)).stages}
    added = PromptGenerationEstimate.tokens_from_characters(len(_PADDING))
    for name in stages:
        growth = (large[name].min_tokens - small[name].min_tokens, large[name].max_tokens - small[name].max_tokens)
        assert growth == (large[name].min_calls * added, large[name].max_calls * added)


@pytest.mark.parametrize("policy_exists", [False, True])
def test_estimate_counts_the_escaped_request_that_is_sent(policy_exists: bool) -> None:
    def estimate(context: str) -> PromptGenerationEstimate:
        if policy_exists:
            return AssertGenerator.estimate(
                checkpoint=make_assert_checkpoint(config=make_assert_config(context=context))
            )
        return AssertGenerator.estimate(
            checkpoint=AssertGenerationCheckpoint(config=_described_config(context=context))
        )

    assert estimate("一" * 300) == estimate("x" * 1800)


@pytest.mark.parametrize("group_count, completed_batches", [(0, 1), (1, 0), (0, 5), (0, 50)])
def test_estimate_rejects_a_checkpoint_that_resuming_rejects(group_count: int, completed_batches: int) -> None:
    groups = tuple(make_assert_group_content(index=index) for index in range(group_count))
    checkpoint = make_assert_checkpoint(groups=groups, completed_batches=completed_batches)
    with pytest.raises(ValueError, match="don't match the planned coverage assignments"):
        AssertGenerator.estimate(checkpoint=checkpoint)
