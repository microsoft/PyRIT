# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from treelib.tree import Tree

from pyrit.exceptions import InvalidJsonException
from pyrit.executor.attack import (
    AttackAdversarialConfig,
    AttackConverterConfig,
    AttackParameters,
    TAPAttackContext,
    TAPAttackResult,
    TreeOfAttacksWithPruningAttack,
)
from pyrit.executor.attack.multi_turn.tree_of_attacks import (
    AttackScoringConfig,
    TAPAttackScoringConfig,
    _TreeOfAttacksNode,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import (
    AttackOutcome,
    ConversationReference,
    ConversationType,
    Message,
    MessagePiece,
    Score,
    SeedPrompt,
)
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptChatTarget, PromptTarget
from pyrit.prompt_target.common.target_capabilities import CapabilityName
from pyrit.score import FloatScaleThresholdScorer, Scorer, TrueFalseScorer
from pyrit.score.float_scale.float_scale_scorer import FloatScaleScorer
from pyrit.score.score_utils import normalize_score_to_float

logger = logging.getLogger(__name__)


@dataclass
class NodeMockConfig:
    """Configuration for creating mock _TreeOfAttacksNode objects."""

    node_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id: Optional[str] = None
    prompt_sent: bool = False
    completed: bool = True
    off_topic: bool = False
    objective_score_value: Optional[float] = None
    auxiliary_scores: dict[str, float] = field(default_factory=dict)
    objective_target_conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    adversarial_chat_conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class MockNodeFactory:
    """Factory for creating mock _TreeOfAttacksNode objects."""

    @staticmethod
    def create_node(config: Optional[NodeMockConfig] = None) -> "_TreeOfAttacksNode":
        """Create a mock _TreeOfAttacksNode with the given configuration."""
        if config is None:
            config = NodeMockConfig()

        node = MagicMock()

        # Set all attributes
        node.node_id = config.node_id
        node.parent_id = config.parent_id
        node._vis_node_id = "root"
        node.prompt_sent = config.prompt_sent
        node.completed = config.completed
        node.off_topic = config.off_topic
        node.objective_target_conversation_id = config.objective_target_conversation_id
        node.adversarial_chat_conversation_id = config.adversarial_chat_conversation_id
        node.error_message = None

        node.send_prompt_async = AsyncMock(return_value=None)

        node._generate_adversarial_prompt_async = AsyncMock(return_value="test prompt")
        node._generate_red_teaming_prompt_async = AsyncMock(return_value='{"prompt": "test prompt"}')
        node._send_prompt_to_target_async = AsyncMock(return_value=MagicMock())
        node._score_response_async = AsyncMock(return_value=None)
        node._send_to_adversarial_chat_async = AsyncMock(return_value='{"prompt": "test prompt"}')
        node._check_on_topic_async = AsyncMock(return_value=True)
        node._execute_objective_prompt_async = AsyncMock(return_value=None)

        # Set up objective score
        if config.objective_score_value is not None:
            node.objective_score = MagicMock(
                get_value=MagicMock(return_value=config.objective_score_value), score_metadata=None
            )
        else:
            node.objective_score = None

        # Set up auxiliary scores
        node.auxiliary_scores = {}
        for name, value in config.auxiliary_scores.items():
            node.auxiliary_scores[name] = MagicMock(get_value=MagicMock(return_value=value))

        # Set up duplicate method to return a new mock node
        def duplicate_side_effect():
            dup = MockNodeFactory.create_node(NodeMockConfig(parent_id=node.node_id))
            dup._vis_node_id = node._vis_node_id
            return dup

        node.duplicate = MagicMock(side_effect=duplicate_side_effect)

        node.last_prompt_sent = None
        node.last_response = None

        node._memory = MagicMock()
        node._memory.duplicate_conversation = MagicMock(return_value=str(uuid.uuid4()))
        node._objective_target = MagicMock()
        node._adversarial_chat = MagicMock()
        node._objective_scorer = MagicMock()
        node._on_topic_scorer = None
        node._auxiliary_scorers = []
        node._request_converters = []
        node._response_converters = []
        node._memory_labels = {}
        node._attack_id = {"__type__": "MockAttack", "__module__": "test_module"}
        node._prompt_normalizer = MagicMock()

        # Mock the required internal methods that might be called
        node._mark_execution_complete = MagicMock()
        node._handle_json_error = MagicMock()
        node._handle_unexpected_error = MagicMock()
        node._parse_red_teaming_response = MagicMock(return_value="test prompt")

        return node

    @staticmethod
    def create_nodes_with_scores(scores: list[float]) -> list[_TreeOfAttacksNode]:
        """Create multiple nodes with the given objective scores."""
        return [
            MockNodeFactory.create_node(NodeMockConfig(node_id=f"node_{i}", objective_score_value=score))
            for i, score in enumerate(scores)
        ]


class AttackBuilder:
    """Builder for creating TreeOfAttacksWithPruningAttack instances with common configurations."""

    def __init__(self) -> None:
        self.objective_target: Optional[PromptTarget] = None
        self.adversarial_chat: Optional[PromptChatTarget] = None
        self.objective_scorer: Optional[Scorer] = None
        self.auxiliary_scorers: list[Scorer] = []
        self.tree_params: dict[str, Any] = {}
        self.converters: Optional[AttackConverterConfig] = None
        self.successful_threshold: float = 0.8
        self.prompt_normalizer: Optional[PromptNormalizer] = None
        self.error_score_map: dict[str, float] | None = None
        self._supports_multi_turn: bool = True

    def with_default_mocks(self) -> "AttackBuilder":
        """Set up default mocks for all required components."""
        self.objective_target = self._create_mock_target(supports_multi_turn=self._supports_multi_turn)
        self.adversarial_chat = self._create_mock_chat()
        self.objective_scorer = self._create_mock_scorer("MockScorer")
        return self

    def with_tree_params(self, **kwargs) -> "AttackBuilder":
        """Set tree parameters (width, depth, branching_factor, batch_size)."""
        self.tree_params = kwargs
        return self

    def with_threshold(self, threshold: float) -> "AttackBuilder":
        """Set successful objective threshold."""
        self.successful_threshold = threshold
        return self

    def with_auxiliary_scorers(self, count: int = 1) -> "AttackBuilder":
        """Add auxiliary scorers."""
        self.auxiliary_scorers = [self._create_mock_aux_scorer(f"MockAuxScorer{i}") for i in range(count)]
        return self

    def with_prompt_normalizer(self) -> "AttackBuilder":
        """Add a mock prompt normalizer."""
        normalizer = MagicMock(spec=PromptNormalizer)
        normalizer.send_prompt_async = AsyncMock(return_value=None)
        self.prompt_normalizer = cast("PromptNormalizer", normalizer)
        return self

    def with_error_score_map(self, error_score_map: dict[str, float] | None) -> "AttackBuilder":
        """Set the error score mapping."""
        self.error_score_map = error_score_map
        return self

    def with_supports_multi_turn(self, supports_multi_turn: bool) -> "AttackBuilder":
        """Set whether the objective target supports multi-turn conversations."""
        self._supports_multi_turn = supports_multi_turn
        return self

    def build(self) -> TreeOfAttacksWithPruningAttack:
        """Build the attack instance."""
        assert self.adversarial_chat is not None, "Adversarial chat target must be set."
        adversarial_config = AttackAdversarialConfig(target=self.adversarial_chat)

        # Create a mock FloatScaleThresholdScorer with the specified threshold
        mock_threshold_scorer = MagicMock(spec=FloatScaleThresholdScorer)
        mock_threshold_scorer.threshold = self.successful_threshold
        mock_threshold_scorer.scorer_type = "true_false"
        mock_threshold_scorer.score_async = AsyncMock(return_value=[])
        mock_threshold_scorer.get_identifier.return_value = ComponentIdentifier(
            class_name="FloatScaleThresholdScorer",
            class_module="pyrit.score",
        )

        scoring_config = TAPAttackScoringConfig(
            objective_scorer=mock_threshold_scorer,
            auxiliary_scorers=self.auxiliary_scorers,
        )

        kwargs = {
            "objective_target": self.objective_target,
            "attack_adversarial_config": adversarial_config,
            "attack_scoring_config": scoring_config,
            **self.tree_params,
        }

        if self.prompt_normalizer:
            kwargs["prompt_normalizer"] = self.prompt_normalizer

        if self.error_score_map is not None:
            kwargs["error_score_map"] = self.error_score_map

        return TreeOfAttacksWithPruningAttack(**kwargs)

    @staticmethod
    def _create_mock_target(supports_multi_turn: bool = True) -> PromptTarget:
        target = MagicMock(spec=PromptTarget)
        target.send_prompt_async = AsyncMock(return_value=None)
        target.get_identifier.return_value = ComponentIdentifier(
            class_name="MockTarget",
            class_module="test_module",
        )
        target.capabilities.supports_multi_turn = supports_multi_turn
        target.capabilities.output_modalities = frozenset({frozenset(["text"])})
        target.configuration.includes.side_effect = (
            lambda capability: capability == CapabilityName.MULTI_TURN and supports_multi_turn
        )
        target.configuration.capabilities.output_modalities = frozenset({frozenset(["text"])})
        return cast("PromptTarget", target)

    @staticmethod
    def _create_mock_chat() -> PromptChatTarget:
        chat = MagicMock(spec=PromptChatTarget)
        chat.send_prompt_async = AsyncMock(return_value=None)
        chat.set_system_prompt = MagicMock()
        chat.get_identifier.return_value = ComponentIdentifier(
            class_name="MockChatTarget",
            class_module="test_module",
        )
        return cast("PromptChatTarget", chat)

    @staticmethod
    def _create_mock_scorer(name: str) -> TrueFalseScorer:
        scorer = MagicMock(spec=TrueFalseScorer)
        scorer.scorer_type = "true_false"
        scorer.score_async = AsyncMock(return_value=[])
        scorer.get_identifier.return_value = ComponentIdentifier(
            class_name=name,
            class_module="test_module",
        )
        return cast("TrueFalseScorer", scorer)

    @staticmethod
    def _create_mock_aux_scorer(name: str) -> Scorer:
        """Create a mock auxiliary scorer (can be any Scorer type)."""
        scorer = MagicMock(spec=Scorer)
        scorer.scorer_type = "float_scale"
        scorer.score_async = AsyncMock(return_value=[])
        scorer.get_identifier.return_value = ComponentIdentifier(
            class_name=name,
            class_module="test_module",
        )
        return cast("Scorer", scorer)


class TestHelpers:
    """Helper methods for common test operations."""

    @staticmethod
    def create_basic_context() -> TAPAttackContext:
        """Create a basic context with initialized tree."""
        context = TAPAttackContext(
            params=AttackParameters(objective="Test objective", memory_labels={"test": "label"}),
        )
        context.tree_visualization.create_node("Root", "root")
        return context

    @staticmethod
    def create_score(value: float = 0.9) -> Score:
        """Create a mock Score object."""
        return Score(
            id=None,
            score_type="float_scale",
            score_value=str(value),
            score_category=["test"],
            score_value_description="Test score",
            score_rationale="Test rationale",
            score_metadata={"test": "metadata"},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=ComponentIdentifier(
                class_name="MockScorer",
                class_module="test_module",
            ),
        )

    @staticmethod
    async def create_threshold_score_async(*, original_float_value: float, threshold: float = 0.8) -> Score:
        """
        Create a TrueFalse Score using actual FloatScaleThresholdScorer.

        This uses the real FloatScaleThresholdScorer with a mock underlying FloatScaleScorer,
        ensuring tests will catch any changes to how FloatScaleThresholdScorer produces scores.

        Args:
            original_float_value: The original float score (0.0-1.0).
            threshold: The threshold used for true/false determination.

        Returns:
            Score with TrueFalse type and original_float_value in metadata.
        """
        # Create a mock FloatScaleScorer that returns the desired float value
        mock_float_scorer = MagicMock(spec=FloatScaleScorer)
        # Set up a proper identifier that can be JSON serialized
        mock_float_scorer.get_identifier.return_value = ComponentIdentifier(
            class_name="MockFloatScaleScorer",
            class_module="test_module",
        )

        # Create the float scale score that the mock scorer will return
        float_score = Score(
            id=None,
            score_type="float_scale",
            score_value=str(original_float_value),
            score_category=["objective"],
            score_value_description="Mock float score",
            score_rationale="Mock rationale",
            score_metadata={},
            message_piece_id=str(uuid.uuid4()),
            scorer_class_identifier=ComponentIdentifier(
                class_name="MockFloatScaleScorer",
                class_module="test_module",
            ),
        )
        mock_float_scorer.score_async = AsyncMock(return_value=[float_score])

        # Create the actual FloatScaleThresholdScorer
        threshold_scorer = FloatScaleThresholdScorer(scorer=mock_float_scorer, threshold=threshold)

        # Patch get_identifier to avoid MagicMock serialization issues
        threshold_scorer.get_identifier = lambda: ComponentIdentifier(
            class_name="FloatScaleThresholdScorer",
            class_module="pyrit.score",
        )

        # Create a dummy message to score
        dummy_message = Message(
            message_pieces=[
                MessagePiece(
                    role="assistant",
                    original_value="test response",
                    converted_value="test response",
                    conversation_id=str(uuid.uuid4()),
                    id=str(uuid.uuid4()),
                )
            ]
        )

        # Score using the actual FloatScaleThresholdScorer
        scores = await threshold_scorer.score_async(dummy_message)
        return scores[0]

    @staticmethod
    def create_threshold_score(*, original_float_value: float, threshold: float = 0.8) -> Score:
        """
        Create a TrueFalse Score using actual FloatScaleThresholdScorer (sync wrapper).

        This is a synchronous wrapper around create_threshold_score_async for use in
        non-async test methods. Uses asyncio.run() for proper event loop handling.

        Args:
            original_float_value: The original float score (0.0-1.0).
            threshold: The threshold used for true/false determination.

        Returns:
            Score with TrueFalse type and original_float_value in metadata.
        """
        return asyncio.run(
            TestHelpers.create_threshold_score_async(original_float_value=original_float_value, threshold=threshold)
        )

    @staticmethod
    def add_nodes_to_tree(context: TAPAttackContext, nodes: list[_TreeOfAttacksNode], parent: str = "root"):
        """Add nodes to the context's tree visualization and set their _vis_node_id."""
        for _i, node in enumerate(nodes):
            score_str = ""
            if node.objective_score:
                score_str = f": Score {node.objective_score.get_value()}"
            vis_id = f"{node.node_id}_d{context.executed_turns}"
            context.tree_visualization.create_node(f"{context.executed_turns}{score_str}", vis_id, parent=parent)
            node._vis_node_id = vis_id

    @staticmethod
    def mock_prompt_loading(attack: TreeOfAttacksWithPruningAttack):
        """Mock the prompt loading process."""
        mock_seed_prompts = [MagicMock(spec=SeedPrompt) for _ in range(3)]
        mock_dataset = MagicMock()
        mock_dataset.seeds = mock_seed_prompts

        with patch("pyrit.models.seeds.seed_dataset.SeedDataset.from_yaml_file", return_value=mock_dataset):
            attack._load_adversarial_prompts()


@pytest.fixture
def node_factory():
    """Fixture providing the MockNodeFactory."""
    return MockNodeFactory()


@pytest.fixture
def attack_builder():
    """Fixture providing the AttackBuilder."""
    return AttackBuilder()


@pytest.fixture
def helpers():
    """Fixture providing TestHelpers."""
    return TestHelpers()


@pytest.fixture
def basic_attack(attack_builder):
    """Create a basic attack instance with default configuration."""
    return attack_builder.with_default_mocks().build()


@pytest.mark.usefixtures("patch_central_database")
class TestTreeOfAttacksInitialization:
    """Tests for TreeOfAttacksWithPruningAttack initialization and configuration."""

    def test_init_with_minimal_required_parameters(self, attack_builder):
        """Test that attack initializes correctly with only required parameters."""
        attack = attack_builder.with_default_mocks().build()

        assert attack._tree_width == 3
        assert attack._tree_depth == 5
        assert attack._branching_factor == 2
        assert attack._on_topic_checking_enabled is True
        assert attack._batch_size == 10

    def test_init_with_custom_tree_parameters(self, attack_builder):
        """Test initialization with custom tree parameters."""
        attack = (
            attack_builder.with_default_mocks()
            .with_tree_params(tree_width=5, tree_depth=10, branching_factor=3, batch_size=20)
            .build()
        )

        assert attack._tree_width == 5
        assert attack._tree_depth == 10
        assert attack._branching_factor == 3
        assert attack._batch_size == 20

    @pytest.mark.parametrize(
        "tree_params,expected_error",
        [
            ({"tree_width": 0}, "tree width must be at least 1"),
            ({"tree_depth": 0}, "tree depth must be at least 1"),
            ({"branching_factor": 0}, "branching factor must be at least 1"),
            ({"batch_size": 0}, "batch size must be at least 1"),
            ({"tree_width": -1}, "tree width must be at least 1"),
            ({"tree_depth": -1}, "tree depth must be at least 1"),
            ({"branching_factor": -1}, "branching factor must be at least 1"),
            ({"batch_size": -1}, "batch size must be at least 1"),
        ],
    )
    def test_init_with_invalid_tree_parameters(self, attack_builder, tree_params, expected_error):
        """Test that invalid tree parameters raise ValueError."""
        with pytest.raises(ValueError, match=expected_error):
            attack_builder.with_default_mocks().with_tree_params(**tree_params).build()

    def test_init_with_auxiliary_scorers(self, attack_builder):
        """Test initialization with auxiliary scorers."""
        attack = attack_builder.with_default_mocks().with_auxiliary_scorers(2).build()
        assert len(attack._auxiliary_scorers) == 2

    def test_init_accepts_base_attack_scoring_config(self, attack_builder):
        """Test that TAP accepts AttackScoringConfig and converts to TAPAttackScoringConfig."""
        # Set up attack builder with default mocks
        attack_builder.with_default_mocks()

        # Create a mock FloatScaleThresholdScorer with proper spec so isinstance checks pass
        mock_threshold_scorer = MagicMock(spec=FloatScaleThresholdScorer)
        mock_threshold_scorer.threshold = 0.8
        mock_threshold_scorer.scorer_type = "true_false"
        mock_threshold_scorer.score_async = AsyncMock(return_value=[])
        mock_threshold_scorer.get_identifier.return_value = ComponentIdentifier(
            class_name="FloatScaleThresholdScorer",
            class_module="pyrit.score",
        )

        # Pass base AttackScoringConfig (not TAPAttackScoringConfig)
        base_config = AttackScoringConfig(
            objective_scorer=mock_threshold_scorer,
            use_score_as_feedback=False,
        )

        adversarial_config = AttackAdversarialConfig(target=attack_builder.adversarial_chat)
        attack = TreeOfAttacksWithPruningAttack(
            objective_target=attack_builder.objective_target,
            attack_adversarial_config=adversarial_config,
            attack_scoring_config=base_config,
        )

        # Verify it was converted to TAPAttackScoringConfig
        result = attack.get_attack_scoring_config()
        assert isinstance(result, TAPAttackScoringConfig)
        assert result.threshold == 0.8
        assert result.use_score_as_feedback is False

    def test_get_objective_target_returns_correct_target(self, attack_builder):
        """Test that get_objective_target returns the target passed to constructor"""
        attack = attack_builder.with_default_mocks().build()

        assert attack.get_objective_target() == attack_builder.objective_target

    def test_get_attack_scoring_config_returns_config(self, attack_builder):
        """Test that get_attack_scoring_config returns the scoring configuration"""
        attack = attack_builder.with_default_mocks().with_auxiliary_scorers(1).with_threshold(0.75).build()

        result = attack.get_attack_scoring_config()

        assert result is not None
        assert isinstance(result, TAPAttackScoringConfig)
        # The objective_scorer should be a FloatScaleThresholdScorer (or mock of one)
        assert result.objective_scorer is not None
        assert len(result.auxiliary_scorers) == 1
        # TAPAttackScoringConfig exposes threshold property
        assert result.threshold == 0.75

    async def test_tree_depth_validation_with_prepended_conversation(self, attack_builder, helpers):
        """Test that prepended conversation turns are validated against tree_depth."""
        attack = attack_builder.with_default_mocks().with_tree_params(tree_depth=1).build()

        # Create prepended conversation with 2 assistant messages (2 turns)
        prepended = [
            Message.from_prompt(prompt="Hello", role="user"),
            Message.from_prompt(prompt="Hi there!", role="assistant"),
            Message.from_prompt(prompt="How are you?", role="user"),
            Message.from_prompt(prompt="I'm fine!", role="assistant"),
        ]
        next_message = Message.from_prompt(prompt="Continue conversation", role="user")

        # Should raise RuntimeError because prepended turns (2) exceed tree_depth (1)
        with pytest.raises(RuntimeError, match="equals or exceeds tree_depth"):
            await attack.execute_async(
                objective="Test objective",
                prepended_conversation=prepended,
                next_message=next_message,
            )

    def test_default_scorer_detects_text_output_modalities(self):
        """Test that default scorer detects text output modalities from target capabilities."""
        builder = AttackBuilder()
        builder._supports_multi_turn = True
        builder.objective_target = AttackBuilder._create_mock_target(supports_multi_turn=True)
        builder.adversarial_chat = AttackBuilder._create_mock_chat()
        # Don't set objective_scorer — let TAP create the default
        attack = TreeOfAttacksWithPruningAttack(
            objective_target=builder.objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=builder.adversarial_chat),
        )
        scorer = attack._objective_scorer._scorer
        assert "text" in scorer._validator._supported_data_types

    def test_default_scorer_detects_image_target_modalities(self):
        """Test that image target output modalities are passed to scorer validator."""
        builder = AttackBuilder()
        builder.objective_target = AttackBuilder._create_mock_target(supports_multi_turn=False)
        builder.objective_target.configuration.capabilities.output_modalities = frozenset({frozenset(["image_path"])})
        builder.adversarial_chat = AttackBuilder._create_mock_chat()

        attack = TreeOfAttacksWithPruningAttack(
            objective_target=builder.objective_target,
            attack_adversarial_config=AttackAdversarialConfig(target=builder.adversarial_chat),
        )
        scorer = attack._objective_scorer._scorer
        assert "image_path" in scorer._validator._supported_data_types


@pytest.mark.usefixtures("patch_central_database")
class TestPruningLogic:
    """Tests for node pruning functionality."""

    def test_prune_nodes_to_maintain_width_removes_lowest_scoring_nodes(self, basic_attack, node_factory, helpers):
        """Test that pruning keeps highest scoring nodes."""
        context = helpers.create_basic_context()

        # Create nodes with different scores
        nodes = node_factory.create_nodes_with_scores([0.9, 0.7, 0.5, 0.3, 0.1])
        context.nodes = nodes
        helpers.add_nodes_to_tree(context, nodes)
        basic_attack._tree_width = 3

        # Execute pruning
        basic_attack._prune_nodes_to_maintain_width(context=context)

        # Verify only top 3 nodes remain
        assert len(context.nodes) == 3
        remaining_scores = [
            node.objective_score.get_value() for node in context.nodes if node.objective_score is not None
        ]
        assert sorted(remaining_scores, reverse=True) == [0.9, 0.7, 0.5]

    def test_prune_off_topic_nodes(self, basic_attack, node_factory):
        """Test that off-topic nodes are not included in pruning consideration."""
        # Create mix of on-topic and off-topic nodes
        off_topic_node = node_factory.create_node(
            NodeMockConfig(node_id="off_topic", off_topic=True, objective_score_value=0.9)
        )

        on_topic_nodes = node_factory.create_nodes_with_scores([0.5, 0.4, 0.3])
        all_nodes = [off_topic_node] + on_topic_nodes

        # Get completed nodes
        completed = basic_attack._get_completed_nodes_sorted_by_score(all_nodes)

        # Verify off-topic node is excluded
        assert len(completed) == 3
        assert all(not node.off_topic for node in completed)

    async def test_send_prompts_adds_off_topic_and_incomplete_nodes_to_related_conversations(
        self, attack_builder, node_factory, helpers
    ):
        """Test that off-topic and incomplete nodes are added to related_conversations as PRUNED."""
        attack = attack_builder.with_default_mocks().with_prompt_normalizer().build()
        context = helpers.create_basic_context()

        # Create mix of off-topic, incomplete, and valid nodes
        off_topic_node = node_factory.create_node(
            NodeMockConfig(
                node_id="off_topic",
                off_topic=True,
                objective_score_value=0.9,
                objective_target_conversation_id="off_topic_conv",
            )
        )
        incomplete_node = node_factory.create_node(
            NodeMockConfig(
                node_id="incomplete",
                completed=False,
                objective_score_value=None,
                objective_target_conversation_id="incomplete_conv",
            )
        )
        valid_node = node_factory.create_node(
            NodeMockConfig(
                node_id="valid",
                completed=True,
                off_topic=False,
                objective_score_value=0.7,
                objective_target_conversation_id="valid_conv",
            )
        )

        context.nodes = [off_topic_node, incomplete_node, valid_node]
        context.executed_turns = 1

        # Execute sending prompts (which creates visualization nodes and tracks pruned nodes)
        await attack._send_prompts_to_all_nodes_async(context=context)

        # Verify off-topic node's conversation is tracked as PRUNED
        assert (
            ConversationReference(
                conversation_id="off_topic_conv",
                conversation_type=ConversationType.PRUNED,
            )
            in context.related_conversations
        )

        # Verify incomplete node's conversation is tracked as PRUNED
        assert (
            ConversationReference(
                conversation_id="incomplete_conv",
                conversation_type=ConversationType.PRUNED,
            )
            in context.related_conversations
        )

        # Verify valid node's conversation is NOT tracked as PRUNED
        assert (
            ConversationReference(
                conversation_id="valid_conv",
                conversation_type=ConversationType.PRUNED,
            )
            not in context.related_conversations
        )

    def test_update_best_performing_node_with_unsorted_nodes(self, basic_attack, node_factory, helpers):
        """Test that _update_best_performing_node correctly finds the best node regardless of input order."""
        context = helpers.create_basic_context()

        # Create nodes with scores in random order (not sorted)
        nodes = node_factory.create_nodes_with_scores([0.3, 0.9, 0.1, 0.7, 0.5])
        # Shuffle to ensure they're not in any particular order
        import random

        random.shuffle(nodes)
        context.nodes = nodes

        # Execute update
        basic_attack._update_best_performing_node(context)

        # Verify the best node (0.9 score) was selected
        assert context.best_objective_score is not None
        assert context.best_objective_score.get_value() == 0.9
        assert context.best_conversation_id is not None

    def test_update_best_performing_node_tracks_adversarial_conversation_id(self, basic_attack, node_factory, helpers):
        """Test that _update_best_performing_node also tracks the best adversarial conversation ID."""
        context = helpers.create_basic_context()

        # Create nodes with specific conversation IDs
        best_node = node_factory.create_node(
            NodeMockConfig(
                node_id="best",
                objective_score_value=0.9,
                objective_target_conversation_id="best_obj_conv",
                adversarial_chat_conversation_id="best_adv_conv",
            )
        )
        other_node = node_factory.create_node(
            NodeMockConfig(
                node_id="other",
                objective_score_value=0.5,
                objective_target_conversation_id="other_obj_conv",
                adversarial_chat_conversation_id="other_adv_conv",
            )
        )

        context.nodes = [other_node, best_node]  # Put best node second to verify sorting works

        # Execute update
        basic_attack._update_best_performing_node(context)

        # Verify both conversation IDs are tracked for the best node
        assert context.best_conversation_id == "best_obj_conv"
        assert context.best_adversarial_conversation_id == "best_adv_conv"

    def test_update_best_performing_node_with_empty_nodes(self, basic_attack, helpers):
        """Test that _update_best_performing_node handles empty nodes gracefully."""
        context = helpers.create_basic_context()
        context.nodes = []

        # Should return early without raising an exception
        basic_attack._update_best_performing_node(context)

        # Best scores should remain None since no nodes exist
        assert context.best_objective_score is None
        assert context.best_conversation_id is None

    def test_update_best_performing_node_with_incomplete_nodes(self, basic_attack, node_factory, helpers):
        """Test that _update_best_performing_node handles nodes without valid scores."""
        context = helpers.create_basic_context()

        # Create mix of completed and incomplete nodes
        incomplete_node = node_factory.create_node(
            NodeMockConfig(node_id="incomplete", completed=False, objective_score_value=None)
        )
        off_topic_node = node_factory.create_node(
            NodeMockConfig(node_id="off_topic", off_topic=True, objective_score_value=0.9)
        )
        no_score_node = node_factory.create_node(
            NodeMockConfig(node_id="no_score", completed=True, objective_score_value=None)
        )
        valid_node = node_factory.create_node(
            NodeMockConfig(node_id="valid", completed=True, objective_score_value=0.6)
        )

        context.nodes = [incomplete_node, off_topic_node, no_score_node, valid_node]

        # Execute update
        basic_attack._update_best_performing_node(context)

        # Should select the only valid node
        assert context.best_objective_score is not None
        assert context.best_objective_score.get_value() == 0.6
        assert context.best_conversation_id == valid_node.objective_target_conversation_id

    def test_update_best_performing_node_with_all_invalid_nodes(self, basic_attack, node_factory, helpers):
        """Test that _update_best_performing_node uses fallback when no valid nodes exist.

        When all nodes are invalid (incomplete or off-topic), the method should use the
        fallback to pick any node with a conversation_id for result reporting purposes.
        """
        context = helpers.create_basic_context()

        # Create only invalid nodes
        incomplete_node = node_factory.create_node(
            NodeMockConfig(node_id="incomplete", completed=False, objective_score_value=None)
        )
        off_topic_node = node_factory.create_node(
            NodeMockConfig(node_id="off_topic", off_topic=True, objective_score_value=0.9)
        )

        context.nodes = [incomplete_node, off_topic_node]

        # Execute update - should use fallback since no valid completed nodes
        basic_attack._update_best_performing_node(context)

        # Fallback should pick first node with a conversation_id for reporting
        assert context.best_conversation_id == incomplete_node.objective_target_conversation_id
        assert context.best_objective_score == incomplete_node.objective_score

    def test_update_best_performing_node_preserves_existing_best_when_no_valid_nodes(
        self, basic_attack, node_factory, helpers
    ):
        """Test that _update_best_performing_node preserves existing best when no new valid nodes."""
        context = helpers.create_basic_context()

        # Set existing best
        existing_score = helpers.create_score(0.8)
        context.best_objective_score = existing_score
        context.best_conversation_id = "existing_conv_id"

        # Add only invalid nodes
        off_topic_node = node_factory.create_node(
            NodeMockConfig(node_id="off_topic", off_topic=True, objective_score_value=0.95)
        )
        context.nodes = [off_topic_node]

        # Execute update
        basic_attack._update_best_performing_node(context)

        # Should preserve existing best since no valid nodes
        assert context.best_objective_score == existing_score
        assert context.best_conversation_id == "existing_conv_id"

    def test_prune_blocked_nodes_with_score_zero(self, attack_builder, node_factory, helpers):
        """Test that nodes with 'blocked' score=0 are only pruned when width exceeded."""
        attack = (
            attack_builder.with_default_mocks()
            .with_tree_params(tree_width=3)
            .with_error_score_map({"blocked": 0.0})
            .build()
        )

        context = helpers.create_basic_context()

        # Create 2 blocked nodes (score 0.0) and 2 valid nodes
        blocked_nodes = [
            node_factory.create_node(
                NodeMockConfig(
                    node_id=f"blocked_node_{i}",
                    completed=True,
                    off_topic=False,
                    objective_score_value=0.0,
                    objective_target_conversation_id=f"conv_blocked_{i}",
                )
            )
            for i in range(2)
        ]
        valid_nodes = node_factory.create_nodes_with_scores([0.8, 0.6])
        nodes = blocked_nodes + valid_nodes

        context.nodes = nodes
        helpers.add_nodes_to_tree(context, nodes)

        # Execute pruning
        attack._prune_nodes_to_maintain_width(context=context)

        # 4 completed nodes with tree_width=3 → 1 pruned (lowest score = a blocked node)
        assert len(context.nodes) == 3
        remaining_scores = sorted(
            [node.objective_score.get_value() for node in context.nodes if node.objective_score is not None],
            reverse=True,
        )
        assert remaining_scores == [0.8, 0.6, 0.0]

        pruned_nodes = [node for node in nodes if node not in context.nodes]
        assert len(pruned_nodes) == 1
        assert "Pruned (width)" in context.tree_visualization[pruned_nodes[0]._vis_node_id].tag

    def test_no_pruning_when_below_width(self, basic_attack, node_factory, helpers):
        """Test that blocked nodes are not pruned when completed list is below tree_width."""
        basic_attack._error_score_map = {"blocked": 0.0}
        basic_attack._tree_width = 5

        context = helpers.create_basic_context()

        # 2 blocked nodes (score 0.0) + 1 valid node = 3 total, below width of 5
        blocked_nodes = [
            node_factory.create_node(
                NodeMockConfig(
                    node_id=f"blocked_node_{i}",
                    completed=True,
                    off_topic=False,
                    objective_score_value=0.0,
                    objective_target_conversation_id=f"conv_blocked_{i}",
                )
            )
            for i in range(2)
        ]
        valid_node = node_factory.create_node(
            NodeMockConfig(
                node_id="valid_node", objective_score_value=0.7, objective_target_conversation_id="conv_valid"
            )
        )
        nodes = blocked_nodes + [valid_node]

        context.nodes = nodes
        helpers.add_nodes_to_tree(context, nodes)

        basic_attack._prune_nodes_to_maintain_width(context=context)

        # No pruning: 3 nodes < tree_width=5
        assert len(context.nodes) == 3
        for node in nodes:
            assert "Pruned" not in context.tree_visualization[node._vis_node_id].tag


@pytest.mark.usefixtures("patch_central_database")
class TestErrorScoreMap:
    """Tests for error_score_map functionality in TAP attack."""

    def test_default_error_score_map_maps_blocked(self, attack_builder):
        """Test that default error_score_map (None) maps 'blocked' to 0.0."""
        attack = attack_builder.with_default_mocks().build()
        assert attack._error_score_map == {"blocked": 0.0}

    def test_custom_error_score_map(self, attack_builder):
        """Test that a custom error_score_map is used as-is."""
        custom_map = {"blocked": 0.1, "unknown": 0.2}
        attack = attack_builder.with_default_mocks().with_error_score_map(custom_map).build()
        assert attack._error_score_map == custom_map

    def test_empty_error_score_map_disables_mapping(self, attack_builder):
        """Test that passing {} disables error score mapping entirely."""
        attack = attack_builder.with_default_mocks().with_error_score_map({}).build()
        assert attack._error_score_map == {}

    def test_invalid_error_score_map_key_raises(self, attack_builder):
        """Test that an invalid error type key raises ValueError."""
        with pytest.raises(ValueError, match="not a valid PromptResponseError"):
            attack_builder.with_default_mocks().with_error_score_map({"invalid_error": 0.0}).build()

    def test_out_of_range_error_score_map_value_raises(self, attack_builder):
        """Test that a score value outside [0, 1] raises ValueError."""
        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            attack_builder.with_default_mocks().with_error_score_map({"blocked": 1.5}).build()

    @pytest.mark.asyncio
    async def test_score_response_assigns_score_for_mapped_error(self, attack_builder):
        """Test that _score_response_async assigns a synthetic score for mapped errors."""
        builder = attack_builder.with_default_mocks()
        attack = builder.build()

        # Create a real _TreeOfAttacksNode with error_score_map
        adversarial_chat_seed = MagicMock(spec=SeedPrompt)
        adversarial_chat_seed.render_template_value = MagicMock(return_value="seed")
        adversarial_chat_system = MagicMock(spec=SeedPrompt)
        adversarial_chat_system.render_template_value = MagicMock(return_value="system")
        adversarial_chat_template = MagicMock(spec=SeedPrompt)
        adversarial_chat_template.render_template_value = MagicMock(return_value="template")
        normalizer = MagicMock(spec=PromptNormalizer)
        normalizer.send_prompt_async = AsyncMock(return_value=None)

        node = _TreeOfAttacksNode(
            objective_target=builder.objective_target,
            adversarial_chat=builder.adversarial_chat,
            adversarial_chat_seed_prompt=adversarial_chat_seed,
            adversarial_chat_system_seed_prompt=adversarial_chat_system,
            adversarial_chat_prompt_template=adversarial_chat_template,
            objective_scorer=builder.objective_scorer,
            on_topic_scorer=None,
            request_converters=[],
            response_converters=[],
            auxiliary_scorers=[],
            attack_id=ComponentIdentifier(class_name="Test", class_module="test"),
            attack_strategy_name="TreeOfAttacksWithPruningAttack",
            desired_response_prefix="Sure, here is",
            prompt_normalizer=normalizer,
            error_score_map={"blocked": 0.0},
        )

        # Create a Message with a blocked error
        piece = MessagePiece(
            role="assistant",
            original_value="Content blocked",
            converted_value="Content blocked",
            conversation_id=node.objective_target_conversation_id,
            response_error="blocked",
        )
        response = Message(message_pieces=[piece])

        await node._score_response_async(response=response, objective="test objective")

        assert node.objective_score is not None
        assert node.objective_score.get_value() == 0.0
        assert node.objective_score.score_type == "float_scale"
        assert "blocked" in node.objective_score.score_rationale

    @pytest.mark.asyncio
    async def test_score_response_skips_unmapped_error(self, attack_builder):
        """Test that unmapped errors still go through normal scoring path."""
        builder = attack_builder.with_default_mocks()
        attack = builder.build()

        adversarial_chat_seed = MagicMock(spec=SeedPrompt)
        adversarial_chat_seed.render_template_value = MagicMock(return_value="seed")
        adversarial_chat_system = MagicMock(spec=SeedPrompt)
        adversarial_chat_system.render_template_value = MagicMock(return_value="system")
        adversarial_chat_template = MagicMock(spec=SeedPrompt)
        adversarial_chat_template.render_template_value = MagicMock(return_value="template")
        normalizer = MagicMock(spec=PromptNormalizer)
        normalizer.send_prompt_async = AsyncMock(return_value=None)

        # error_score_map only maps "blocked", not "unknown"
        node = _TreeOfAttacksNode(
            objective_target=builder.objective_target,
            adversarial_chat=builder.adversarial_chat,
            adversarial_chat_seed_prompt=adversarial_chat_seed,
            adversarial_chat_system_seed_prompt=adversarial_chat_system,
            adversarial_chat_prompt_template=adversarial_chat_template,
            objective_scorer=builder.objective_scorer,
            on_topic_scorer=None,
            request_converters=[],
            response_converters=[],
            auxiliary_scorers=[],
            attack_id=ComponentIdentifier(class_name="Test", class_module="test"),
            attack_strategy_name="TreeOfAttacksWithPruningAttack",
            desired_response_prefix="Sure, here is",
            prompt_normalizer=normalizer,
            error_score_map={"blocked": 0.0},
        )

        # Create a Message with "unknown" error (not in map)
        piece = MessagePiece(
            role="assistant",
            original_value="Unknown error",
            converted_value="Unknown error",
            conversation_id=node.objective_target_conversation_id,
            response_error="unknown",
        )
        response = Message(message_pieces=[piece])

        # Mock Scorer.score_response_async to return objective scores
        mock_score = Score(
            score_value="0.5",
            score_value_description="test",
            score_type="float_scale",
            score_rationale="test rationale",
            message_piece_id=str(piece.id),
            scorer_class_identifier=builder.objective_scorer.get_identifier(),
            objective="test objective",
        )
        with patch.object(
            Scorer, "score_response_async", return_value={"objective_scores": [mock_score], "auxiliary_scores": []}
        ):
            await node._score_response_async(response=response, objective="test objective")

        # Should have used the normal scorer, not the error map
        assert node.objective_score is not None
        assert node.objective_score.get_value() == 0.5

    @pytest.mark.asyncio
    async def test_score_response_empty_map_disables_interception(self, attack_builder):
        """Test that empty error_score_map lets all errors through to normal scoring."""
        builder = attack_builder.with_default_mocks()

        adversarial_chat_seed = MagicMock(spec=SeedPrompt)
        adversarial_chat_seed.render_template_value = MagicMock(return_value="seed")
        adversarial_chat_system = MagicMock(spec=SeedPrompt)
        adversarial_chat_system.render_template_value = MagicMock(return_value="system")
        adversarial_chat_template = MagicMock(spec=SeedPrompt)
        adversarial_chat_template.render_template_value = MagicMock(return_value="template")
        normalizer = MagicMock(spec=PromptNormalizer)
        normalizer.send_prompt_async = AsyncMock(return_value=None)

        node = _TreeOfAttacksNode(
            objective_target=builder.objective_target,
            adversarial_chat=builder.adversarial_chat,
            adversarial_chat_seed_prompt=adversarial_chat_seed,
            adversarial_chat_system_seed_prompt=adversarial_chat_system,
            adversarial_chat_prompt_template=adversarial_chat_template,
            objective_scorer=builder.objective_scorer,
            on_topic_scorer=None,
            request_converters=[],
            response_converters=[],
            auxiliary_scorers=[],
            attack_id=ComponentIdentifier(class_name="Test", class_module="test"),
            attack_strategy_name="TreeOfAttacksWithPruningAttack",
            desired_response_prefix="Sure, here is",
            prompt_normalizer=normalizer,
            error_score_map={},
        )

        # Create a blocked response
        piece = MessagePiece(
            role="assistant",
            original_value="Content blocked",
            converted_value="Content blocked",
            conversation_id=node.objective_target_conversation_id,
            response_error="blocked",
        )
        response = Message(message_pieces=[piece])

        mock_score = Score(
            score_value="0.0",
            score_value_description="test",
            score_type="float_scale",
            score_rationale="scorer handled it",
            message_piece_id=str(piece.id),
            scorer_class_identifier=builder.objective_scorer.get_identifier(),
        )
        with patch.object(
            Scorer, "score_response_async", return_value={"objective_scores": [mock_score], "auxiliary_scores": []}
        ) as mock_scorer:
            await node._score_response_async(response=response, objective="test objective")

        # Empty map → normal scoring path was called
        mock_scorer.assert_called_once()

    def test_duplicate_preserves_error_score_map(self, attack_builder):
        """Test that duplicate() preserves the error_score_map on child nodes."""
        builder = attack_builder.with_default_mocks()

        adversarial_chat_seed = MagicMock(spec=SeedPrompt)
        adversarial_chat_seed.render_template_value = MagicMock(return_value="seed")
        adversarial_chat_system = MagicMock(spec=SeedPrompt)
        adversarial_chat_system.render_template_value = MagicMock(return_value="system")
        adversarial_chat_template = MagicMock(spec=SeedPrompt)
        adversarial_chat_template.render_template_value = MagicMock(return_value="template")
        normalizer = MagicMock(spec=PromptNormalizer)
        normalizer.send_prompt_async = AsyncMock(return_value=None)

        custom_map = {"blocked": 0.1, "unknown": 0.2}
        parent = _TreeOfAttacksNode(
            objective_target=builder.objective_target,
            adversarial_chat=builder.adversarial_chat,
            adversarial_chat_seed_prompt=adversarial_chat_seed,
            adversarial_chat_system_seed_prompt=adversarial_chat_system,
            adversarial_chat_prompt_template=adversarial_chat_template,
            objective_scorer=builder.objective_scorer,
            on_topic_scorer=None,
            request_converters=[],
            response_converters=[],
            auxiliary_scorers=[],
            attack_id=ComponentIdentifier(class_name="Test", class_module="test"),
            attack_strategy_name="TreeOfAttacksWithPruningAttack",
            desired_response_prefix="Sure, here is",
            prompt_normalizer=normalizer,
            error_score_map=custom_map,
        )

        with patch.object(parent._memory, "duplicate_conversation", return_value="new_conv_id"):
            child = parent.duplicate()

        assert child._error_score_map == custom_map
        # Verify it's a copy, not the same object
        assert child._error_score_map is not parent._error_score_map


@pytest.mark.usefixtures("patch_central_database")
class TestBranchingLogic:
    """Tests for node branching functionality."""

    def test_branch_existing_nodes(self, basic_attack, node_factory, helpers):
        """Test that nodes are branched correctly."""
        context = helpers.create_basic_context()
        basic_attack._branching_factor = 3

        # Create initial nodes
        initial_nodes = node_factory.create_nodes_with_scores([0.8, 0.7])
        helpers.add_nodes_to_tree(context, initial_nodes)
        context.nodes = initial_nodes.copy()
        context.executed_turns = 2

        # Execute branching
        basic_attack._branch_existing_nodes(context=context)

        # Verify results
        # 2 original + 4 new
        assert len(context.nodes) == 6

        # Verify duplicate was called correct number of times
        for node in initial_nodes:
            assert node.duplicate.call_count == 2


@pytest.mark.usefixtures("patch_central_database")
class TestExecutionPhase:
    """Tests for the main execution phase of the attack."""

    async def test_perform_attack_single_iteration_success(self, attack_builder, node_factory, helpers):
        """Test successful execution of single iteration."""
        attack = (
            attack_builder.with_default_mocks()
            .with_tree_params(tree_depth=1, tree_width=1)
            .with_prompt_normalizer()
            .build()
        )

        context = helpers.create_basic_context()

        # Create successful node
        success_node = node_factory.create_node(
            NodeMockConfig(
                node_id="success_node", objective_score_value=0.9, objective_target_conversation_id="success_conv"
            )
        )

        with patch.object(attack, "_create_attack_node", return_value=success_node):
            with patch.object(attack._memory, "get_message_pieces", return_value=[]):
                result = await attack._perform_async(context=context)

        assert result.outcome == AttackOutcome.SUCCESS
        assert result.conversation_id == "success_conv"
        assert result.max_depth_reached == 1

    async def test_perform_attack_early_termination_on_success(self, attack_builder, node_factory, helpers):
        """Test early termination when objective is achieved."""
        attack = (
            attack_builder.with_default_mocks()
            .with_tree_params(tree_depth=5, tree_width=1)
            .with_threshold(0.8)
            .with_prompt_normalizer()
            .build()
        )

        context = helpers.create_basic_context()

        # Create successful node
        success_node = node_factory.create_node(
            NodeMockConfig(
                node_id="success_node", objective_score_value=0.9, objective_target_conversation_id="success_conv"
            )
        )

        with patch.object(attack, "_create_attack_node", return_value=success_node):
            with patch.object(attack._memory, "get_message_pieces", return_value=[]):
                result = await attack._perform_async(context=context)

        # Should succeed after first iteration
        assert result.outcome == AttackOutcome.SUCCESS
        assert result.max_depth_reached == 1
        assert result.conversation_id == "success_conv"

    async def test_perform_attack_batch_processing(self, attack_builder, node_factory, helpers):
        """Test batch processing of nodes."""
        attack = (
            attack_builder.with_default_mocks()
            .with_tree_params(tree_width=15, batch_size=5, tree_depth=1)
            .with_prompt_normalizer()
            .build()
        )

        context = helpers.create_basic_context()
        context.executed_turns = 1

        # Create 15 mock nodes (vis nodes will be created by _send_prompts_to_all_nodes_async)
        nodes = node_factory.create_nodes_with_scores([0.5] * 15)
        context.nodes = nodes

        await attack._send_prompts_to_all_nodes_async(context=context)

        # Verify all nodes were processed
        for node in nodes:
            node.send_prompt_async.assert_called_once()

    async def test_perform_async_sets_atomic_attack_identifier(self, attack_builder, node_factory, helpers):
        """Test that _perform_async sets atomic_attack_identifier in the correct AtomicAttack format."""
        attack = (
            attack_builder.with_default_mocks()
            .with_tree_params(tree_depth=1, tree_width=1)
            .with_prompt_normalizer()
            .build()
        )

        context = helpers.create_basic_context()

        success_node = node_factory.create_node(
            NodeMockConfig(node_id="id_node", objective_score_value=0.9, objective_target_conversation_id="id_conv")
        )

        with patch.object(attack, "_create_attack_node", return_value=success_node):
            with patch.object(attack._memory, "get_message_pieces", return_value=[]):
                result = await attack._perform_async(context=context)

        assert result.atomic_attack_identifier is not None
        assert result.atomic_attack_identifier.class_name == "AtomicAttack"
        assert result.get_attack_strategy_identifier() == attack.get_identifier()


@pytest.mark.usefixtures("patch_central_database")
class TestHelperMethods:
    """Tests for helper methods."""

    def test_format_node_result(self, basic_attack, node_factory):
        """Test formatting node results for visualization."""
        # Test off-topic node
        off_topic_node = node_factory.create_node(NodeMockConfig(off_topic=True))
        result = basic_attack._format_node_result(off_topic_node)
        assert result == "Pruned (off-topic)"

        # Test incomplete node
        incomplete_node = node_factory.create_node(NodeMockConfig(completed=False))
        result = basic_attack._format_node_result(incomplete_node)
        assert result == "Pruned (no score available)"

        # Test completed node with score
        completed_node = node_factory.create_node(NodeMockConfig(objective_score_value=0.7))
        result = basic_attack._format_node_result(completed_node)
        assert "Score: " in result
        assert "/10" in result

    def test_is_objective_achieved(self, attack_builder, helpers):
        """Test _is_objective_achieved logic with mock scores."""
        attack = attack_builder.with_default_mocks().with_threshold(0.8).build()
        context = helpers.create_basic_context()

        # Test 1: No score available
        context.best_objective_score = None
        assert attack._is_objective_achieved(context=context) is False

        # Test 2: Score below threshold
        context.best_objective_score = MagicMock(get_value=MagicMock(return_value=0.5), score_metadata=None)
        assert attack._is_objective_achieved(context=context) is False

        # Test 3: Score at threshold
        context.best_objective_score = MagicMock(get_value=MagicMock(return_value=0.8), score_metadata=None)
        assert attack._is_objective_achieved(context=context) is True

        # Test 4: Score above threshold
        context.best_objective_score = MagicMock(get_value=MagicMock(return_value=0.9), score_metadata=None)
        assert attack._is_objective_achieved(context=context) is True

    def test_is_objective_achieved_with_threshold_scores(self, attack_builder, helpers):
        """Test _is_objective_achieved with realistic FloatScaleThresholdScorer output.

        This test verifies that threshold comparison correctly extracts float values
        from score_metadata when using TrueFalse scores produced by FloatScaleThresholdScorer.
        """
        attack = attack_builder.with_default_mocks().with_threshold(0.8).build()
        context = helpers.create_basic_context()

        # Test 1: TrueFalse score with original_float_value below threshold
        # Even though score_value is "False", we verify the float extraction works
        context.best_objective_score = helpers.create_threshold_score(original_float_value=0.5, threshold=0.8)
        assert attack._is_objective_achieved(context=context) is False
        # Verify normalize_score_to_float extracts the float correctly
        assert normalize_score_to_float(context.best_objective_score) == 0.5

        # Test 2: TrueFalse score with original_float_value at threshold
        context.best_objective_score = helpers.create_threshold_score(original_float_value=0.8, threshold=0.8)
        assert attack._is_objective_achieved(context=context) is True
        assert normalize_score_to_float(context.best_objective_score) == 0.8

        # Test 3: TrueFalse score with original_float_value above threshold
        context.best_objective_score = helpers.create_threshold_score(original_float_value=0.95, threshold=0.8)
        assert attack._is_objective_achieved(context=context) is True
        assert normalize_score_to_float(context.best_objective_score) == 0.95

        # Test 4: Edge case - float value just below threshold
        context.best_objective_score = helpers.create_threshold_score(original_float_value=0.79, threshold=0.8)
        assert attack._is_objective_achieved(context=context) is False
        assert normalize_score_to_float(context.best_objective_score) == 0.79

    def test_create_success_result_with_threshold_score(self, attack_builder, helpers):
        """Test that _create_success_result correctly formats score in outcome_reason."""
        attack = attack_builder.with_default_mocks().with_threshold(0.8).build()
        context = helpers.create_basic_context()

        # Set up context with a threshold score
        context.best_objective_score = helpers.create_threshold_score(original_float_value=0.92, threshold=0.8)
        context.successful_objective_conversation_id = "conv_123"

        result = attack._create_success_result(context=context)

        # Verify the outcome reason contains the float score, not "True"
        assert "0.92" in result.outcome_reason
        assert result.outcome == AttackOutcome.SUCCESS

    def test_create_failure_result_with_threshold_score(self, attack_builder, helpers):
        """Test that _create_failure_result correctly formats score in outcome_reason."""
        attack = attack_builder.with_default_mocks().with_threshold(0.8).build()
        context = helpers.create_basic_context()

        # Set up context with a threshold score below the threshold
        context.best_objective_score = helpers.create_threshold_score(original_float_value=0.65, threshold=0.8)

        result = attack._create_failure_result(context=context)

        # Verify the outcome reason contains the float score, not "False"
        assert "0.65" in result.outcome_reason
        assert result.outcome == AttackOutcome.FAILURE


@pytest.mark.usefixtures("patch_central_database")
class TestEndToEndExecution:
    """Tests for end-to-end execution using execute_async."""

    async def test_execute_async_with_message_uses_it_for_root_node(self, attack_builder, helpers):
        """Test that providing a message parameter uses it for the root node prompt."""
        attack = (
            attack_builder.with_default_mocks()
            .with_tree_params(tree_width=2, tree_depth=1)
            .with_prompt_normalizer()
            .build()
        )

        # Mock seed prompt loading
        helpers.mock_prompt_loading(attack)

        # Custom message to use for root node
        custom_message = Message.from_prompt(prompt="Custom root prompt", role="user")

        # Mock the tree execution to verify message was used
        mock_result = TAPAttackResult(
            conversation_id="test_conv_id",
            objective="Test objective",
            last_response=None,
            last_score=helpers.create_score(0.5),
            executed_turns=1,
            execution_time_ms=100,
            outcome=AttackOutcome.FAILURE,
            outcome_reason="Test",
        )
        mock_result.tree_visualization = Tree()
        mock_result.nodes_explored = 1
        mock_result.nodes_pruned = 0
        mock_result.max_depth_reached = 1
        mock_result.auxiliary_scores_summary = {}

        with patch.object(attack, "_perform_async", return_value=mock_result) as mock_perform:
            with patch.object(attack._memory, "get_conversation", return_value=[]):
                with patch.object(attack._memory, "get_message_pieces", return_value=[]):
                    with patch.object(attack._memory, "add_attack_results_to_memory", return_value=None):
                        result = await attack.execute_async(
                            objective="Test objective",
                            next_message=custom_message,
                            memory_labels={"test": "label"},
                        )

        # Verify perform_async was called and context had the message
        assert mock_perform.called
        context = mock_perform.call_args.kwargs["context"]
        assert context.next_message == custom_message
        assert isinstance(result, TAPAttackResult)

    async def test_execute_async_success_flow(self, attack_builder, helpers):
        """Test complete successful attack flow through execute_async."""
        attack = (
            attack_builder.with_default_mocks()
            .with_tree_params(tree_width=2, tree_depth=2)
            .with_prompt_normalizer()
            .build()
        )

        # Mock seed prompt loading
        helpers.mock_prompt_loading(attack)

        # Create mock result
        mock_result = TAPAttackResult(
            conversation_id="success_conv_id",
            objective="Test objective",
            last_response=None,
            last_score=helpers.create_score(0.9),
            executed_turns=1,
            execution_time_ms=100,
            outcome=AttackOutcome.SUCCESS,
            outcome_reason="Objective achieved",
        )
        # Set tree-specific properties
        mock_result.tree_visualization = Tree()
        mock_result.nodes_explored = 2
        mock_result.nodes_pruned = 0
        mock_result.max_depth_reached = 1
        mock_result.auxiliary_scores_summary = {}

        with patch.object(attack, "_perform_async", return_value=mock_result):
            with patch.object(attack._memory, "get_conversation", return_value=[]):
                with patch.object(attack._memory, "get_message_pieces", return_value=[]):
                    with patch.object(attack._memory, "add_attack_results_to_memory", return_value=None):
                        result = await attack.execute_async(objective="Test objective", memory_labels={"test": "label"})

        assert result.outcome == AttackOutcome.SUCCESS
        assert result.objective == "Test objective"
        assert isinstance(result, TAPAttackResult)
        assert result.nodes_explored > 0


@pytest.mark.usefixtures("patch_central_database")
class TestTreeOfAttacksNode:
    """Tests for _TreeOfAttacksNode functionality."""

    @pytest.fixture
    def node_components(self, attack_builder):
        """Create components needed for _TreeOfAttacksNode."""
        builder = attack_builder.with_default_mocks()

        adversarial_chat_seed_prompt = MagicMock(spec=SeedPrompt)
        adversarial_chat_seed_prompt.render_template_value = MagicMock(return_value="rendered seed prompt")

        adversarial_chat_system_seed_prompt = MagicMock(spec=SeedPrompt)
        adversarial_chat_system_seed_prompt.render_template_value = MagicMock(return_value="rendered system prompt")

        adversarial_chat_prompt_template = MagicMock(spec=SeedPrompt)
        adversarial_chat_prompt_template.render_template_value = MagicMock(return_value="rendered template")

        prompt_normalizer = MagicMock()
        prompt_normalizer.send_prompt_async = AsyncMock(return_value=None)

        return {
            "objective_target": builder.objective_target,
            "adversarial_chat": builder.adversarial_chat,
            "objective_scorer": builder.objective_scorer,
            "adversarial_chat_seed_prompt": adversarial_chat_seed_prompt,
            "adversarial_chat_system_seed_prompt": adversarial_chat_system_seed_prompt,
            "adversarial_chat_prompt_template": adversarial_chat_prompt_template,
            "desired_response_prefix": "Sure, here is",
            "on_topic_scorer": None,
            "request_converters": [],
            "response_converters": [],
            "auxiliary_scorers": [],
            "attack_id": {"id": "test_attack"},
            "attack_strategy_name": "TreeOfAttacksWithPruningAttack",
            "memory_labels": {"test": "label"},
            "parent_id": None,
            "prompt_normalizer": prompt_normalizer,
        }

    def test_node_initialization(self, node_components):
        """Test _TreeOfAttacksNode initialization."""
        node = _TreeOfAttacksNode(**node_components)

        assert node.node_id is not None
        assert node.parent_id is None
        assert node.completed is False
        assert node.off_topic is False
        assert node.objective_score is None
        assert node.auxiliary_scores == {}
        assert node.error_message is None

    def test_node_duplicate_creates_child(self, node_components):
        """Test that duplicate() creates a proper child node."""
        parent_node = _TreeOfAttacksNode(**node_components)
        parent_node.node_id = "parent_node_id"

        # Mock memory duplicate conversation
        with patch.object(parent_node._memory, "duplicate_conversation", return_value="new_conv_id"):
            child_node = parent_node.duplicate()

        assert child_node.node_id != parent_node.node_id
        assert child_node.parent_id == parent_node.node_id
        assert child_node.completed is False

    async def test_node_send_prompt_json_error_handling(self, node_components):
        """Test handling of JSON parsing errors in send_prompt_async."""
        prompt_normalizer = MagicMock(spec=PromptNormalizer)
        components_with_normalizer = node_components.copy()
        components_with_normalizer["prompt_normalizer"] = prompt_normalizer
        node = _TreeOfAttacksNode(**components_with_normalizer)

        # Mock adversarial chat to raise JSON error
        json_error = InvalidJsonException(message="Invalid JSON")
        node._adversarial_chat.send_prompt_async = AsyncMock(side_effect=json_error)

        # Mock the prompt normalizer to raise the wrapped exception
        wrapped_exception = Exception("Error sending prompt with conversation ID: id")
        prompt_normalizer.send_prompt_async = AsyncMock(side_effect=wrapped_exception)

        await node.send_prompt_async(objective="Test objective")

        # Node should handle the error gracefully
        assert node.completed is False
        assert node.error_message is not None
        assert "Error sending prompt with conversation ID" in node.error_message

    async def test_node_send_prompt_unexpected_error_handling(self, node_components):
        """Test handling of unexpected errors in send_prompt_async."""
        node = _TreeOfAttacksNode(**node_components)

        # Mock adversarial chat to raise unexpected error
        unexpected_error = RuntimeError("Unexpected error")
        node._adversarial_chat.send_prompt_async = AsyncMock(side_effect=unexpected_error)

        await node.send_prompt_async(objective="Test objective")

        # Node should handle the error gracefully
        assert node.completed is False
        assert node.error_message is not None
        assert "Execution error" in node.error_message

    async def test_node_off_topic_detection(self, node_components):
        """Test off-topic detection in nodes after retry exhaustion.

        The node should retry off-topic prompts with feedback before marking as off-topic.
        This test verifies that after all retries are exhausted, the node is marked off-topic.
        """
        # Enable on-topic checking
        on_topic_scorer = MagicMock(spec=Scorer)

        # Create a score that indicates off-topic
        on_topic_score = MagicMock(spec=Score)
        on_topic_score.get_value = MagicMock(return_value=False)  # False = off-topic
        on_topic_score.score_value = "False"
        on_topic_score.score_type = "true_false"
        on_topic_score.score_rationale = "Prompt is not relevant to the objective"
        on_topic_scorer.score_text_async = AsyncMock(return_value=[on_topic_score])

        components_with_scorer = node_components.copy()
        components_with_scorer["on_topic_scorer"] = on_topic_scorer
        components_with_scorer["adversarial_chat"].conversation_id = "test-adv-conv-id"
        components_with_scorer["objective_target"].conversation_id = "test-obj-conv-id"

        node = _TreeOfAttacksNode(**components_with_scorer)

        test_prompt = "test adversarial prompt"
        # Mock the retry attempts to 1 so it exhausts quickly
        with (
            patch.object(
                node, "_generate_single_red_teaming_prompt_async", new_callable=AsyncMock, return_value=test_prompt
            ) as red_teaming_mock,
            patch("pyrit.executor.attack.multi_turn.tree_of_attacks.get_retry_max_num_attempts", return_value=1),
            patch.object(node, "_send_to_adversarial_chat_async", new_callable=AsyncMock, return_value="new prompt"),
            patch.object(node, "_parse_red_teaming_response", return_value="new prompt"),
        ):
            await node.send_prompt_async(objective="Test objective")

        # Verify off-topic detection worked after retry exhaustion
        assert node.off_topic is True
        # Node stops execution when off-topic
        assert node.completed is False

        red_teaming_mock.assert_called_once()
        # Verify the on-topic scorer was called multiple times (initial + retries + final check)
        assert on_topic_scorer.score_text_async.call_count >= 2

    async def test_node_auxiliary_scoring(self, node_components):
        """Test auxiliary scoring functionality."""
        # Add auxiliary scorers with specific class identifiers
        aux_score1 = MagicMock()
        aux_score1.get_value.return_value = 0.8
        aux_score1.scorer_class_identifier = ComponentIdentifier(
            class_name="AuxScorer1",
            class_module="test.module",
        )
        aux_scorer1 = MagicMock(spec=Scorer)
        aux_scorer1.score_async = AsyncMock(return_value=[aux_score1])

        aux_score2 = MagicMock()
        aux_score2.get_value.return_value = 0.6
        aux_score2.scorer_class_identifier = ComponentIdentifier(
            class_name="AuxScorer2",
            class_module="test.module",
        )
        aux_scorer2 = MagicMock(spec=Scorer)
        aux_scorer2.score_async = AsyncMock(return_value=[aux_score2])

        node_components["auxiliary_scorers"] = [aux_scorer1, aux_scorer2]

        node = _TreeOfAttacksNode(**node_components)

        # Create a mock prompt normalizer if not provided
        mock_normalizer = node._prompt_normalizer

        # Mock the prompt normalizer's send_prompt_async method
        async def normalizer_side_effect(*args, **kwargs):
            target = kwargs.get("target")

            if target == node._adversarial_chat:
                # Return JSON response for adversarial chat
                return Message(
                    message_pieces=[
                        MessagePiece(
                            role="assistant",
                            original_value=json.dumps({"prompt": "test prompt", "improvement": "test"}),
                            converted_value=json.dumps({"prompt": "test prompt", "improvement": "test"}),
                            conversation_id=node.adversarial_chat_conversation_id,
                            id=str(uuid.uuid4()),
                        )
                    ]
                )
            # Return normal response for objective target
            return Message(
                message_pieces=[
                    MessagePiece(
                        role="assistant",
                        original_value="Target response",
                        converted_value="Target response",
                        conversation_id=node.objective_target_conversation_id,
                        id=str(uuid.uuid4()),
                    )
                ]
            )

        mock_normalizer.send_prompt_async = AsyncMock(side_effect=normalizer_side_effect)

        # Mocking objective scorer
        obj_score = MagicMock()
        obj_score.get_value.return_value = 0.7
        obj_score.scorer_class_identifier = ComponentIdentifier(
            class_name="ObjectiveScorer",
            class_module="test.module",
        )
        node._objective_scorer.score_async = AsyncMock(return_value=[obj_score])

        # Mock for Scorer.score_response_async
        def mock_score_response(*args, **kwargs):
            return {"objective_scores": [obj_score], "auxiliary_scores": [aux_score1, aux_score2]}

        with patch(
            "pyrit.score.Scorer.score_response_async",
            new_callable=AsyncMock,
            side_effect=mock_score_response,
        ):
            await node.send_prompt_async(objective="Test objective")

        # Verify node state
        assert node.completed is True
        assert node.error_message is None
        assert node.last_prompt_sent == "test prompt"
        assert node.last_response == "Target response"

        # Verify scores
        assert node.objective_score is not None
        assert node.objective_score == obj_score
        assert node.objective_score.get_value() == 0.7

        # Verify auxiliary scores are stored with correct keys
        assert len(node.auxiliary_scores) == 2
        assert "AuxScorer1" in node.auxiliary_scores
        assert "AuxScorer2" in node.auxiliary_scores
        assert node.auxiliary_scores["AuxScorer1"].get_value() == 0.8
        assert node.auxiliary_scores["AuxScorer2"].get_value() == 0.6

    @pytest.mark.asyncio
    async def test_node_single_turn_target_generates_new_conv_id(self, node_components):
        """Test that single-turn targets get a fresh conversation_id before each send."""
        node_components["objective_target"].capabilities.supports_multi_turn = False
        node_components["objective_target"].configuration.includes.side_effect = lambda capability: False
        node = _TreeOfAttacksNode(**node_components)

        original_conv_id = node.objective_target_conversation_id

        # Mock the adversarial chat to return valid JSON prompt
        response_piece = MessagePiece(
            role="assistant",
            original_value="response",
            converted_value="response",
            conversation_id="resp_conv",
        )
        response_msg = Message(message_pieces=[response_piece])

        node._prompt_normalizer.send_prompt_async = AsyncMock(return_value=response_msg)
        node._adversarial_chat.send_prompt_async = AsyncMock(return_value=response_msg)

        with patch.object(node, "_generate_adversarial_prompt_async", new_callable=AsyncMock, return_value="prompt"):
            with patch.object(node, "_score_response_async", new_callable=AsyncMock):
                await node._send_prompt_to_target_async("test prompt")

        # Conversation ID should have changed for single-turn target
        assert node.objective_target_conversation_id != original_conv_id

    @pytest.mark.asyncio
    async def test_node_multi_turn_target_keeps_conv_id(self, node_components):
        """Test that multi-turn targets keep the same conversation_id."""
        node_components["objective_target"].capabilities.supports_multi_turn = True
        node = _TreeOfAttacksNode(**node_components)

        original_conv_id = node.objective_target_conversation_id

        response_piece = MessagePiece(
            role="assistant",
            original_value="response",
            converted_value="response",
            conversation_id="resp_conv",
        )
        response_msg = Message(message_pieces=[response_piece])

        node._prompt_normalizer.send_prompt_async = AsyncMock(return_value=response_msg)

        await node._send_prompt_to_target_async("test prompt")

        assert node.objective_target_conversation_id == original_conv_id


@pytest.mark.usefixtures("patch_central_database")
class TestTreeOfAttacksErrorHandling:
    """Tests for error handling in TreeOfAttacksWithPruningAttack."""

    async def test_attack_handles_all_nodes_failing(self, attack_builder, helpers, node_factory):
        """Test attack behavior when all nodes fail."""
        attack = (
            attack_builder.with_default_mocks().with_tree_params(tree_width=2, tree_depth=1).with_threshold(0.8).build()
        )

        context = helpers.create_basic_context()

        # Create nodes that will all fail (no scores)
        failing_nodes = []
        for i in range(2):
            node = node_factory.create_node(
                NodeMockConfig(
                    node_id=f"failing_node_{i}",
                    completed=True,
                    off_topic=False,
                    objective_score_value=None,
                    objective_target_conversation_id=f"conv_{i}",
                )
            )
            node.error_message = "Execution failed"
            node.send_prompt_async = AsyncMock(return_value=None)
            failing_nodes.append(node)

        # Use an iterator to return nodes one by one
        node_iterator = iter(failing_nodes)

        with patch.object(attack, "_create_attack_node", side_effect=lambda **kwargs: next(node_iterator)):
            with patch.object(attack._memory, "get_message_pieces", return_value=[]):
                result = await attack._perform_async(context=context)

        # Should return failure when all nodes fail
        assert result.outcome == AttackOutcome.FAILURE
        # The actual message is about not achieving threshold score
        assert "did not achieve threshold score" in result.outcome_reason.lower()

    async def test_attack_continues_after_node_errors(self, attack_builder, node_factory, helpers):
        """Test that attack continues when some nodes have errors."""
        attack = (
            attack_builder.with_default_mocks().with_tree_params(tree_width=3, tree_depth=2, branching_factor=2).build()
        )

        context = helpers.create_basic_context()

        # Create mix of successful and failing nodes for first iteration
        nodes = []

        # Failing node
        fail_node = node_factory.create_node(
            NodeMockConfig(
                node_id="fail_node",
                completed=True,
                off_topic=False,
                objective_score_value=None,  # Explicitly set to None for failure
                objective_target_conversation_id="fail_conv",
            )
        )
        fail_node.error_message = "JSON parsing error"
        fail_node.duplicate = MagicMock(
            return_value=node_factory.create_node(NodeMockConfig(node_id="fail_dup", objective_score_value=0.3))
        )
        nodes.append(fail_node)

        # Successful nodes
        for i in range(2):
            success_node = node_factory.create_node(
                NodeMockConfig(node_id=f"success_node_{i}", objective_score_value=0.5 + i * 0.1)
            )
            nodes.append(success_node)

        # Create all nodes at once
        node_iter = iter(nodes)
        with patch.object(attack, "_create_attack_node", side_effect=lambda **kwargs: next(node_iter, nodes[0])):
            with patch.object(attack._memory, "get_message_pieces", return_value=[]):
                result = await attack._perform_async(context=context)

        # Attack should continue despite some nodes failing
        assert result.outcome == AttackOutcome.FAILURE  # Did not reach threshold
        assert result.max_depth_reached == 2


@pytest.mark.usefixtures("patch_central_database")
class TestTreeOfAttacksMemoryOperations:
    """Tests for memory-related operations."""

    def test_setup_with_prepended_conversation_without_next_message(self, attack_builder, helpers):
        """Test that setup works with prepended_conversation even without next_message.

        The adversarial chat will generate the first prompt based on the prepended
        conversation context. This is similar to a fresh attack, but with context.
        """
        attack = attack_builder.with_default_mocks().build()

        # Create context with prepended_conversation but no next_message
        prepended = [
            Message.from_prompt(prompt="Hello", role="user"),
            Message.from_prompt(prompt="Hi there!", role="assistant"),
        ]
        context = helpers.create_basic_context()
        context.prepended_conversation = prepended
        context.next_message = None  # Explicitly no next_message

        # Setup should succeed without raising an error
        asyncio.run(attack._setup_async(context=context))

        # Verify setup completed - tree visualization initialized
        assert context.tree_visualization is not None
        assert context.next_message is None  # No custom message, adversarial chat will generate

    def test_attack_updates_memory_labels(self, attack_builder, helpers):
        """Test that memory labels are properly combined."""
        attack = attack_builder.with_default_mocks().build()

        # Set initial memory labels on attack
        attack._memory_labels = {"attack_label": "attack_value"}

        context = helpers.create_basic_context()
        context.memory_labels = {"context_label": "context_value"}

        # Run setup to combine labels
        asyncio.run(attack._setup_async(context=context))

        # Verify labels are combined
        assert context.memory_labels["attack_label"] == "attack_value"
        assert context.memory_labels["context_label"] == "context_value"


@pytest.mark.usefixtures("patch_central_database")
class TestTreeOfAttacksPromptLoading:
    """Tests for prompt loading and handling."""

    def test_load_adversarial_prompts_default(self, attack_builder):
        """Test loading prompts with default paths."""
        attack = attack_builder.with_default_mocks().build()

        # Mock SeedPrompt loading
        mock_system = MagicMock(spec=SeedPrompt)
        mock_template = MagicMock(spec=SeedPrompt)
        mock_seed = MagicMock(spec=SeedPrompt)

        with patch.object(SeedPrompt, "from_yaml_with_required_parameters", return_value=mock_system):
            with patch.object(SeedPrompt, "from_yaml_file", side_effect=[mock_template, mock_seed]):
                attack._load_adversarial_prompts()

        # Verify prompts were loaded and stored
        assert attack._adversarial_chat_system_seed_prompt == mock_system
        assert attack._adversarial_chat_prompt_template == mock_template
        assert attack._adversarial_chat_seed_prompt == mock_seed


@pytest.mark.usefixtures("patch_central_database")
class TestTreeOfAttacksVisualization:
    """Tests for tree visualization functionality."""

    def test_format_node_result_with_scores(self, basic_attack):
        """Test formatting node results with different score formats."""
        node = MagicMock()
        node.off_topic = False
        node.completed = True
        node.objective_score = MagicMock(get_value=MagicMock(return_value=0.7), score_metadata=None)

        result = basic_attack._format_node_result(node)

        # The actual format uses integer division
        assert "Score: " in result
        assert "/10" in result
        # Should show as 7/10, not 7.0/10
        assert "7/10" in result

    def test_tree_visualization_structure(self, basic_attack, node_factory, helpers):
        """Test that tree visualization maintains proper depth-as-children structure."""
        context = helpers.create_basic_context()

        # Create a tree structure where each depth is a child of the previous:
        # root
        # ├── node_0_d1 (depth 1)
        # │   ├── node_0_child_0_d2 (depth 2, branched from node_0)
        # │   └── node_0_child_1_d2 (depth 2, branched from node_0)
        # └── node_1_d1 (depth 1)
        #     └── node_1_child_0_d2 (depth 2, branched from node_1)

        # First level nodes
        node_0 = node_factory.create_node(NodeMockConfig(node_id="node_0"))
        node_1 = node_factory.create_node(NodeMockConfig(node_id="node_1"))

        # Add first level to tree (simulates _send_prompts creating vis nodes)
        context.executed_turns = 1
        helpers.add_nodes_to_tree(context, [node_0, node_1])

        # Second level nodes (branched from first level)
        node_0_child_0 = node_factory.create_node(NodeMockConfig(node_id="node_0_child_0", parent_id="node_0"))
        node_0_child_1 = node_factory.create_node(NodeMockConfig(node_id="node_0_child_1", parent_id="node_0"))
        node_1_child_0 = node_factory.create_node(NodeMockConfig(node_id="node_1_child_0", parent_id="node_1"))

        # Add second level as children of the first level's vis nodes
        context.executed_turns = 2
        helpers.add_nodes_to_tree(context, [node_0_child_0, node_0_child_1], parent=node_0._vis_node_id)
        helpers.add_nodes_to_tree(context, [node_1_child_0], parent=node_1._vis_node_id)

        # Verify tree structure
        assert len(context.tree_visualization.all_nodes()) == 6  # root + 5 nodes
        assert len(context.tree_visualization.children("root")) == 2
        assert len(context.tree_visualization.children(node_0._vis_node_id)) == 2
        assert len(context.tree_visualization.children(node_1._vis_node_id)) == 1

        # Verify the parent relationships: depth-2 nodes are children of depth-1 vis nodes
        assert context.tree_visualization.parent(node_0_child_0._vis_node_id).identifier == node_0._vis_node_id
        assert context.tree_visualization.parent(node_0_child_1._vis_node_id).identifier == node_0._vis_node_id
        assert context.tree_visualization.parent(node_1_child_0._vis_node_id).identifier == node_1._vis_node_id

    @pytest.mark.asyncio
    async def test_surviving_node_gets_child_vis_nodes_per_depth(self, attack_builder, node_factory, helpers):
        """Test that a surviving node gets a new child vis node at each depth (not appended scores)."""
        attack = (
            attack_builder.with_default_mocks()
            .with_tree_params(tree_width=1, tree_depth=3, branching_factor=1)
            .with_prompt_normalizer()
            .build()
        )

        context = helpers.create_basic_context()
        context.executed_turns = 1

        # Create one node that survives all depths
        node = node_factory.create_node(NodeMockConfig(node_id="survivor", objective_score_value=0.5))
        context.nodes = [node]

        # Depth 1: send_prompts creates vis node under root
        await attack._send_prompts_to_all_nodes_async(context=context)
        depth1_vis = node._vis_node_id
        assert depth1_vis == "survivor_d1"
        assert context.tree_visualization.parent(depth1_vis).identifier == "root"
        assert "Score:" in context.tree_visualization[depth1_vis].tag
        # No appended scores — just one score
        assert context.tree_visualization[depth1_vis].tag.count("Score:") == 1

        # Depth 2: surviving node gets a NEW child vis node
        context.executed_turns = 2
        await attack._send_prompts_to_all_nodes_async(context=context)
        depth2_vis = node._vis_node_id
        assert depth2_vis == "survivor_d2"
        assert context.tree_visualization.parent(depth2_vis).identifier == depth1_vis
        # Each vis node still has exactly one score
        assert context.tree_visualization[depth1_vis].tag.count("Score:") == 1
        assert context.tree_visualization[depth2_vis].tag.count("Score:") == 1

        # Depth 3: another new child vis node
        context.executed_turns = 3
        await attack._send_prompts_to_all_nodes_async(context=context)
        depth3_vis = node._vis_node_id
        assert depth3_vis == "survivor_d3"
        assert context.tree_visualization.parent(depth3_vis).identifier == depth2_vis

        # No node has "Score: ... || Score:" pattern (the old appended format)
        for tree_node in context.tree_visualization.all_nodes():
            assert "|| Score:" not in tree_node.tag


@pytest.mark.usefixtures("patch_central_database")
class TestTreeOfAttacksConversationTracking:
    """Test that adversarial chat conversation IDs are properly tracked."""

    def test_create_attack_node_tracks_adversarial_chat_conversation_id(self, basic_attack, helpers):
        """Test that creating a node adds its adversarial chat conversation ID to the context."""
        context = helpers.create_basic_context()

        # Create a node
        node = basic_attack._create_attack_node(context=context, parent_id=None)

        # Verify the adversarial chat conversation ID is tracked
        assert (
            ConversationReference(
                conversation_id=node.adversarial_chat_conversation_id,
                conversation_type=ConversationType.ADVERSARIAL,
            )
            in context.related_conversations
        )
        assert len(context.related_conversations) == 1

    def test_branch_existing_nodes_tracks_adversarial_chat_conversation_ids(self, basic_attack, node_factory, helpers):
        """Test that branching nodes adds their adversarial chat conversation IDs to the context."""
        context = helpers.create_basic_context()

        # Create initial nodes
        nodes = node_factory.create_nodes_with_scores([0.8, 0.9])
        context.nodes = nodes

        # Add the initial nodes to the tree visualization and set their _vis_node_id
        context.executed_turns = 1
        helpers.add_nodes_to_tree(context, nodes)

        # Set up branching factor to create additional nodes
        basic_attack._branching_factor = 3

        # Branch the nodes
        basic_attack._branch_existing_nodes(context)

        # Manually add all node adversarial chat conversation IDs to the set (simulating real code behavior)
        for node in context.nodes:
            context.related_conversations.add(
                ConversationReference(
                    conversation_id=node.adversarial_chat_conversation_id,
                    conversation_type=ConversationType.ADVERSARIAL,
                )
            )

        # Verify that adversarial chat conversation IDs are tracked for duplicated nodes
        expected_count = 6  # 2 originals + 4 unique duplicates (2 nodes * (3-1) branching factor)
        assert len(context.related_conversations) == expected_count

        # Verify all nodes have their adversarial chat conversation IDs tracked
        all_nodes = context.nodes
        for node in all_nodes:
            assert (
                ConversationReference(
                    conversation_id=node.adversarial_chat_conversation_id,
                    conversation_type=ConversationType.ADVERSARIAL,
                )
                in context.related_conversations
            )

    def test_initialize_first_level_nodes_tracks_adversarial_chat_conversation_ids(self, basic_attack, helpers):
        """Test that initializing first level nodes tracks their adversarial chat conversation IDs."""
        context = helpers.create_basic_context()

        # Set tree width to create multiple nodes
        basic_attack._tree_width = 3

        # Initialize first level nodes
        asyncio.run(basic_attack._initialize_first_level_nodes_async(context))

        # Verify that adversarial chat conversation IDs are tracked
        assert len(context.related_conversations) == 3

        # Verify all nodes have their adversarial chat conversation IDs tracked
        for node in context.nodes:
            assert (
                ConversationReference(
                    conversation_id=node.adversarial_chat_conversation_id,
                    conversation_type=ConversationType.ADVERSARIAL,
                )
                in context.related_conversations
            )

    def test_attack_result_includes_adversarial_chat_conversation_ids(self, attack_builder, helpers):
        """Test that the attack result includes the tracked adversarial chat conversation IDs."""
        attack = attack_builder.with_default_mocks().build()
        context = helpers.create_basic_context()

        # Create some nodes to populate the tracking
        context.related_conversations = {
            ConversationReference(conversation_id="adv_conv_1", conversation_type=ConversationType.ADVERSARIAL),
            ConversationReference(conversation_id="adv_conv_2", conversation_type=ConversationType.ADVERSARIAL),
        }
        context.best_conversation_id = "best_conv"
        context.best_objective_score = helpers.create_score(0.9)

        # Create the result
        result = attack._create_attack_result(
            context=context, outcome=AttackOutcome.SUCCESS, outcome_reason="Test success"
        )

        # Verify the adversarial chat conversation IDs are included in the result
        assert (
            ConversationReference(
                conversation_id="adv_conv_1",
                conversation_type=ConversationType.ADVERSARIAL,
            )
            in result.related_conversations
        )
        assert (
            ConversationReference(
                conversation_id="adv_conv_2",
                conversation_type=ConversationType.ADVERSARIAL,
            )
            in result.related_conversations
        )

    def test_add_adversarial_chat_conversation_id_ensures_uniqueness(self, basic_attack, helpers):
        """Test that adding adversarial chat conversation IDs ensures uniqueness."""
        context = helpers.create_basic_context()

        # Add a conversation ID
        conversation_id = "test_conv_id"
        context.related_conversations.add(
            ConversationReference(
                conversation_id=conversation_id,
                conversation_type=ConversationType.ADVERSARIAL,
            )
        )

        # Verify it was added
        assert (
            ConversationReference(
                conversation_id=conversation_id,
                conversation_type=ConversationType.ADVERSARIAL,
            )
            in context.related_conversations
        )
        assert len(context.related_conversations) == 1

        # Try to add the same ID again
        context.related_conversations.add(
            ConversationReference(
                conversation_id=conversation_id,
                conversation_type=ConversationType.ADVERSARIAL,
            )
        )

        # Verify it's still only one entry
        assert len(context.related_conversations) == 1

        # Add a different ID
        different_id = "different_conv_id"
        context.related_conversations.add(
            ConversationReference(
                conversation_id=different_id,
                conversation_type=ConversationType.ADVERSARIAL,
            )
        )

        # Verify both IDs are present
        assert len(context.related_conversations) == 2
        assert (
            ConversationReference(
                conversation_id=conversation_id,
                conversation_type=ConversationType.ADVERSARIAL,
            )
            in context.related_conversations
        )
        assert (
            ConversationReference(
                conversation_id=different_id,
                conversation_type=ConversationType.ADVERSARIAL,
            )
            in context.related_conversations
        )


def test_tap_init_raises_when_objective_scorer_is_none():
    """Test that TAP __init__ raises ValueError when AttackScoringConfig has objective_scorer=None."""
    scoring_config = AttackScoringConfig(objective_scorer=None)
    with pytest.raises(ValueError, match="objective_scorer is required"):
        TreeOfAttacksWithPruningAttack(
            objective_target=MagicMock(spec=PromptChatTarget),
            attack_adversarial_config=MagicMock(
                target=MagicMock(spec=PromptChatTarget),
                system_prompt_path=None,
            ),
            attack_scoring_config=scoring_config,
        )


def test_tap_attack_result_tree_visualization_getter_returns_value():
    """Test that TAPAttackResult.tree_visualization returns the stored tree."""
    tree = Tree()
    tree.create_node("root", "root")
    result = TAPAttackResult(
        conversation_id="conv1",
        objective="test",
    )
    result.metadata["tree_visualization"] = tree
    assert result.tree_visualization is tree


def test_tap_attack_result_tree_visualization_getter_returns_none_when_missing():
    """Test that TAPAttackResult.tree_visualization returns None when not set."""
    result = TAPAttackResult(
        conversation_id="conv1",
        objective="test",
    )
    assert result.tree_visualization is None


# ---------------------------------------------------------------------------
# Scenario-driven end-to-end TAP simulation tests
# ---------------------------------------------------------------------------
# Each scenario is a compact dict describing the behavior of every node at
# every depth.  The test harness wires up mocked nodes whose send_prompt_async
# applies the prescribed behavior (score, error, off-topic, json-error), then
# runs _perform_async and asserts the expected outcome, best score, depth, and
# node / prune counts.
#
# Node behaviors per depth are lists (one entry per node in that depth).
# Allowed behavior keys:
#   score     – float objective score (node completes successfully)
#   error     – response_error string triggering error_score_map (e.g. "blocked")
#   fail      – True → node raises an exception (unexpected error)
#   off_topic – True → node is off-topic after retries
#   json_err  – True → node raises InvalidJsonException
# ---------------------------------------------------------------------------


@dataclass
class _ScenarioNodeBehavior:
    """Compact description of how a node should behave during send_prompt_async."""

    score: float | None = None
    error: str | None = None
    fail: bool = False
    off_topic: bool = False
    json_err: bool = False


def _make_node_with_behavior(behavior: _ScenarioNodeBehavior, node_id: str) -> _TreeOfAttacksNode:
    """Create a mock node that applies the given behavior during send_prompt_async."""
    _call_behaviors: list[_ScenarioNodeBehavior] = [behavior]

    async def _send_prompt(objective: str) -> None:
        # Use the latest behavior (may be updated for re-execution at deeper depths)
        b = _call_behaviors[-1]
        # Reset state for re-execution
        node.completed = False
        node.off_topic = False
        node.objective_score = None
        node.error_message = None
        if b.json_err:
            node.error_message = "JSON parsing error"
            return
        if b.fail:
            node.error_message = "Execution error: simulated failure"
            return
        if b.off_topic:
            node.off_topic = True
            return
        # Normal completion
        node.completed = True
        if b.error is not None:
            node.objective_score = MagicMock(
                get_value=MagicMock(return_value=0.0),
                score_metadata=None,
            )
        elif b.score is not None:
            node.objective_score = MagicMock(
                get_value=MagicMock(return_value=b.score),
                score_metadata=None,
            )

    node = MagicMock()
    node.node_id = node_id
    node.parent_id = None
    node._vis_node_id = "root"
    node.completed = False
    node.off_topic = False
    node.objective_score = None
    node.auxiliary_scores = {}
    node.error_message = None
    node.last_prompt_sent = None
    node.last_response = None
    node.objective_target_conversation_id = str(uuid.uuid4())
    node.adversarial_chat_conversation_id = str(uuid.uuid4())
    node.send_prompt_async = AsyncMock(side_effect=_send_prompt)
    node.duplicate = MagicMock()  # replaced per-depth below
    node._call_behaviors = _call_behaviors  # expose for re-programming
    return node


# Scenario parameters: tree_width, tree_depth, branching_factor, threshold,
# error_score_map, behaviors_per_depth, expected_outcome, expected_best_score, expected_max_depth

_B = _ScenarioNodeBehavior  # shorthand

_SCENARIOS = [
    pytest.param(
        # Scenario: immediate success on depth 1
        2,
        4,
        2,
        0.7,
        {"blocked": 0.0},
        {1: [_B(score=0.9), _B(score=0.3)]},
        AttackOutcome.SUCCESS,
        0.9,
        1,
        id="immediate_success_depth1",
    ),
    pytest.param(
        # Scenario: all nodes blocked on depth 1, branches survive, succeed on depth 2
        2,
        4,
        2,
        0.7,
        {"blocked": 0.0},
        {
            1: [_B(error="blocked"), _B(error="blocked")],
            2: [_B(score=0.8), _B(score=0.5), _B(score=0.2), _B(score=0.1)],
        },
        AttackOutcome.SUCCESS,
        0.8,
        2,
        id="blocked_depth1_recovers_depth2",
    ),
    pytest.param(
        # Scenario: mixed — one blocked, one scores low; depth 2 one succeeds
        2,
        4,
        2,
        0.7,
        {"blocked": 0.0},
        {
            1: [_B(error="blocked"), _B(score=0.3)],
            2: [_B(score=0.75), _B(fail=True), _B(score=0.4), _B(off_topic=True)],
        },
        AttackOutcome.SUCCESS,
        0.75,
        2,
        id="mixed_errors_and_success",
    ),
    pytest.param(
        # Scenario: all fail across all 4 depths → failure
        2,
        4,
        2,
        0.7,
        {"blocked": 0.0},
        {
            1: [_B(fail=True), _B(json_err=True)],
            # No surviving nodes → all pruned after depth 1
        },
        AttackOutcome.FAILURE,
        0.0,
        1,
        id="all_fail_all_depths",
    ),
    pytest.param(
        # Scenario: gradual improvement over 3 depths (width=2, branching=2 → 4 nodes/depth)
        # After pruning to width 2, only the top 2 survive.
        # Original survivors get re-executed + branching_factor-1 new duplicates.
        2,
        3,
        2,
        0.7,
        {"blocked": 0.0},
        {
            1: [_B(score=0.2), _B(score=0.3)],
            2: [_B(score=0.5), _B(score=0.4), _B(error="blocked"), _B(score=0.35)],
            3: [_B(score=0.75), _B(score=0.6), _B(json_err=True), _B(off_topic=True)],
        },
        AttackOutcome.SUCCESS,
        0.75,
        3,
        id="gradual_improvement_succeeds_depth3",
    ),
    pytest.param(
        # Scenario: close but never reaches threshold across 3 depths
        2,
        3,
        2,
        0.7,
        {"blocked": 0.0},
        {
            1: [_B(score=0.2), _B(score=0.3)],
            2: [_B(score=0.4), _B(score=0.5), _B(score=0.45), _B(score=0.35)],
            3: [_B(score=0.6), _B(score=0.65), _B(score=0.55), _B(score=0.68)],
        },
        AttackOutcome.FAILURE,
        0.65,
        3,
        id="close_but_never_reaches_threshold",
    ),
    pytest.param(
        # Scenario: error_score_map disabled (empty), blocked = immediate prune
        2,
        4,
        2,
        0.7,
        {},
        {
            1: [_B(error="blocked"), _B(error="blocked")],
            # With empty error_score_map, blocked nodes don't get scores → pruned
        },
        AttackOutcome.FAILURE,
        0.0,
        2,
        id="empty_error_map_blocked_prunes_all",
    ),
    pytest.param(
        # Scenario: off-topic nodes recovered by siblings
        2,
        4,
        2,
        0.7,
        {"blocked": 0.0},
        {
            1: [_B(off_topic=True), _B(score=0.4)],
            2: [_B(score=0.8), _B(score=0.5)],
        },
        AttackOutcome.SUCCESS,
        0.8,
        2,
        id="off_topic_sibling_recovers",
    ),
]


@pytest.mark.usefixtures("patch_central_database")
class TestTAPScenarios:
    """Scenario-driven tests exercising the full TAP execution loop with mocked nodes.

    Each scenario is run twice: once with a multi-turn target and once with a single-turn target.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("supports_multi_turn", [True, False], ids=["multi_turn", "single_turn"])
    @pytest.mark.parametrize(
        "tree_width, tree_depth, branching_factor, threshold, error_score_map, "
        "behaviors_per_depth, expected_outcome, expected_best_score, expected_max_depth",
        _SCENARIOS,
    )
    async def test_tap_scenario(
        self,
        attack_builder,
        helpers,
        supports_multi_turn,
        tree_width,
        tree_depth,
        branching_factor,
        threshold,
        error_score_map,
        behaviors_per_depth,
        expected_outcome,
        expected_best_score,
        expected_max_depth,
    ):
        attack = (
            attack_builder.with_supports_multi_turn(supports_multi_turn)
            .with_default_mocks()
            .with_tree_params(
                tree_width=tree_width,
                tree_depth=tree_depth,
                branching_factor=branching_factor,
            )
            .with_threshold(threshold)
            .with_error_score_map(error_score_map)
            .with_prompt_normalizer()
            .build()
        )

        # Track per-depth behavior counters
        _depth_counters: dict[int, int] = {}

        def _get_next_behavior(depth: int) -> _ScenarioNodeBehavior:
            if depth not in _depth_counters:
                _depth_counters[depth] = 0
            behaviors = behaviors_per_depth.get(depth, [])
            idx = _depth_counters[depth]
            _depth_counters[depth] += 1
            return behaviors[idx] if idx < len(behaviors) else _B(fail=True)

        def _make_nodes_for_depth(depth: int, count: int) -> list:
            nodes = []
            for i in range(count):
                b = _get_next_behavior(depth)
                node = _make_node_with_behavior(b, f"d{depth}_n{i}")
                next_depth = depth + 1

                def _dup_factory(parent=node, d=next_depth):
                    # Reprogram parent for re-execution at next depth
                    parent._call_behaviors.append(_get_next_behavior(d))
                    # Create child with its own behavior
                    cb = _get_next_behavior(d)
                    child = _make_node_with_behavior(cb, f"d{d}_n{_depth_counters.get(d, 0) - 1}")
                    child.parent_id = parent.node_id
                    child._vis_node_id = parent._vis_node_id
                    child.duplicate = MagicMock(side_effect=lambda p=child, dd=d + 1: _dup_child(p, dd))
                    return child

                def _dup_child(parent_node, d):
                    parent_node._call_behaviors.append(_get_next_behavior(d))
                    cb = _get_next_behavior(d)
                    child = _make_node_with_behavior(cb, f"d{d}_n{_depth_counters.get(d, 0) - 1}")
                    child.parent_id = parent_node.node_id
                    child._vis_node_id = parent_node._vis_node_id
                    child.duplicate = MagicMock(side_effect=lambda p=child, dd=d + 1: _dup_child(p, dd))
                    return child

                node.duplicate = MagicMock(side_effect=_dup_factory)
                nodes.append(node)
            return nodes

        # Mock _create_attack_node to produce nodes with prescribed behaviors
        depth1_nodes = _make_nodes_for_depth(1, tree_width)
        _depth1_idx = [0]

        def _create_node_side_effect(**kwargs):
            if _depth1_idx[0] < len(depth1_nodes):
                node = depth1_nodes[_depth1_idx[0]]
                _depth1_idx[0] += 1
            else:
                node = _make_node_with_behavior(_B(fail=True), f"extra_{_depth1_idx[0]}")
                _depth1_idx[0] += 1
            return node

        context = helpers.create_basic_context()

        with patch.object(attack, "_create_attack_node", side_effect=_create_node_side_effect):
            with patch.object(attack._memory, "get_message_pieces", return_value=[]):
                result = await attack._perform_async(context=context)

        assert result.outcome == expected_outcome, (
            f"Expected {expected_outcome}, got {result.outcome}. "
            f"Best score: {context.best_objective_score.get_value() if context.best_objective_score else 'None'}"
        )
        assert result.max_depth_reached == expected_max_depth

        if expected_best_score > 0:
            assert context.best_objective_score is not None
            assert abs(context.best_objective_score.get_value() - expected_best_score) < 0.01, (
                f"Expected best score ~{expected_best_score}, got {context.best_objective_score.get_value()}"
            )
