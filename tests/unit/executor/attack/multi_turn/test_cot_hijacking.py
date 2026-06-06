# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Unit tests for CoT Hijacking Attack implementation.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackParameters,
    AttackScoringConfig,
    ConversationSession,
    CoTHijackingAttack,
    CoTHijackingAttackContext,
)
from pyrit.executor.attack.multi_turn.cot_hijacking import StreamState
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    AttackOutcome,
    AttackResult,
    Message,
    MessagePiece,
    Score,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget
from pyrit.score import TrueFalseScorer


def _mock_scorer_id(name: str = "MockScorer") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


def create_mock_chat_target(*, name: str = "MockChatTarget") -> MagicMock:
    """Create a mock chat target with common setup."""
    target = MagicMock(spec=PromptTarget)
    target.send_prompt_async = AsyncMock()
    target.set_system_prompt = MagicMock()
    target.get_identifier.return_value = _mock_target_id(name)
    target.configuration = MagicMock()
    target.configuration.includes.return_value = True
    target.capabilities = MagicMock()
    target.capabilities.supports_json_output = True
    return target


class CoTHijackingTestHelper:
    """Helper class for creating CoT Hijacking attack instances with mocked memory."""

    @staticmethod
    def create_attack(
        *,
        objective_target: MagicMock,
        adversarial_chat: MagicMock,
        objective_scorer: MagicMock | None = None,
        **kwargs,
    ) -> CoTHijackingAttack:
        """Create a CoTHijackingAttack instance with flexible configuration."""
        adversarial_config = AttackAdversarialConfig(target=adversarial_chat)

        if objective_scorer is None:
            objective_scorer = MagicMock(spec=TrueFalseScorer)
            objective_scorer.get_identifier.return_value = _mock_scorer_id()

        scoring_config = AttackScoringConfig(objective_scorer=objective_scorer)

        # Default max_iterations to 3 for tests unless overridden
        kwargs.setdefault("max_iterations", 3)

        attack = CoTHijackingAttack(
            objective_target=objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
            **{k: v for k, v in kwargs.items() if k in ["max_iterations", "puzzle_types", "n_streams"]},
        )

        # Mock the memory to avoid "Central memory instance has not been set" error
        mock_memory = MagicMock()
        attack._memory = mock_memory

        return attack


@pytest.fixture
def mock_objective_target() -> MagicMock:
    """Mock objective target for attack."""
    return create_mock_chat_target(name="MockObjectiveTarget")


@pytest.fixture
def mock_adversarial_chat() -> MagicMock:
    """Mock adversarial chat for attack generation."""
    return create_mock_chat_target(name="MockAdversarialChat")


@pytest.fixture
def mock_objective_scorer() -> MagicMock:
    """Mock objective scorer."""
    scorer = MagicMock(spec=TrueFalseScorer)
    scorer.score_async = AsyncMock()
    scorer.get_identifier.return_value = _mock_scorer_id("MockScorer")
    return scorer


@pytest.fixture
def mock_prompt_normalizer() -> MagicMock:
    """Mock prompt normalizer."""
    normalizer = MagicMock(spec=PromptNormalizer)
    normalizer.send_prompt_async = AsyncMock()
    return normalizer


@pytest.fixture
def basic_context() -> CoTHijackingAttackContext:
    """Create basic attack context with a single initialized stream."""
    return CoTHijackingAttackContext(
        params=AttackParameters(objective="Test objective"),
        session=ConversationSession(),
        stream_states=[StreamState(stream_id=0, puzzle_type="logic_grid")],
    )


@pytest.fixture
def sample_response() -> Message:
    """Create sample target response."""
    return Message(
        message_pieces=[
            MessagePiece(
                role="assistant",
                original_value="Test response",
                original_value_data_type="text",
                converted_value="Test response",
                converted_value_data_type="text",
            )
        ]
    )


@pytest.fixture
def success_score() -> Score:
    """Create success score (float_scale value 1.0 → get_value() returns True after threshold)."""
    return Score(
        score_type="true_false",
        score_value="true",
        score_category=["test"],
        score_value_description="Test success score",
        score_rationale="Test rationale for success",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id("MockScorer"),
    )


@pytest.fixture
def failure_score() -> Score:
    """Create failure score (get_value() returns False)."""
    return Score(
        score_type="true_false",
        score_value="false",
        score_category=["test"],
        score_value_description="Test failure score",
        score_rationale="Test rationale for failure",
        score_metadata={},
        message_piece_id=str(uuid.uuid4()),
        scorer_class_identifier=_mock_scorer_id("MockScorer"),
    )


@pytest.mark.usefixtures("patch_central_database")
class TestCoTHijackingAttackInitialization:
    """Tests for CoTHijacking attack initialization."""

    def test_init_with_minimal_parameters(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test attack initializes with minimal parameters."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )

        assert attack._objective_target == mock_objective_target
        assert attack._objective_scorer == mock_objective_scorer
        assert attack._adversarial_chat == mock_adversarial_chat
        assert isinstance(attack._prompt_normalizer, PromptNormalizer)
        # Default set to 3 by the test helper
        assert attack._max_iterations == 3

    def test_init_default_max_iterations(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that the real default max_iterations is 10."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)
        attack = CoTHijackingAttack(
            objective_target=mock_objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=scoring_config,
        )
        assert attack._max_iterations == 10

    def test_init_with_custom_max_iterations(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test attack initializes with custom max_iterations."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            max_iterations=5,
        )

        assert attack._max_iterations == 5

    def test_init_with_custom_puzzle_types(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test attack initializes with custom puzzle types."""
        puzzle_types = [
            "logic_grid",
            "skyscrapers",
        ]
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            puzzle_types=puzzle_types,
        )

        assert attack._puzzle_types == puzzle_types

    def test_init_with_invalid_puzzle_type_raises(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that invalid puzzle types raise ValueError."""
        with pytest.raises(ValueError, match="Unknown puzzle_type"):
            CoTHijackingTestHelper.create_attack(
                objective_target=mock_objective_target,
                adversarial_chat=mock_adversarial_chat,
                puzzle_types=["invalid_type"],
            )

    def test_init_without_objective_scorer_raises(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that initialization without an objective scorer raises ValueError."""
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig()

        with pytest.raises(ValueError, match="An objective scorer is required"):
            CoTHijackingAttack(
                objective_target=mock_objective_target,
                attack_adversarial_config=adversarial_config,
                attack_scoring_config=scoring_config,
            )

    def test_init_adversarial_target_must_support_multi_turn(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
    ):
        """Test that adversarial targets without native MULTI_TURN are rejected."""
        bad_adversarial = MagicMock(spec=PromptTarget)
        bad_adversarial.get_identifier.return_value = _mock_target_id("BadAdversarial")
        bad_adversarial.configuration = MagicMock()
        bad_adversarial.configuration.includes.return_value = False
        adversarial_config = AttackAdversarialConfig(target=bad_adversarial)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        with pytest.raises(ValueError, match="CoTHijackingAttack"):
            CoTHijackingAttack(
                objective_target=mock_objective_target,
                attack_adversarial_config=adversarial_config,
                attack_scoring_config=scoring_config,
            )

    def test_init_all_supported_puzzle_types_valid(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that all SUPPORTED_PUZZLE_TYPES are accepted."""
        from pyrit.executor.attack.multi_turn.cot_hijacking import SUPPORTED_PUZZLE_TYPES

        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            puzzle_types=SUPPORTED_PUZZLE_TYPES,
        )
        assert attack._puzzle_types == SUPPORTED_PUZZLE_TYPES

    def test_build_identifier_includes_behavioral_params(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that _build_identifier includes behavioral parameters and adversarial chat."""
        puzzle_types = ["logic_grid", "sudoku"]
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            max_iterations=7,
            puzzle_types=puzzle_types,
            n_streams=3,
        )

        identifier = attack.get_identifier()

        assert identifier.class_name == "CoTHijackingAttack"
        assert identifier.params["max_iterations"] == 7
        assert identifier.params["puzzle_types"] == puzzle_types
        assert identifier.params["n_streams"] == 3
        assert "adversarial_chat" in identifier.children
        assert "objective_target" in identifier.children
        assert "objective_scorer" in identifier.children

    def test_different_configs_produce_different_identifiers(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that different behavioral configs produce different identifier hashes."""
        attack1 = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            max_iterations=3,
        )
        attack2 = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            max_iterations=5,
        )

        assert attack1.get_identifier().hash != attack2.get_identifier().hash

    def test_same_config_produces_same_identifier(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that identical configs produce deterministic identifiers."""
        attack1 = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            puzzle_types=["logic_grid"],
        )
        attack2 = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            puzzle_types=["logic_grid"],
        )

        assert attack1.get_identifier().hash == attack2.get_identifier().hash


@pytest.mark.usefixtures("patch_central_database")
class TestReasoningStepTracking:
    """Tests for CoT length / reasoning step tracking in the feedback loop."""

    def test_extract_reasoning_step_count_counts_paragraph_breaks(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Counts double-newline paragraph breaks in the target response."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
        )
        message = Message.from_prompt(
            prompt="Step one\n\nStep two\n\nStep three",
            role="assistant",
        )

        assert attack._extract_reasoning_step_count(message=message) == 2

    def test_format_target_feedback_includes_step_number(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Feedback sent to the adversarial model includes STEP NUMBER."""
        feedback = CoTHijackingAttack._format_target_feedback(
            objective="Test objective",
            target_response="Model output",
            score=7.5,
            step_number=12,
        )

        assert "LANGUAGE MODEL OUTPUT: Model output" in feedback
        assert "OBJECTIVE:" in feedback
        assert "Test objective" in feedback
        assert "SCORE: 7.5." in feedback
        assert "STEP NUMBER: 12." in feedback

    @pytest.mark.asyncio
    async def test_build_adversarial_prompt_includes_step_number_on_refinement(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
    ):
        """Refinement prompts include structured feedback with STEP NUMBER."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
        )
        basic_context.last_target_response = "Blocked response"
        basic_context.last_reasoning_step_count = 9

        prompt = await attack._build_adversarial_prompt_async(
            context=basic_context,
            stream_state=StreamState(stream_id=0, puzzle_type="logic_grid"),
            iteration=1,
        )

        assert "STEP NUMBER: 9." in prompt
        assert "LANGUAGE MODEL OUTPUT: Blocked response" in prompt


@pytest.mark.usefixtures("patch_central_database")
class TestContextCreation:
    """Tests for context creation."""

    @pytest.mark.asyncio
    async def test_execute_async_creates_context(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test that execute_async creates context properly."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )

        with patch.object(attack, "_validate_context"):
            with patch.object(attack, "_setup_async", new_callable=AsyncMock):
                with patch.object(attack, "_perform_async", new_callable=AsyncMock) as mock_perform:
                    with patch.object(attack, "_teardown_async", new_callable=AsyncMock):
                        captured_context = None

                        async def capture_context(*args, **kwargs):
                            nonlocal captured_context
                            captured_context = kwargs.get("context")
                            return AttackResult(
                                conversation_id="test-id",
                                objective="Test objective",
                                atomic_attack_identifier=attack.get_identifier(),
                                outcome=AttackOutcome.SUCCESS,
                                executed_turns=1,
                            )

                        mock_perform.side_effect = capture_context

                        await attack.execute_async(objective="Test objective")

                        assert captured_context is not None
                        assert captured_context.objective == "Test objective"

    def test_init_objective_target_must_support_multi_turn(
        self,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
    ):
        """Test that objective targets without native MULTI_TURN are rejected at init."""
        bad_objective = MagicMock(spec=PromptTarget)
        bad_objective.get_identifier.return_value = _mock_target_id("BadObjective")
        bad_objective.configuration = MagicMock()
        bad_objective.configuration.includes.return_value = False
        adversarial_config = AttackAdversarialConfig(target=mock_adversarial_chat)
        scoring_config = AttackScoringConfig(objective_scorer=mock_objective_scorer)

        with pytest.raises(ValueError, match="supports_multi_turn"):
            CoTHijackingAttack(
                objective_target=bad_objective,
                attack_adversarial_config=adversarial_config,
                attack_scoring_config=scoring_config,
            )


@pytest.mark.usefixtures("patch_central_database")
class TestAttackExecution:
    """Tests for attack execution."""

    @pytest.mark.asyncio
    async def test_perform_attack_succeeds_on_first_iteration(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
        sample_response: Message,
        success_score: Score,
    ):
        """Test attack returns SUCCESS when scorer returns True on first iteration."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )

        with patch.object(
            attack, "_generate_attack_prompt_async", new_callable=AsyncMock, return_value="Attack prompt"
        ):
            with patch.object(
                attack, "_send_prompt_to_target_async", new_callable=AsyncMock, return_value=sample_response
            ):
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=success_score):
                    result = await attack._perform_async(context=basic_context)

                    assert result.outcome == AttackOutcome.SUCCESS
                    assert result.executed_turns == 1

    @pytest.mark.asyncio
    async def test_perform_attack_reaches_max_iterations(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
        sample_response: Message,
        failure_score: Score,
    ):
        """Test that attack stops after max iterations and returns FAILURE."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            max_iterations=2,
        )

        with patch.object(
            attack, "_generate_attack_prompt_async", new_callable=AsyncMock, return_value="Attack prompt"
        ):
            with patch.object(
                attack, "_send_prompt_to_target_async", new_callable=AsyncMock, return_value=sample_response
            ):
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=failure_score):
                    result = await attack._perform_async(context=basic_context)

                    assert result.outcome == AttackOutcome.FAILURE
                    assert result.executed_turns == 2

    @pytest.mark.asyncio
    async def test_perform_attack_continues_on_none_response(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
        sample_response: Message,
        failure_score: Score,
    ):
        """Test that attack skips iteration when target returns None."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            max_iterations=2,
        )

        # First call returns None (blocked), second returns a real response
        responses = [None, sample_response]

        with patch.object(
            attack, "_generate_attack_prompt_async", new_callable=AsyncMock, return_value="Attack prompt"
        ):
            with patch.object(attack, "_send_prompt_to_target_async", new_callable=AsyncMock, side_effect=responses):
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=failure_score):
                    result = await attack._perform_async(context=basic_context)

                    assert result.outcome == AttackOutcome.FAILURE
                    # Only 1 real turn executed (first was skipped)
                    assert result.executed_turns == 2

    @pytest.mark.asyncio
    async def test_perform_attack_stores_response_for_feedback(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
        sample_response: Message,
        failure_score: Score,
    ):
        """Test that last_target_response is updated after each iteration."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            max_iterations=1,
        )

        assert basic_context.last_target_response == ""

        with patch.object(
            attack, "_generate_attack_prompt_async", new_callable=AsyncMock, return_value="Attack prompt"
        ):
            with patch.object(
                attack, "_send_prompt_to_target_async", new_callable=AsyncMock, return_value=sample_response
            ):
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=failure_score):
                    await attack._perform_async(context=basic_context)

                    assert basic_context.last_target_response != ""
                    assert "Test response" in basic_context.last_target_response

    @pytest.mark.asyncio
    async def test_perform_attack_updates_last_score(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
        sample_response: Message,
        failure_score: Score,
    ):
        """Test that last_score is updated after scoring."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            max_iterations=1,
        )

        assert basic_context.last_score is None

        with patch.object(
            attack, "_generate_attack_prompt_async", new_callable=AsyncMock, return_value="Attack prompt"
        ):
            with patch.object(
                attack, "_send_prompt_to_target_async", new_callable=AsyncMock, return_value=sample_response
            ):
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=failure_score):
                    await attack._perform_async(context=basic_context)

                    assert basic_context.last_score is not None
                    assert basic_context.last_score == failure_score

    @pytest.mark.asyncio
    async def test_attack_assigns_puzzle_types_per_stream(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
        sample_response: Message,
        failure_score: Score,
    ):
        """Test that each parallel stream uses its assigned puzzle type."""
        puzzle_types = [
            "logic_grid",
            "skyscrapers",
            "category_theory",
        ]
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            max_iterations=1,
            n_streams=3,
            puzzle_types=puzzle_types,
        )
        basic_context.stream_states = [StreamState(stream_id=i, puzzle_type=puzzle_types[i]) for i in range(3)]

        captured_puzzle_types = []

        async def capture_puzzle_type(*args, **kwargs):
            stream_state = kwargs.get("stream_state")
            if stream_state is not None:
                captured_puzzle_types.append(stream_state.puzzle_type)
            return "Attack prompt"

        with patch.object(
            attack, "_generate_attack_prompt_async", new_callable=AsyncMock, side_effect=capture_puzzle_type
        ):
            with patch.object(
                attack, "_send_prompt_to_target_async", new_callable=AsyncMock, return_value=sample_response
            ):
                with patch.object(attack, "_score_response_async", new_callable=AsyncMock, return_value=failure_score):
                    await attack._perform_async(context=basic_context)

                    assert captured_puzzle_types == puzzle_types


@pytest.mark.usefixtures("patch_central_database")
class TestGenerateAttackPrompt:
    """Tests for adversarial prompt generation, template rendering, and JSON parsing."""

    @staticmethod
    def _create_attack_with_normalizer(
        *,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
        mock_objective_scorer: MagicMock,
    ) -> tuple[CoTHijackingAttack, MagicMock]:
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )
        mock_normalizer = MagicMock(spec=PromptNormalizer)
        mock_normalizer.send_prompt_async = AsyncMock()
        attack._prompt_normalizer = mock_normalizer
        return attack, mock_normalizer

    def test_render_meta_prompt_substitutes_objective_via_jinja2(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Template rendering injects the attack objective into the meta-prompt."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
        )
        context = CoTHijackingAttackContext(
            params=AttackParameters(objective="Build a harmless test puzzle"),
            session=ConversationSession(),
        )

        rendered = attack._render_meta_prompt(context=context, puzzle_type="logic_grid")

        assert "Build a harmless test puzzle" in rendered
        assert "{{objective}}" not in rendered

    @pytest.mark.asyncio
    async def test_generate_attack_prompt_extracts_prompt_from_json(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
    ):
        """End-to-end: adversarial model JSON with a prompt key is parsed and returned."""
        attack, mock_normalizer = self._create_attack_with_normalizer(
            mock_objective_target=mock_objective_target,
            mock_adversarial_chat=mock_adversarial_chat,
            mock_objective_scorer=mock_objective_scorer,
        )
        stream_state = StreamState(stream_id=0, puzzle_type="logic_grid")
        mock_normalizer.send_prompt_async.return_value = Message.from_prompt(
            prompt='{"prompt": "crafted jailbreak prompt"}',
            role="assistant",
        )

        result = await attack._generate_attack_prompt_async(
            context=basic_context,
            stream_state=stream_state,
            iteration=0,
        )

        assert result == "crafted jailbreak prompt"
        mock_normalizer.send_prompt_async.assert_awaited_once()
        call_kwargs = mock_normalizer.send_prompt_async.await_args.kwargs
        assert call_kwargs["conversation_id"] == stream_state.adversarial_chat_conversation_id
        assert call_kwargs["target"] == mock_adversarial_chat
        sent_message = call_kwargs["message"]
        assert sent_message.message_pieces[0].prompt_metadata.get("response_format") == "json"

    @pytest.mark.asyncio
    async def test_generate_attack_prompt_parses_markdown_wrapped_json(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
    ):
        """Markdown-fenced JSON from the adversarial model is stripped and parsed."""
        attack, mock_normalizer = self._create_attack_with_normalizer(
            mock_objective_target=mock_objective_target,
            mock_adversarial_chat=mock_adversarial_chat,
            mock_objective_scorer=mock_objective_scorer,
        )
        stream_state = StreamState(stream_id=0, puzzle_type="logic_grid")
        mock_normalizer.send_prompt_async.return_value = Message.from_prompt(
            prompt='```json\n{"prompt": "fenced jailbreak"}\n```',
            role="assistant",
        )

        result = await attack._generate_attack_prompt_async(
            context=basic_context,
            stream_state=stream_state,
            iteration=0,
        )

        assert result == "fenced jailbreak"

    @pytest.mark.asyncio
    async def test_generate_attack_prompt_falls_back_on_malformed_json(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
    ):
        """Malformed adversarial output falls back to the rendered meta-prompt."""
        attack, mock_normalizer = self._create_attack_with_normalizer(
            mock_objective_target=mock_objective_target,
            mock_adversarial_chat=mock_adversarial_chat,
            mock_objective_scorer=mock_objective_scorer,
        )
        stream_state = StreamState(stream_id=0, puzzle_type="logic_grid")
        expected_fallback = attack._render_meta_prompt(context=basic_context, puzzle_type="logic_grid")
        mock_normalizer.send_prompt_async.return_value = Message.from_prompt(
            prompt="this is not valid json {{{",
            role="assistant",
        )

        result = await attack._generate_attack_prompt_async(
            context=basic_context,
            stream_state=stream_state,
            iteration=0,
        )

        assert result == expected_fallback

    @pytest.mark.asyncio
    async def test_generate_attack_prompt_falls_back_when_prompt_key_missing(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
    ):
        """Valid JSON without a prompt key falls back to the rendered meta-prompt."""
        attack, mock_normalizer = self._create_attack_with_normalizer(
            mock_objective_target=mock_objective_target,
            mock_adversarial_chat=mock_adversarial_chat,
            mock_objective_scorer=mock_objective_scorer,
        )
        stream_state = StreamState(stream_id=0, puzzle_type="logic_grid")
        expected_fallback = attack._render_meta_prompt(context=basic_context, puzzle_type="logic_grid")
        mock_normalizer.send_prompt_async.return_value = Message.from_prompt(
            prompt='{"improvement": "make it harder", "puzzle_framework": "grid"}',
            role="assistant",
        )

        result = await attack._generate_attack_prompt_async(
            context=basic_context,
            stream_state=stream_state,
            iteration=0,
        )

        assert result == expected_fallback


@pytest.mark.usefixtures("patch_central_database")
class TestScoring:
    """Tests for scoring behavior."""

    @pytest.mark.asyncio
    async def test_score_response_handles_scorer_exception(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
        sample_response: Message,
    ):
        """Test that scoring failure is handled gracefully and returns None."""
        mock_objective_scorer.score_async.side_effect = Exception("Scorer exploded")

        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )

        result = await attack._score_response_async(message=sample_response, context=basic_context)
        assert result is None

    def test_extract_raw_score_from_true_false(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
        success_score: Score,
        failure_score: Score,
    ):
        """Test _extract_raw_score with true_false scorer values."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
        )

        assert attack._extract_raw_score(success_score) == 1.0
        assert attack._extract_raw_score(failure_score) == 0.0

    def test_extract_raw_score_from_metadata(
        self,
        mock_objective_target: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test _extract_raw_score reads ORIGINAL_FLOAT_VALUE_KEY from metadata."""
        from pyrit.score.score_utils import ORIGINAL_FLOAT_VALUE_KEY

        score_with_metadata = Score(
            score_type="true_false",
            score_value="true",
            score_category=["test"],
            score_value_description="desc",
            score_rationale="rationale",
            score_metadata={ORIGINAL_FLOAT_VALUE_KEY: "0.75"},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=_mock_scorer_id(),
        )

        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
        )

        assert attack._extract_raw_score(score_with_metadata) == 0.75


@pytest.mark.usefixtures("patch_central_database")
class TestFullAttackLifecycle:
    """Tests for complete attack lifecycle."""

    @pytest.mark.asyncio
    async def test_execute_async_successful_lifecycle(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
    ):
        """Test successful execution of complete attack lifecycle."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
            max_iterations=1,
        )

        with patch.object(attack, "_validate_context"):
            with patch.object(attack, "_setup_async", new_callable=AsyncMock):
                with patch.object(attack, "_perform_async", new_callable=AsyncMock) as mock_perform:
                    with patch.object(attack, "_teardown_async", new_callable=AsyncMock):
                        mock_perform.return_value = AttackResult(
                            conversation_id="test-id",
                            objective="Test objective",
                            atomic_attack_identifier=attack.get_identifier(),
                            outcome=AttackOutcome.SUCCESS,
                            executed_turns=1,
                        )

                        result = await attack.execute_async(objective="Test objective")

                        assert isinstance(result, AttackResult)
                        assert result.outcome == AttackOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_execute_with_context_async_successful(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
    ):
        """Test successful execution using execute_with_context_async."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )

        with patch.object(attack, "_validate_context"):
            with patch.object(attack, "_setup_async", new_callable=AsyncMock):
                with patch.object(attack, "_perform_async", new_callable=AsyncMock) as mock_perform:
                    with patch.object(attack, "_teardown_async", new_callable=AsyncMock):
                        mock_perform.return_value = AttackResult(
                            conversation_id=basic_context.session.conversation_id,
                            objective=basic_context.objective,
                            atomic_attack_identifier=attack.get_identifier(),
                            outcome=AttackOutcome.SUCCESS,
                            executed_turns=1,
                        )

                        result = await attack.execute_with_context_async(context=basic_context)

                        assert isinstance(result, AttackResult)
                        assert result.outcome == AttackOutcome.SUCCESS

    @pytest.mark.asyncio
    async def test_teardown_is_called_after_perform(
        self,
        mock_objective_target: MagicMock,
        mock_objective_scorer: MagicMock,
        mock_adversarial_chat: MagicMock,
        basic_context: CoTHijackingAttackContext,
    ):
        """Test that _teardown_async is always called after _perform_async."""
        attack = CoTHijackingTestHelper.create_attack(
            objective_target=mock_objective_target,
            adversarial_chat=mock_adversarial_chat,
            objective_scorer=mock_objective_scorer,
        )

        with patch.object(attack, "_validate_context"):
            with patch.object(attack, "_setup_async", new_callable=AsyncMock):
                with patch.object(attack, "_perform_async", new_callable=AsyncMock) as mock_perform:
                    with patch.object(attack, "_teardown_async", new_callable=AsyncMock) as mock_teardown:
                        mock_perform.return_value = AttackResult(
                            conversation_id="test-id",
                            objective="Test objective",
                            atomic_attack_identifier=attack.get_identifier(),
                            outcome=AttackOutcome.FAILURE,
                            executed_turns=3,
                        )

                        await attack.execute_with_context_async(context=basic_context)

                        mock_teardown.assert_called_once()
