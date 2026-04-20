# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib
from unittest.mock import MagicMock, patch

import pytest
from unit.mocks import MockPromptTarget

from pyrit.common.path import DATASETS_PATH
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
from pyrit.models import (
    Score,
    SeedDataset,
    SeedPrompt,
)
from pyrit.score import (
    FloatScaleScorer,
    FloatScaleThresholdScorer,
    Scorer,
    TrueFalseScorer,
)
from tests.unit.mocks import get_mock_scorer_identifier


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
        scorer = MagicMock(TrueFalseScorer)
        return scorer

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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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

    @pytest.mark.asyncio
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
