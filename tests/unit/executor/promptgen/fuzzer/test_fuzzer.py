# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget, get_mock_scorer_identifier

from pyrit.common.path import DATASETS_PATH
from pyrit.converter import ConverterResult
from pyrit.datasets import TextJailBreak
from pyrit.executor.promptgen.fuzzer import (
    FuzzerContext,
    FuzzerConverter,
    FuzzerExpandConverter,
    FuzzerGenerator,
    FuzzerResult,
    FuzzerShortenConverter,
)
from pyrit.executor.promptgen.fuzzer.fuzzer import _PromptNode
from pyrit.memory import MemoryInterface, SQLiteMemory
from pyrit.models import (
    ComponentIdentifier,
    Message,
    MessagePiece,
    Scorable,
    Score,
    SeedDataset,
    SeedPrompt,
)
from pyrit.score import (
    FloatScaleScorer,
    FloatScaleThresholdScorer,
    MessageFloatScaleScorer,
    Scorer,
    ScorerPromptValidator,
    TrueFalseScorer,
)


class _DeterministicBatchScorer(MessageFloatScaleScorer):
    def __init__(self, *, values_by_prompt: dict[str, list[float]]) -> None:
        super().__init__(validator=ScorerPromptValidator(supported_data_types=["text"]))
        self._values_by_prompt = values_by_prompt
        self._batch_ready = asyncio.Barrier(2)
        self._second_scored = asyncio.Event()
        self.scoring_started = {prompt: asyncio.Event() for prompt in values_by_prompt}
        self.completed_prompts: list[str] = []

    def _build_identifier(self) -> ComponentIdentifier:
        return self._create_identifier()

    async def _score_piece_async(self, *, message_piece: MessagePiece, objective: str | None = None) -> list[Score]:
        prompt = message_piece.converted_value
        assert objective == prompt
        self.scoring_started[prompt].set()
        await self._batch_ready.wait()
        self.scoring_started[prompt].clear()
        # Complete the second scoring task first without relying on timing.
        if prompt == "first":
            await self._second_scored.wait()
            self._second_scored.clear()
        self.completed_prompts.append(prompt)
        if prompt == "second":
            self._second_scored.set()
        return [
            Score(
                score_value=str(value),
                score_type="float_scale",
                score_category=["test"],
                score_rationale=prompt,
                score_metadata={"prompt": prompt},
                message_piece_id=message_piece.id,
                scorer_class_identifier=self.get_identifier(),
            )
            for value in self._values_by_prompt[prompt]
        ]


@pytest.mark.usefixtures("patch_central_database")
@pytest.mark.parametrize(
    ("first_values", "second_values"),
    [
        pytest.param([0.0], [1.0], id="one-to-one"),
        pytest.param([0.0, 0.0], [1.0], id="multiple-scores"),
        pytest.param([], [1.0], id="skipped-first-response"),
        pytest.param([], [], id="no-scores"),
        pytest.param([1.0], [1.0], id="both-successful"),
    ],
)
@pytest.mark.parametrize(
    ("first_acquired_prompt", "delay_acquisition"),
    [
        pytest.param("first", False, id="first-acquired-first"),
        pytest.param("second", False, id="second-acquired-first"),
        pytest.param("first", True, id="delayed-second-acquisition"),
    ],
)
async def test_execute_async_preserves_score_attribution_and_query_limit_async(
    *,
    sqlite_instance: SQLiteMemory,
    first_values: list[float],
    second_values: list[float],
    first_acquired_prompt: str,
    delay_acquisition: bool,
) -> None:
    """Exercise real batching and persistence, including two turns and reversed scoring completion."""
    values_by_prompt = {"first": first_values, "second": second_values}
    responses_by_prompt: dict[str, list[MessagePiece]] = {"first": [], "second": []}
    acquired_prompts: list[str] = []
    max_queries = 4
    acquisition_clock_jump = 5.25 if delay_acquisition else 0.0
    loop = asyncio.get_running_loop()
    original_time = loop.time
    clock_offset = 0.0

    async def respond_async(*, normalized_conversation: list[Message]) -> list[Message]:
        assert sum(len(responses) for responses in responses_by_prompt.values()) < max_queries
        request = normalized_conversation[-1].get_piece()
        response = MessagePiece(
            role="assistant", original_value=request.converted_value, conversation_id=request.conversation_id
        )
        responses_by_prompt[request.converted_value].append(response)
        return [response.to_message()]

    target = MockPromptTarget()
    converter = FuzzerShortenConverter(converter_target=target)
    scorer = _DeterministicBatchScorer(values_by_prompt=values_by_prompt)
    resolve_async = scorer._message_resolver.resolve_async

    async def acquire_async(*, scorable: Scorable, memory: MemoryInterface) -> Message:
        nonlocal clock_offset
        message = await resolve_async(scorable=scorable, memory=memory)
        prompt = message.get_piece().converted_value
        if prompt != first_acquired_prompt:
            await scorer.scoring_started[first_acquired_prompt].wait()
            if delay_acquisition and not clock_offset:
                # Dispatch the old five-second deadline without slowing the test down.
                clock_offset = acquisition_clock_jump
                await asyncio.sleep(0)
                await asyncio.sleep(0)
        acquired_prompts.append(prompt)
        return message

    generator = FuzzerGenerator(
        objective_target=target,
        template_converters=[converter],
        scorer=scorer,
        target_jailbreak_goal_count=100,
    )
    with (
        patch.object(loop, "time", side_effect=lambda: original_time() + clock_offset),
        patch.object(scorer._message_resolver, "resolve_async", side_effect=acquire_async),
        patch.object(target, "_send_prompt_to_target_async", new_callable=AsyncMock, side_effect=respond_async) as send,
        patch.object(
            converter,
            "convert_async",
            new_callable=AsyncMock,
            return_value=ConverterResult(output_text="{{ prompt }}", output_type="text"),
        ) as convert,
    ):
        result = await asyncio.wait_for(
            generator.execute_async(
                prompts=list(values_by_prompt), prompt_templates=["{{ prompt }}"], max_query_limit=max_queries
            ),
            # Keep the real-time watchdog budget unchanged by the simulated acquisition delay.
            timeout=10 + acquisition_clock_jump,
        )

    assert send.await_count == result.total_queries == max_queries
    assert convert.await_count == 2
    second_acquired_prompt = "second" if first_acquired_prompt == "first" else "first"
    assert acquired_prompts == [first_acquired_prompt, second_acquired_prompt] * 2
    assert clock_offset == acquisition_clock_jump
    assert scorer.completed_prompts == ["second", "first"] * 2
    expected_conversations = [
        responses_by_prompt[prompt][turn].conversation_id
        for turn in range(2)
        for prompt, values in values_by_prompt.items()
        for value in values
        if value >= 0.8
    ]
    assert result.jailbreak_conversation_ids == expected_conversations
    assert result.successful_templates == (["{{ prompt }}"] if expected_conversations else [])

    stored_scores = await sqlite_instance.get_scores_async(score_type="float_scale")
    assert len(stored_scores) == 2 * sum(len(values) for values in values_by_prompt.values())
    stored_responses = await sqlite_instance.get_message_pieces_async(role="assistant")
    assert len(stored_responses) == max_queries
    responses_by_id = {str(piece.id): piece for piece in stored_responses}
    successful_conversations = []
    for score in stored_scores:
        response = responses_by_id[str(score.message_piece_id)]
        prompt = response.converted_value
        assert score.objective == score.score_rationale == prompt
        assert score.score_category == ["test"]
        assert score.score_metadata == {"prompt": prompt}
        assert score.get_value() in values_by_prompt[prompt]
        if score.get_value() >= 0.8:
            successful_conversations.append(response.conversation_id)
    assert sorted(successful_conversations) == sorted(result.jailbreak_conversation_ids)


@pytest.mark.usefixtures("patch_central_database")
class TestFuzzerGenerator:
    """Test class for FuzzerGenerator with database isolation."""

    @pytest.fixture
    def scoring_target(self) -> MockPromptTarget:
        return MockPromptTarget()

    @pytest.fixture
    def simple_prompts(self) -> list[str]:
        """Sample prompts for testing."""
        prompts = SeedDataset.from_yaml_file(
            pathlib.Path(DATASETS_PATH) / "seed_datasets" / "local" / "airt" / "illegal.prompt"
        )
        return [p.value for p in prompts.seeds]

    @pytest.fixture
    def simple_prompt_templates(self) -> list[str]:
        """Sample prompt templates for testing."""
        prompt_template1 = TextJailBreak(template_file_name="jailbreak_1.yaml")
        prompt_template2 = TextJailBreak(template_file_name="aim.yaml")
        prompt_template3 = TextJailBreak(template_file_name="aligned.yaml")
        prompt_template4 = TextJailBreak(template_file_name="axies.yaml")

        return [
            prompt_template1.template.value,
            prompt_template2.template.value,
            prompt_template3.template.value,
            prompt_template4.template.value,
        ]

    @pytest.fixture
    def template_converters(self, scoring_target) -> list[FuzzerConverter]:
        """Template converters for testing."""
        prompt_shorten_converter = FuzzerShortenConverter(converter_target=scoring_target)
        prompt_expand_converter = FuzzerExpandConverter(converter_target=scoring_target)
        return [prompt_shorten_converter, prompt_expand_converter]

    @pytest.fixture
    def mock_scorer(self) -> MagicMock:
        """Mock scorer for testing."""
        return MagicMock(TrueFalseScorer)

    @pytest.fixture
    def fuzzer_context(self, simple_prompts: list[str], simple_prompt_templates: list[str]) -> FuzzerContext:
        """Create a FuzzerContext for testing."""
        return FuzzerContext(
            prompts=simple_prompts,
            prompt_templates=simple_prompt_templates,
            max_query_limit=100,
        )

    def test_fuzzer_generator_initialization_with_valid_params(
        self, scoring_target: MockPromptTarget, template_converters: list[FuzzerConverter], mock_scorer: MagicMock
    ) -> None:
        """Test FuzzerGenerator initialization with valid parameters."""
        generator = FuzzerGenerator(
            objective_target=scoring_target,
            template_converters=template_converters,
            scorer=mock_scorer,
        )

        assert generator._objective_target == scoring_target
        assert generator._template_converters == template_converters
        assert generator._scorer == mock_scorer
        assert generator._batch_size == FuzzerGenerator._DEFAULT_BATCH_SIZE
        assert generator._target_jailbreak_goal_count == FuzzerGenerator._DEFAULT_TARGET_JAILBREAK_COUNT

    def test_fuzzer_generator_initialization_with_all_params(
        self, scoring_target: MockPromptTarget, template_converters: list[FuzzerConverter], mock_scorer: MagicMock
    ) -> None:
        """Test FuzzerGenerator initialization with all optional parameters."""
        generator = FuzzerGenerator(
            objective_target=scoring_target,
            template_converters=template_converters,
            scorer=mock_scorer,
            scoring_success_threshold=0.9,
            frequency_weight=0.6,
            reward_penalty=0.2,
            minimum_reward=0.3,
            non_leaf_node_probability=0.15,
            batch_size=20,
            target_jailbreak_goal_count=3,
        )

        assert generator._scoring_success_threshold == 0.9
        assert generator._batch_size == 20
        assert generator._target_jailbreak_goal_count == 3
        assert generator._mcts_explorer.frequency_weight == 0.6
        assert generator._mcts_explorer.reward_penalty == 0.2
        assert generator._mcts_explorer.minimum_reward == 0.3
        assert generator._mcts_explorer.non_leaf_node_probability == 0.15

    def test_fuzzer_generator_initialization_empty_template_converters(
        self, scoring_target: MockPromptTarget, mock_scorer: MagicMock
    ) -> None:
        """Test FuzzerGenerator raises error with empty template converters."""
        with pytest.raises(ValueError) as exc_info:
            FuzzerGenerator(
                objective_target=scoring_target,
                template_converters=[],
                scorer=mock_scorer,
            )
        assert exc_info.match("Template converters cannot be empty.")

    def test_fuzzer_generator_initialization_invalid_batch_size(
        self, scoring_target: MockPromptTarget, template_converters: list[FuzzerConverter], mock_scorer: MagicMock
    ) -> None:
        """Test FuzzerGenerator raises error with invalid batch size."""
        with pytest.raises(ValueError) as exc_info:
            FuzzerGenerator(
                objective_target=scoring_target,
                template_converters=template_converters,
                scorer=mock_scorer,
                batch_size=0,
            )
        assert exc_info.match("Batch size must be at least 1.")

    def test_fuzzer_generator_initialization_missing_scorer(
        self, scoring_target: MockPromptTarget, template_converters: list[FuzzerConverter]
    ) -> None:
        """Test FuzzerGenerator raises error when scorer is missing."""
        with pytest.raises(ValueError) as exc_info:
            FuzzerGenerator(
                objective_target=scoring_target,
                template_converters=template_converters,
                scorer=None,
            )
        assert exc_info.match("Scorer must be provided")

    def test_fuzzer_context_initialization(self, simple_prompts: list[str], simple_prompt_templates: list[str]) -> None:
        """Test FuzzerContext initialization with required parameters."""
        context = FuzzerContext(
            prompts=simple_prompts,
            prompt_templates=simple_prompt_templates,
        )

        assert context.prompts == simple_prompts
        assert context.prompt_templates == simple_prompt_templates
        assert context.total_target_query_count == 0
        assert context.total_jailbreak_count == 0
        assert context.jailbreak_conversation_ids == []
        assert context.executed_turns == 0
        assert context.initial_prompt_nodes == []

    def test_fuzzer_context_max_query_limit_calculation(
        self, simple_prompts: list[str], simple_prompt_templates: list[str]
    ) -> None:
        """Test FuzzerContext calculates max_query_limit correctly."""
        context = FuzzerContext(
            prompts=simple_prompts[:3],  # 3 prompts
            prompt_templates=simple_prompt_templates,  # 4 templates
        )

        # Should be 4 templates * 3 prompts * 10 (DEFAULT_QUERY_LIMIT_MULTIPLIER) = 120
        expected_limit = len(simple_prompt_templates) * 3 * FuzzerGenerator._DEFAULT_QUERY_LIMIT_MULTIPLIER
        assert context.max_query_limit == expected_limit

    def test_fuzzer_context_custom_max_query_limit(
        self, simple_prompts: list[str], simple_prompt_templates: list[str]
    ) -> None:
        """Test FuzzerContext respects custom max_query_limit."""
        custom_limit = 50
        context = FuzzerContext(
            prompts=simple_prompts,
            prompt_templates=simple_prompt_templates,
            max_query_limit=custom_limit,
        )

        assert context.max_query_limit == custom_limit

    def test_fuzzer_context_validation_empty_prompts(self, simple_prompt_templates: list[str]) -> None:
        """Test FuzzerContext validation with empty prompts."""
        generator = FuzzerGenerator(
            objective_target=MockPromptTarget(),
            template_converters=[FuzzerShortenConverter(converter_target=MockPromptTarget())],
            scorer=MagicMock(Scorer),
        )

        context = FuzzerContext(
            prompts=[],
            prompt_templates=simple_prompt_templates,
        )

        with pytest.raises(ValueError) as exc_info:
            generator._validate_context(context=context)
        assert exc_info.match("Prompts in context cannot be empty.")

    def test_fuzzer_context_validation_empty_templates(self, simple_prompts: list[str]) -> None:
        """Test FuzzerContext validation with empty prompt templates."""
        generator = FuzzerGenerator(
            objective_target=MockPromptTarget(),
            template_converters=[FuzzerShortenConverter(converter_target=MockPromptTarget())],
            scorer=MagicMock(Scorer),
        )

        context = FuzzerContext(
            prompts=simple_prompts,
            prompt_templates=[],
        )

        with pytest.raises(ValueError) as exc_info:
            generator._validate_context(context=context)
        assert exc_info.match("Prompt templates in context cannot be empty.")

    async def test_fuzzer_generator_with_default_scorer(
        self, scoring_target: MockPromptTarget, template_converters: list[FuzzerConverter]
    ) -> None:
        """Test FuzzerGenerator.with_default_scorer factory method."""
        generator = FuzzerGenerator.with_default_scorer(
            objective_target=scoring_target,
            template_converters=template_converters,
            scoring_target=scoring_target,
        )

        assert generator._objective_target == scoring_target
        assert generator._template_converters == template_converters
        assert isinstance(generator._scorer, FloatScaleThresholdScorer)

    def test_is_jailbreak_true_false_scorer(
        self, scoring_target: MockPromptTarget, template_converters: list[FuzzerConverter], mock_scorer: MagicMock
    ) -> None:
        """Test _is_jailbreak method with true_false scorer."""
        generator = FuzzerGenerator(
            objective_target=scoring_target,
            template_converters=template_converters,
            scorer=mock_scorer,
        )

        # Test with "True" score
        true_score = Score(
            score_value="True",
            score_value_description="",
            score_type="true_false",
            score_category=[],
            score_rationale="",
            score_metadata={},
            message_piece_id="",
            scorer_class_identifier=get_mock_scorer_identifier(),
        )
        assert generator._is_jailbreak(true_score) is True

        # Test with "False" score
        false_score = Score(
            score_value="False",
            score_value_description="",
            score_type="true_false",
            score_category=[],
            score_rationale="",
            score_metadata={},
            message_piece_id="",
            scorer_class_identifier=get_mock_scorer_identifier(),
        )
        assert generator._is_jailbreak(false_score) is False

    def test_process_scoring_results_attributes_jailbreaks_to_scored_response(
        self,
        scoring_target: MockPromptTarget,
        template_converters: list[FuzzerConverter],
        mock_scorer: MagicMock,
        fuzzer_context: FuzzerContext,
    ) -> None:
        """A jailbreak verdict must map to the response its score was stored on, not its list position."""
        generator = FuzzerGenerator(
            objective_target=scoring_target,
            template_converters=template_converters,
            scorer=mock_scorer,
        )

        first_piece = MessagePiece(role="assistant", original_value="first", conversation_id="conv-first")
        second_piece = MessagePiece(role="assistant", original_value="second", conversation_id="conv-second")
        responses = [first_piece.to_message(), second_piece.to_message()]

        def _score(piece: MessagePiece, value: str) -> Score:
            return Score(
                score_value=value,
                score_value_description="",
                score_type="true_false",
                score_category=[],
                score_rationale="",
                score_metadata={},
                message_piece_id=piece.id,
                scorer_class_identifier=get_mock_scorer_identifier(),
            )

        node = _PromptNode(template="template", parent=None)

        # A scorer can return several scores for one response, so the flattened
        # batch list does not line up with the responses by position.
        scores = [_score(first_piece, "false"), _score(first_piece, "false"), _score(second_piece, "true")]
        jailbreak_count = generator._process_scoring_results(
            context=fuzzer_context,
            scores=scores,
            responses=responses,
            template_node=node,
            current_seed=node,
        )
        assert jailbreak_count == 1
        assert fuzzer_context.jailbreak_conversation_ids == ["conv-second"]
        assert fuzzer_context.total_target_query_count == 2

        # A batch where only the second response produced a score must not credit the first conversation.
        fuzzer_context.jailbreak_conversation_ids = []
        fuzzer_context.total_target_query_count = 0
        jailbreak_count = generator._process_scoring_results(
            context=fuzzer_context,
            scores=[_score(second_piece, "true")],
            responses=responses,
            template_node=node,
            current_seed=node,
        )
        assert jailbreak_count == 1
        assert fuzzer_context.jailbreak_conversation_ids == ["conv-second"]
        assert fuzzer_context.total_target_query_count == 2

    def test_is_jailbreak_float_scale_scorer(
        self, scoring_target: MockPromptTarget, template_converters: list[FuzzerConverter]
    ) -> None:
        """Test _is_jailbreak method with float scale scorer."""
        mock_scorer = MagicMock(FloatScaleScorer)

        generator = FuzzerGenerator(
            objective_target=scoring_target,
            template_converters=template_converters,
            scorer=mock_scorer,
            scoring_success_threshold=0.8,
        )

        # Test with high score (above threshold)
        high_score = Score(
            score_value="0.9",
            score_value_description="",
            score_type="float_scale",
            score_category=[],
            score_rationale="",
            score_metadata={},
            message_piece_id="",
            scorer_class_identifier=get_mock_scorer_identifier(),
        )
        assert generator._is_jailbreak(high_score) is True

        # Test with low score (below threshold)
        low_score = Score(
            score_value="0.7",
            score_value_description="",
            score_type="float_scale",
            score_category=[],
            score_rationale="",
            score_metadata={},
            message_piece_id="",
            scorer_class_identifier=get_mock_scorer_identifier(),
        )
        assert generator._is_jailbreak(low_score) is False

    def test_generate_prompts_from_template(
        self,
        scoring_target: MockPromptTarget,
        template_converters: list[FuzzerConverter],
        mock_scorer: MagicMock,
        simple_prompts: list[str],
    ) -> None:
        """Test _generate_prompts_from_template method."""
        generator = FuzzerGenerator(
            objective_target=scoring_target,
            template_converters=template_converters,
            scorer=mock_scorer,
        )

        template = SeedPrompt(value="Please {{ prompt }} carefully.", parameters=["prompt"])
        result = generator._generate_prompts_from_template(template=template, prompts=simple_prompts[:2])

        assert len(result) == 2
        for i, generated_prompt in enumerate(result):
            assert simple_prompts[i] in generated_prompt
            assert "Please" in generated_prompt
            assert "carefully." in generated_prompt

    def test_fuzzer_result_initialization(self) -> None:
        """Test FuzzerResult initialization."""
        result = FuzzerResult(
            successful_templates=["template1", "template2"],
            jailbreak_conversation_ids=["conv1", "conv2"],
            total_queries=50,
            templates_explored=10,
        )

        assert result.successful_templates == ["template1", "template2"]
        assert result.jailbreak_conversation_ids == ["conv1", "conv2"]
        assert result.total_queries == 50
        assert result.templates_explored == 10

    def test_fuzzer_result_str_method(self) -> None:
        """Test FuzzerResult __str__ method."""
        result = FuzzerResult(
            successful_templates=["template1"],
            total_queries=25,
        )

        str_result = str(result)
        assert "Total Queries: 25" in str_result
        assert "Successful Templates: 1" in str_result

    async def test_execute_generation_reaches_jailbreak_goal(
        self,
        scoring_target: MockPromptTarget,
        template_converters: list[FuzzerConverter],
        mock_scorer: MagicMock,
        fuzzer_context: FuzzerContext,
    ) -> None:
        """Test execute generation reaches jailbreak goal."""
        generator = FuzzerGenerator(
            objective_target=scoring_target,
            template_converters=template_converters,
            scorer=mock_scorer,
            target_jailbreak_goal_count=2,
        )

        # Mock the necessary methods
        with patch.object(generator, "_setup_async"):
            with patch.object(generator, "_execute_generation_iteration_async") as mock_iteration:
                with patch.object(generator, "_teardown_async"):
                    with patch.object(generator, "_should_stop_generation") as mock_should_stop:
                        # First two calls return False, third returns True (goal reached)
                        mock_should_stop.side_effect = [False, False, True]

                        result = await generator._perform_async(context=fuzzer_context)

                        assert len(result.successful_templates) >= 0  # Check result structure
                        assert mock_iteration.call_count == 2  # Called twice before stopping

    async def test_execute_generation_reaches_query_limit(
        self,
        scoring_target: MockPromptTarget,
        template_converters: list[FuzzerConverter],
        mock_scorer: MagicMock,
        fuzzer_context: FuzzerContext,
    ) -> None:
        """Test execute generation stops when query limit is reached."""
        generator = FuzzerGenerator(
            objective_target=scoring_target,
            template_converters=template_converters,
            scorer=mock_scorer,
        )

        # Set context to near query limit
        assert fuzzer_context.max_query_limit is not None
        fuzzer_context.total_target_query_count = fuzzer_context.max_query_limit - 1

        with patch.object(generator, "_setup_async"):
            with patch.object(generator, "_execute_generation_iteration_async") as mock_iteration:
                with patch.object(generator, "_teardown_async"):
                    with patch.object(generator, "_should_stop_generation") as mock_should_stop:
                        # First call returns False, second returns True (query limit reached)
                        mock_should_stop.side_effect = [False, True]

                        result = await generator._perform_async(context=fuzzer_context)

                        assert len(result.successful_templates) == 0
                        assert result.total_queries == fuzzer_context.total_target_query_count
                        assert mock_iteration.call_count == 1  # Called once before stopping


@pytest.mark.usefixtures("patch_central_database")
class TestPromptNode:
    """Test class for _PromptNode with database isolation."""

    def test_prompt_node_initialization_no_parent(self) -> None:
        """Test _PromptNode initialization without parent."""
        template = "Test template"
        node = _PromptNode(template=template)

        assert node.template == template
        assert node.parent is None
        assert node.children == []
        assert node.level == 0
        assert node.visited_num == 0
        assert node.rewards == 0

    def test_prompt_node_initialization_with_parent(self) -> None:
        """Test _PromptNode initialization with parent."""
        parent_template = "Parent template"
        child_template = "Child template"

        parent_node = _PromptNode(template=parent_template)
        child_node = _PromptNode(template=child_template, parent=parent_node)

        assert child_node.template == child_template
        assert child_node.parent == parent_node
        assert child_node.level == 1
        assert parent_node.children == [child_node]

    def test_prompt_node_add_parent(self) -> None:
        """Test _PromptNode add_parent method."""
        parent_node = _PromptNode(template="Parent")
        child_node = _PromptNode(template="Child")

        child_node.add_parent(parent_node)

        assert child_node.parent == parent_node
        assert child_node.level == 1
        assert parent_node.children == [child_node]

    def test_prompt_node_multi_level_hierarchy(self) -> None:
        """Test _PromptNode with multi-level hierarchy."""
        root = _PromptNode(template="Root")
        level1 = _PromptNode(template="Level1", parent=root)
        level2 = _PromptNode(template="Level2", parent=level1)

        assert root.level == 0
        assert level1.level == 1
        assert level2.level == 2
        assert len(root.children) == 1
        assert len(level1.children) == 1
        assert len(level2.children) == 0


def test_create_normalizer_requests_raises_when_seed_group_message_none():
    """Test that _create_normalizer_requests raises ValueError when seed_group.next_message is None."""
    from unittest.mock import PropertyMock

    from pyrit.executor.promptgen.fuzzer.fuzzer import FuzzerGenerator

    generator = FuzzerGenerator.__new__(FuzzerGenerator)
    generator._request_converters = []
    generator._response_converters = []

    with patch("pyrit.executor.promptgen.fuzzer.fuzzer.SeedGroup") as mock_seed_group:
        mock_instance = MagicMock()
        type(mock_instance).next_message = PropertyMock(return_value=None)
        mock_seed_group.return_value = mock_instance

        with pytest.raises(ValueError, match="No message in seed group"):
            generator._create_normalizer_requests(["test prompt"])
