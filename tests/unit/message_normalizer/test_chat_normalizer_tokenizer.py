# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import textwrap
from unittest.mock import MagicMock, patch

import pytest

from pyrit.message_normalizer import TokenizerTemplateNormalizer
from pyrit.models import Message, MessagePiece
from pyrit.models.literals import ChatMessageRole


def _make_message(role: ChatMessageRole, content: str) -> Message:
    """Helper to create a Message from role and content."""
    return Message(message_pieces=[MessagePiece(role=role, original_value=content)])


class TestTokenizerTemplateNormalizerInit:
    """Tests for TokenizerTemplateNormalizer initialization."""

    def test_init_with_tokenizer(self):
        """Test direct initialization with a tokenizer."""
        mock_tokenizer = MagicMock()
        normalizer = TokenizerTemplateNormalizer(tokenizer=mock_tokenizer)
        assert normalizer.tokenizer == mock_tokenizer
        assert normalizer.system_message_behavior == "keep"

    def test_init_with_custom_config(self):
        """Test initialization with custom config."""
        mock_tokenizer = MagicMock()
        normalizer = TokenizerTemplateNormalizer(
            tokenizer=mock_tokenizer,
            system_message_behavior="developer",
        )
        assert normalizer.system_message_behavior == "developer"

    def test_model_aliases_contains_expected_aliases(self):
        """Test that MODEL_ALIASES contains expected aliases."""
        aliases = TokenizerTemplateNormalizer.MODEL_ALIASES
        assert "chatml" in aliases
        assert "phi3" in aliases
        assert "qwen" in aliases
        assert "llama3" in aliases


class TestFromModel:
    """Tests for the from_model factory method."""

    @patch.dict("os.environ", {"HUGGINGFACE_TOKEN": ""}, clear=False)
    @patch.object(TokenizerTemplateNormalizer, "_load_tokenizer")
    def test_from_model_with_alias(self, mock_load_tokenizer):
        """Test from_model resolves alias to full model name."""
        mock_tokenizer = MagicMock()
        mock_tokenizer.chat_template = "some template"
        mock_load_tokenizer.return_value = mock_tokenizer

        normalizer = TokenizerTemplateNormalizer.from_model("chatml")

        # get_non_required_value returns "" when no token is provided
        mock_load_tokenizer.assert_called_once_with("HuggingFaceH4/zephyr-7b-beta", "")
        assert normalizer.tokenizer == mock_tokenizer

    @patch.dict("os.environ", {"HUGGINGFACE_TOKEN": ""}, clear=False)
    @patch.object(TokenizerTemplateNormalizer, "_load_tokenizer")
    def test_from_model_with_full_name(self, mock_load_tokenizer):
        """Test from_model works with full model name."""
        mock_tokenizer = MagicMock()
        mock_tokenizer.chat_template = "some template"
        mock_load_tokenizer.return_value = mock_tokenizer

        normalizer = TokenizerTemplateNormalizer.from_model("custom/model-name")

        # get_non_required_value returns "" when no token is provided
        mock_load_tokenizer.assert_called_once_with("custom/model-name", "")
        assert normalizer.tokenizer == mock_tokenizer

    @patch.object(TokenizerTemplateNormalizer, "_load_tokenizer")
    def test_from_model_with_token(self, mock_load_tokenizer):
        """Test from_model passes token for gated models."""
        mock_tokenizer = MagicMock()
        mock_tokenizer.chat_template = "some template"
        mock_load_tokenizer.return_value = mock_tokenizer

        TokenizerTemplateNormalizer.from_model("some-model", token="hf_token123")

        mock_load_tokenizer.assert_called_once_with("some-model", "hf_token123")

    @patch.object(TokenizerTemplateNormalizer, "_load_tokenizer")
    def test_from_model_raises_when_no_chat_template(self, mock_load_tokenizer):
        """Test from_model raises ValueError if tokenizer has no chat_template."""
        mock_tokenizer = MagicMock()
        mock_tokenizer.chat_template = None
        mock_load_tokenizer.return_value = mock_tokenizer

        with pytest.raises(ValueError, match="does not have a chat_template"):
            TokenizerTemplateNormalizer.from_model("model-without-template")

    @patch.dict("os.environ", {"HUGGINGFACE_TOKEN": ""}, clear=False)
    @patch.object(TokenizerTemplateNormalizer, "_load_tokenizer")
    def test_from_model_case_insensitive_alias(self, mock_load_tokenizer):
        """Test from_model aliases are case-insensitive."""
        mock_tokenizer = MagicMock()
        mock_tokenizer.chat_template = "some template"
        mock_load_tokenizer.return_value = mock_tokenizer

        TokenizerTemplateNormalizer.from_model("CHATML")

        # get_non_required_value returns "" when no token is provided
        mock_load_tokenizer.assert_called_once_with("HuggingFaceH4/zephyr-7b-beta", "")


class TestNormalizeStringAsync:
    """Tests for the normalize_string_async method."""

    @pytest.fixture
    def chatml_tokenizer_normalizer(self):
        def _apply_chatml_template(messages, tokenize=False, add_generation_prompt=False):
            """Simulate ChatML template formatting."""
            result = ""
            for msg in messages:
                result += f"<|{msg['role']}|>\n{msg['content']}</s>\n"
            if add_generation_prompt:
                result += "<|assistant|>\n"
            return result

        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.side_effect = _apply_chatml_template
        return TokenizerTemplateNormalizer(tokenizer=mock_tokenizer)

    @pytest.mark.asyncio
    async def test_normalize_chatml(self, chatml_tokenizer_normalizer: TokenizerTemplateNormalizer):
        messages = [
            _make_message("system", "You are a friendly chatbot who always responds in the style of a pirate"),
            _make_message("user", "How many helicopters can a human eat in one sitting?"),
        ]
        expected = textwrap.dedent(
            """\
            <|system|>
            You are a friendly chatbot who always responds in the style of a pirate</s>
            <|user|>
            How many helicopters can a human eat in one sitting?</s>
            <|assistant|>
            """
        )

        assert await chatml_tokenizer_normalizer.normalize_string_async(messages) == expected

    @pytest.mark.asyncio
    async def test_normalize_uses_converted_value(self):
        """Test that normalize uses converted_value when available."""
        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.return_value = "formatted"
        normalizer = TokenizerTemplateNormalizer(tokenizer=mock_tokenizer)

        piece = MessagePiece(
            role="user",
            original_value="original",
            converted_value="converted",
        )
        messages = [Message(message_pieces=[piece])]

        await normalizer.normalize_string_async(messages)

        call_args = mock_tokenizer.apply_chat_template.call_args
        assert call_args[0][0] == [{"role": "user", "content": "converted"}]

    @pytest.mark.asyncio
    async def test_normalize_falls_back_to_original_value(self):
        """Test that normalize falls back to original_value when converted_value is None."""
        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.return_value = "formatted"
        normalizer = TokenizerTemplateNormalizer(tokenizer=mock_tokenizer)

        piece = MessagePiece(
            role="user",
            original_value="original",
            converted_value=None,
        )
        messages = [Message(message_pieces=[piece])]

        await normalizer.normalize_string_async(messages)

        call_args = mock_tokenizer.apply_chat_template.call_args
        assert call_args[0][0] == [{"role": "user", "content": "original"}]
