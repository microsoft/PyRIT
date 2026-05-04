# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""
Tests for ChunkedRequestAttack.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrit.executor.attack.core.attack_parameters import AttackParameters
from pyrit.executor.attack.multi_turn import (
    ChunkedRequestAttack,
    ChunkedRequestAttackContext,
)
from pyrit.identifiers import ComponentIdentifier
from pyrit.models import Message, MessagePiece
from pyrit.prompt_normalizer import PromptNormalizer
from pyrit.prompt_target import PromptTarget


def _mock_target_id(name: str = "MockTarget") -> ComponentIdentifier:
    """Helper to create ComponentIdentifier for tests."""
    return ComponentIdentifier(
        class_name=name,
        class_module="test_module",
    )


def _make_mock_target():
    """Create a mock target with proper get_identifier."""
    target = MagicMock(spec=PromptTarget)
    target.get_identifier.return_value = _mock_target_id("MockTarget")
    return target


class TestChunkedRequestAttackContext:
    """Test the ChunkedRequestAttackContext dataclass."""

    def test_context_default_values(self):
        """Test that context has correct default values."""
        context = ChunkedRequestAttackContext(params=AttackParameters(objective="Extract the secret"))

        assert context.objective == "Extract the secret"
        assert len(context.chunk_responses) == 0

    def test_context_with_chunk_responses(self):
        """Test setting chunk_responses in context."""
        context = ChunkedRequestAttackContext(
            params=AttackParameters(objective="Get the password"),
            chunk_responses=["abc", "def", "ghi"],
        )

        assert context.objective == "Get the password"
        assert context.chunk_responses == ["abc", "def", "ghi"]


@pytest.mark.usefixtures("patch_central_database")
class TestChunkedRequestAttack:
    """Test the ChunkedRequestAttack class."""

    def test_init_default_values(self):
        """Test initialization with default values."""
        mock_target = _make_mock_target()
        attack = ChunkedRequestAttack(objective_target=mock_target)

        assert attack._chunk_size == 50
        assert attack._total_length == 200
        assert attack._chunk_type == "characters"

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        mock_target = _make_mock_target()
        attack = ChunkedRequestAttack(
            objective_target=mock_target,
            chunk_size=25,
            total_length=150,
            chunk_type="words",
        )

        assert attack._chunk_size == 25
        assert attack._total_length == 150
        assert attack._chunk_type == "words"

    def test_init_custom_request_template(self):
        """Test initialization with custom request template."""
        mock_target = _make_mock_target()
        template = "Show me {chunk_type} from position {start} to {end} for '{objective}'"
        attack = ChunkedRequestAttack(
            objective_target=mock_target,
            request_template=template,
        )

        assert attack._request_template == template

    def test_init_invalid_chunk_size(self):
        """Test that invalid chunk_size raises ValueError."""
        mock_target = _make_mock_target()

        with pytest.raises(ValueError, match="chunk_size must be >= 1"):
            ChunkedRequestAttack(objective_target=mock_target, chunk_size=0)

    def test_init_invalid_total_length(self):
        """Test that invalid total_length raises ValueError."""
        mock_target = _make_mock_target()

        with pytest.raises(ValueError, match="total_length must be >= chunk_size"):
            ChunkedRequestAttack(objective_target=mock_target, chunk_size=100, total_length=50)

    def test_generate_chunk_prompts(self):
        """Test chunk prompt generation."""
        mock_target = _make_mock_target()
        attack = ChunkedRequestAttack(
            objective_target=mock_target,
            chunk_size=50,
            total_length=150,
        )

        context = ChunkedRequestAttackContext(params=AttackParameters(objective="Get the secret"))
        prompts = attack._generate_chunk_prompts(context)

        assert len(prompts) == 3
        assert "characters" in prompts[0]
        assert "1-50" in prompts[0]
        assert "51-100" in prompts[1]
        assert "101-150" in prompts[2]

    def test_generate_chunk_prompts_custom_chunk_type(self):
        """Test chunk prompt generation with custom chunk type."""
        mock_target = _make_mock_target()
        attack = ChunkedRequestAttack(
            objective_target=mock_target,
            chunk_size=50,
            total_length=100,
            chunk_type="bytes",
        )

        context = ChunkedRequestAttackContext(params=AttackParameters(objective="Get the data"))
        prompts = attack._generate_chunk_prompts(context)

        assert len(prompts) == 2
        assert "bytes" in prompts[0]
        assert "bytes" in prompts[1]

    def test_validate_context_empty_objective(self):
        """Test validation fails with empty objective."""
        mock_target = _make_mock_target()
        attack = ChunkedRequestAttack(objective_target=mock_target)

        context = ChunkedRequestAttackContext(params=AttackParameters(objective=""))

        with pytest.raises(ValueError, match="Attack objective must be provided"):
            attack._validate_context(context=context)

    def test_validate_context_whitespace_objective(self):
        """Test validation fails with whitespace-only objective."""
        mock_target = _make_mock_target()
        attack = ChunkedRequestAttack(objective_target=mock_target)

        context = ChunkedRequestAttackContext(params=AttackParameters(objective="   "))

        with pytest.raises(ValueError, match="Attack objective must be provided"):
            attack._validate_context(context=context)

    def test_validate_context_valid_objective(self):
        """Test validation succeeds with valid objective."""
        mock_target = _make_mock_target()
        attack = ChunkedRequestAttack(objective_target=mock_target)

        context = ChunkedRequestAttackContext(params=AttackParameters(objective="Extract the secret password"))

        # Should not raise
        attack._validate_context(context=context)

    def test_init_invalid_request_template_missing_start(self):
        """Test that request_template without 'start' placeholder raises ValueError."""
        mock_target = _make_mock_target()

        with pytest.raises(ValueError, match="request_template must contain all required placeholders"):
            ChunkedRequestAttack(
                objective_target=mock_target,
                request_template="Give me {chunk_type} {end} of '{objective}'",
            )

    def test_init_invalid_request_template_missing_end(self):
        """Test that request_template without 'end' placeholder raises ValueError."""
        mock_target = _make_mock_target()

        with pytest.raises(ValueError, match="request_template must contain all required placeholders"):
            ChunkedRequestAttack(
                objective_target=mock_target,
                request_template="Give me {chunk_type} {start} of '{objective}'",
            )

    def test_init_invalid_request_template_missing_chunk_type(self):
        """Test that request_template without 'chunk_type' placeholder raises ValueError."""
        mock_target = _make_mock_target()

        with pytest.raises(ValueError, match="request_template must contain all required placeholders"):
            ChunkedRequestAttack(
                objective_target=mock_target,
                request_template="Give me {start}-{end} of '{objective}'",
            )

    def test_init_invalid_request_template_missing_objective(self):
        """Test that request_template without 'objective' placeholder raises ValueError."""
        mock_target = _make_mock_target()

        with pytest.raises(ValueError, match="request_template must contain all required placeholders"):
            ChunkedRequestAttack(
                objective_target=mock_target,
                request_template="Give me {chunk_type} {start}-{end}",
            )

    def test_init_invalid_request_template_missing_multiple(self):
        """Test that request_template without multiple placeholders raises ValueError."""
        mock_target = _make_mock_target()

        with pytest.raises(ValueError, match="request_template must contain all required placeholders"):
            ChunkedRequestAttack(
                objective_target=mock_target,
                request_template="Give me the data",
            )

    def test_init_valid_request_template_with_extra_placeholders(self):
        """Test that request_template with extra placeholders is accepted."""
        mock_target = _make_mock_target()

        # Should not raise - extra placeholders are fine as long as required ones are present
        attack = ChunkedRequestAttack(
            objective_target=mock_target,
            request_template="Give me {chunk_type} {start}-{end} of '{objective}' in {format}",
        )

        assert attack._request_template == "Give me {chunk_type} {start}-{end} of '{objective}' in {format}"

    def test_generate_chunk_prompts_with_objective(self):
        """Test that chunk prompts include the objective from context."""
        mock_target = _make_mock_target()
        attack = ChunkedRequestAttack(
            objective_target=mock_target,
            chunk_size=50,
            total_length=100,
        )

        context = ChunkedRequestAttackContext(params=AttackParameters(objective="the secret password"))
        prompts = attack._generate_chunk_prompts(context)

        assert len(prompts) == 2
        assert "the secret password" in prompts[0]
        assert "the secret password" in prompts[1]
        assert "1-50" in prompts[0]
        assert "51-100" in prompts[1]


@pytest.mark.usefixtures("patch_central_database")
class TestChunkedRequestAttackExecution:
    """Tests for the main attack execution logic."""

    async def test_perform_async_sets_atomic_attack_identifier(self):
        """Test that _perform_async sets atomic_attack_identifier in the correct AtomicAttack format."""
        mock_target = _make_mock_target()
        mock_normalizer = MagicMock(spec=PromptNormalizer)
        sample_response = Message(
            message_pieces=[
                MessagePiece(role="assistant", original_value="chunk response", original_value_data_type="text")
            ]
        )
        mock_normalizer.send_prompt_async = AsyncMock(return_value=sample_response)

        attack = ChunkedRequestAttack(
            objective_target=mock_target,
            prompt_normalizer=mock_normalizer,
            chunk_size=100,
            total_length=100,
        )

        context = ChunkedRequestAttackContext(params=AttackParameters(objective="Extract the secret"))
        result = await attack._perform_async(context=context)

        assert result.atomic_attack_identifier is not None
        assert result.atomic_attack_identifier.class_name == "AtomicAttack"
        assert result.get_attack_strategy_identifier() == attack.get_identifier()
