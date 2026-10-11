# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import unicodedata
from enum import Enum
from unittest.mock import patch

import pytest
from unit.mocks import mock_memory_resolving, store_message_async

from pyrit.memory import CentralMemory
from pyrit.models import MessagePiece
from pyrit.score import MessageScorable, PlagiarismMetric, PlagiarismScorer


class _OtherMetric(Enum):
    LCS = "lcs"
    LEVENSHTEIN = "levenshtein"
    JACCARD = "jaccard"
    INVALID = "invalid"


@pytest.mark.usefixtures("patch_central_database")
class TestPlagiarismScorer:
    """Test cases for the PlagiarismScorer class."""

    def test_init_with_default_parameters(self):
        """Test initialization with default parameters."""
        reference_text = "This is a sample reference text"
        scorer = PlagiarismScorer(reference_text=reference_text)

        assert scorer.reference_text == reference_text
        assert scorer.metric == PlagiarismMetric.LCS
        assert scorer.n == 5

    def test_init_with_custom_parameters(self):
        """Test initialization with custom parameters."""
        reference_text = "Custom reference text"
        metric = PlagiarismMetric.JACCARD
        n = 3

        scorer = PlagiarismScorer(reference_text=reference_text, metric=metric, n=n)

        assert scorer.reference_text == reference_text
        assert scorer.metric == metric
        assert scorer.n == n

    @pytest.mark.parametrize(
        "invalid_reference",
        ["", "   ", "\t\n  ", None, 123, [], {}],
    )
    def test_init_rejects_empty_or_non_string_reference_text(self, invalid_reference):
        """Test initialization rejects empty, whitespace-only, or non-string reference text."""
        with pytest.raises(ValueError, match="reference_text must be a non-empty string"):
            PlagiarismScorer(reference_text=invalid_reference)

    @pytest.mark.parametrize(
        "no_token_reference",
        ["!!!", "???", "---", "... ,,, ;;;", "   !@#$%^&*()   "],
    )
    def test_init_rejects_reference_text_without_tokens(self, no_token_reference):
        """Test initialization rejects reference text containing no word tokens."""
        with pytest.raises(ValueError, match="reference_text must contain at least one word token"):
            PlagiarismScorer(reference_text=no_token_reference)

    @pytest.mark.parametrize(
        "invalid_n",
        [0, -1, -5, 1.5, False, True, "3", None, [3]],
    )
    def test_init_rejects_invalid_n(self, invalid_n):
        """Test initialization rejects n that is not an integer >= 1 or is a boolean."""
        with pytest.raises(ValueError, match=r"n must be an integer >= 1"):
            PlagiarismScorer(reference_text="Valid reference text", n=invalid_n)

    @pytest.mark.parametrize("valid_n", [1, 2, 5, 10])
    def test_init_accepts_valid_boundary_n(self, valid_n):
        """Test initialization accepts positive integer n-gram sizes."""
        scorer = PlagiarismScorer(reference_text="Valid reference text", n=valid_n)
        assert scorer.n == valid_n

    @pytest.mark.parametrize(
        "invalid_metric",
        ["lcs", "levenshtein", "jaccard", "invalid", None, 123, *_OtherMetric],
    )
    def test_init_rejects_invalid_metric(self, invalid_metric):
        """Test initialization rejects metric that is not an instance of PlagiarismMetric."""
        with pytest.raises(ValueError, match="metric must be an instance of PlagiarismMetric"):
            PlagiarismScorer(reference_text="Valid reference text", metric=invalid_metric)

    @pytest.mark.parametrize("invalid_n", [0, -1, 1.5, False, True, "3", None])
    def test_plagiarism_score_rejects_invalid_n(self, invalid_n):
        """Test _plagiarism_score rejects invalid n."""
        scorer = PlagiarismScorer(reference_text="Valid reference text")
        with pytest.raises(ValueError, match=r"n must be an integer >= 1"):
            scorer._plagiarism_score(response="test", reference="test", n=invalid_n)

    @pytest.mark.parametrize("invalid_metric", ["lcs", "levenshtein", "jaccard", "invalid", None, 123, *_OtherMetric])
    @pytest.mark.parametrize(
        ("response", "reference"),
        [("test", "test"), ("", "test"), ("test", ""), ("different", "test"), ("prefix test suffix", "test")],
    )
    def test_plagiarism_score_rejects_invalid_metric(
        self, *, invalid_metric: object, response: str, reference: str
    ) -> None:
        """Test _plagiarism_score rejects invalid metric."""
        scorer = PlagiarismScorer(reference_text="Valid reference text")
        with pytest.raises(ValueError, match="metric must be an instance of PlagiarismMetric"):
            scorer._plagiarism_score(response=response, reference=reference, metric=invalid_metric)

    async def test_score_async_lcs_metric(self):
        """Test scoring with LCS metric."""
        reference_text = "The quick brown fox jumps over the lazy dog"
        response_text = "The quick brown fox runs over the lazy dog"

        scorer = PlagiarismScorer(reference_text=reference_text, metric=PlagiarismMetric.LCS)

        message_piece = MessagePiece(
            role="assistant",
            original_value=response_text,
            converted_value=response_text,
            converted_value_data_type="text",
        )

        request = message_piece.to_message()

        scores = await scorer.score_async(scorable=MessageScorable.from_message(await store_message_async(request)))

        assert len(scores) == 1
        score = scores[0]
        assert "Plagiarism score using 'lcs' metric" in score.score_value_description
        assert score.score_rationale == "Score is deterministic."
        assert score.message_piece_id == message_piece.id

        # Verify the score value is reasonable (should be high due to similarity)
        score_value = float(score.score_value)
        assert 0.0 <= score_value <= 1.0
        assert score_value > 0.8  # Should be high similarity

    async def test_score_async_levenshtein_metric(self):
        """Test scoring with Levenshtein metric."""
        reference_text = "Hello world"
        response_text = "Hello world test"

        scorer = PlagiarismScorer(reference_text=reference_text, metric=PlagiarismMetric.LEVENSHTEIN)

        request = MessagePiece(
            role="assistant",
            original_value=response_text,
            converted_value=response_text,
            converted_value_data_type="text",
        ).to_message()

        scores = await scorer._score_async(message=request)

        assert len(scores) == 1
        score = scores[0]
        assert "Plagiarism score using 'levenshtein' metric" in score.score_value_description

        score_value = float(score.score_value)
        assert 0.0 <= score_value <= 1.0

    async def test_score_async_jaccard_metric(self):
        """Test scoring with Jaccard metric."""
        reference_text = "The quick brown fox jumps over the lazy dog"
        response_text = "The quick brown fox runs over the lazy cat"

        scorer = PlagiarismScorer(reference_text=reference_text, metric=PlagiarismMetric.JACCARD, n=3)

        request = MessagePiece(
            role="assistant",
            original_value=response_text,
            converted_value=response_text,
            converted_value_data_type="text",
        ).to_message()

        scores = await scorer._score_async(message=request)

        assert len(scores) == 1
        score = scores[0]
        assert "Plagiarism score using 'jaccard' metric" in score.score_value_description

        score_value = float(score.score_value)
        assert 0.0 <= score_value <= 1.0

    async def test_score_async_empty_response(self):
        """Test scoring with empty response."""
        reference_text = "Sample reference text"
        scorer = PlagiarismScorer(reference_text=reference_text)

        request = MessagePiece(
            role="assistant", original_value="", converted_value="", converted_value_data_type="text"
        ).to_message()

        scores = await scorer._score_async(message=request)

        assert len(scores) == 1
        score = scores[0]
        score_value = float(score.score_value)
        assert score_value == 0.0

    async def test_score_async_identical_texts(self):
        """Test scoring with identical texts."""
        reference_text = "This is exactly the same text"

        scorer = PlagiarismScorer(reference_text=reference_text, metric=PlagiarismMetric.LCS)

        request = MessagePiece(
            role="assistant",
            original_value=reference_text,
            converted_value=reference_text,
            converted_value_data_type="text",
        ).to_message()

        scores = await scorer._score_async(message=request)

        assert len(scores) == 1
        score = scores[0]
        score_value = float(score.score_value)
        assert score_value == 1.0  # Should be perfect match

    async def test_score_async_completely_different_texts(self):
        """Test scoring with completely different texts."""
        reference_text = "Apple banana cherry"
        response_text = "Dog elephant fox"

        scorer = PlagiarismScorer(reference_text=reference_text, metric=PlagiarismMetric.LCS)

        request = MessagePiece(
            role="assistant",
            original_value=response_text,
            converted_value=response_text,
            converted_value_data_type="text",
        ).to_message()

        scores = await scorer._score_async(message=request)

        assert len(scores) == 1
        score = scores[0]
        score_value = float(score.score_value)
        assert score_value == 0.0  # Should be no similarity

    async def test_score_async_adds_to_memory(self):
        """Test that scoring adds results to memory."""
        reference_text = "Test reference text"
        scorer = PlagiarismScorer(reference_text=reference_text)

        request = MessagePiece(
            role="assistant",
            original_value="Test response text",
            converted_value="Test response text",
            converted_value_data_type="text",
        ).to_message()

        memory = mock_memory_resolving(request)
        with patch.object(CentralMemory, "get_memory_instance", return_value=memory):
            await scorer.score_async(scorable=MessageScorable.from_message(request))
            memory.add_scores_to_memory_async.assert_called_once()

    async def test_score_async_unsupported_data_type_returns_empty(self, patch_central_database):
        reference_text = "Test reference text"
        scorer = PlagiarismScorer(reference_text=reference_text)

        request = MessagePiece(
            role="assistant",
            original_value="image_data",
            converted_value="image_data",
            converted_value_data_type="image_path",
        ).to_message()

        scores = await scorer.score_async(scorable=MessageScorable.from_message(await store_message_async(request)))
        assert scores == []

    async def test_score_text_async_integration(self):
        """Test scoring using the convenience method score_text_async."""
        reference_text = "The quick brown fox"
        scorer = PlagiarismScorer(reference_text=reference_text)

        scores = await scorer.score_text_async("The quick brown dog")

        assert len(scores) == 1
        score = scores[0]
        score_value = float(score.score_value)
        assert 0.0 <= score_value <= 1.0
        assert score_value > 0.5  # Should have some similarity


class TestPlagiarismScorerUtilityFunctions:
    """Test cases for utility functions in the plagiarism scorer."""

    @pytest.fixture
    def scorer(self):
        """Create a scorer instance for testing utility methods."""
        return PlagiarismScorer(reference_text="test reference text")

    def test_tokenize_basic(self, scorer):
        """Test basic tokenization functionality."""
        text = "Hello World Test"
        tokens = scorer._tokenize(text)
        assert tokens == ["hello", "world", "test"]

    def test_tokenize_with_punctuation(self, scorer):
        """Test tokenization with punctuation removal."""
        text = "Hello, world! How are you?"
        tokens = scorer._tokenize(text)
        assert tokens == ["hello", "world", "how", "are", "you"]

    def test_tokenize_empty_string(self, scorer):
        """Test tokenization with empty string."""
        tokens = scorer._tokenize("")
        assert tokens == []

    def test_tokenize_normalizes_decomposed_accents(self, scorer):
        """A decomposed (NFD) word must tokenize the same as its composed (NFC) form."""
        nfc = "café"
        nfd = unicodedata.normalize("NFD", nfc)
        assert nfd != nfc  # the accent is a separate combining code point
        assert scorer._tokenize(nfd) == ["café"]
        assert scorer._tokenize(nfd) == scorer._tokenize(nfc)

    def test_tokenize_folds_compatibility_forms(self, scorer):
        """Fullwidth and mathematical-alphanumeric homoglyphs fold to plain ASCII."""
        assert scorer._tokenize("ｃａｆｅ") == ["cafe"]
        assert scorer._tokenize("𝐜𝐚𝐟𝐞") == ["cafe"]

    def test_tokenize_keeps_combining_marks(self, scorer):
        """Scripts whose vowel signs are combining marks must not collapse together."""
        assert scorer._tokenize("दिन") == ["दिन"]  # "day"
        assert scorer._tokenize("दीन") == ["दीन"]  # "poor"
        assert scorer._tokenize("दिन") != scorer._tokenize("दीन")

    def test_plagiarism_score_nfd_reference_is_verbatim(self, scorer):
        """A verbatim copy written in NFD must score 1.0 against its NFC reference."""
        reference = "Il était une fois"
        response = unicodedata.normalize("NFD", reference)
        for metric in PlagiarismMetric:
            assert scorer._plagiarism_score(response, reference, metric=metric) == 1.0

    def test_plagiarism_score_distinguishes_combining_mark_words(self, scorer):
        """Different words that differ only by a combining mark are not plagiarism."""
        scorer = PlagiarismScorer(reference_text="दिन")
        score = scorer._plagiarism_score("दीन", "दिन", metric=PlagiarismMetric.LCS)
        assert score == 0.0

    def test_lcs_length_identical(self, scorer):
        """Test LCS with identical sequences."""
        a = ["hello", "world", "test"]
        b = ["hello", "world", "test"]
        length = scorer._lcs_length(a, b)
        assert length == 3

    def test_lcs_length_different(self, scorer):
        """Test LCS with different sequences."""
        a = ["hello", "world", "test"]
        b = ["hello", "test", "case"]
        length = scorer._lcs_length(a, b)
        assert length == 2  # "hello" and "test"

    def test_lcs_length_empty(self, scorer):
        """Test LCS with empty sequences."""
        a = []
        b = ["hello", "world"]
        length = scorer._lcs_length(a, b)
        assert length == 0

    def test_levenshtein_distance_identical(self, scorer):
        """Test Levenshtein distance with identical sequences."""
        a = ["hello", "world"]
        b = ["hello", "world"]
        distance = scorer._levenshtein_distance(a, b)
        assert distance == 0

    def test_levenshtein_distance_different(self, scorer):
        """Test Levenshtein distance with different sequences."""
        a = ["hello", "world"]
        b = ["hello", "test"]
        distance = scorer._levenshtein_distance(a, b)
        assert distance == 1  # One substitution

    def test_levenshtein_distance_empty(self, scorer):
        """Test Levenshtein distance with empty sequences."""
        a = []
        b = ["hello", "world"]
        distance = scorer._levenshtein_distance(a, b)
        assert distance == 2  # Two insertions

    def test_ngram_set_basic(self, scorer):
        """Test n-gram set generation."""
        tokens = ["the", "quick", "brown", "fox"]
        ngrams = scorer._ngram_set(tokens, 2)
        expected = {("the", "quick"), ("quick", "brown"), ("brown", "fox")}
        assert ngrams == expected

    def test_ngram_set_longer_n(self, scorer):
        """Test n-gram set with n longer than token list."""
        tokens = ["hello", "world"]
        ngrams = scorer._ngram_set(tokens, 5)
        assert ngrams == set()

    def test_ngram_set_empty_tokens(self, scorer):
        """Test n-gram set with empty token list."""
        tokens = []
        ngrams = scorer._ngram_set(tokens, 2)
        assert ngrams == set()

    def test_plagiarism_score_lcs(self, scorer):
        """Test plagiarism score with LCS metric."""
        response = "The quick brown fox"
        reference = "The quick brown dog"
        score = scorer._plagiarism_score(response, reference, metric=PlagiarismMetric.LCS)
        assert 0.0 <= score <= 1.0
        assert score == 0.75  # 3/4 words match

    def test_plagiarism_score_levenshtein(self, scorer):
        """Test plagiarism score with Levenshtein metric."""
        response = "hello world"
        reference = "hello world"
        score = scorer._plagiarism_score(response, reference, metric=PlagiarismMetric.LEVENSHTEIN)
        assert score == 1.0  # Perfect match

    def test_plagiarism_score_jaccard(self, scorer):
        """Test plagiarism score with Jaccard metric."""
        response = "the quick brown fox jumps"
        reference = "the quick brown dog runs"
        score = scorer._plagiarism_score(response, reference, metric=PlagiarismMetric.JACCARD, n=2)
        assert 0.0 <= score <= 1.0

    def test_plagiarism_score_empty_texts(self, scorer):
        """Test plagiarism score with empty texts."""
        score = scorer._plagiarism_score("", "hello world", metric=PlagiarismMetric.LCS)
        assert score == 0.0

    def test_plagiarism_score_invalid_metric(self, scorer):
        """Test plagiarism score rejects an unsupported enum."""
        with pytest.raises(ValueError, match="metric must be an instance of PlagiarismMetric"):
            scorer._plagiarism_score("hello", "world", metric=_OtherMetric.INVALID)

    def test_plagiarism_score_case_insensitive(self, scorer):
        """Test that plagiarism score is case insensitive."""
        response = "Hello World"
        reference = "hello world"
        score = scorer._plagiarism_score(response, reference, metric=PlagiarismMetric.LCS)
        assert score == 1.0  # Should be perfect match despite case difference

    def test_plagiarism_score_lcs_reference_contained_in_response(self, scorer):
        """Test LCS metric returns 1.0 when reference text is contained in response."""
        reference = "It was a bright cold day in April"
        response = (
            "The famous opening line states: It was a bright cold day in April, and the clocks were striking thirteen."
        )
        score = scorer._plagiarism_score(response, reference, metric=PlagiarismMetric.LCS)
        assert score == 1.0  # Should be perfect match when reference is contained

    def test_plagiarism_score_levenshtein_reference_contained_in_response(self, scorer):
        """Test Levenshtein metric returns 1.0 when reference text is contained in response."""
        reference = "The quick brown fox jumps"
        response = "Here is the sentence: The quick brown fox jumps over the lazy dog."
        score = scorer._plagiarism_score(response, reference, metric=PlagiarismMetric.LEVENSHTEIN)
        assert score == 1.0  # Should be perfect match when reference is contained

    def test_plagiarism_score_jaccard_reference_contained_in_response(self, scorer):
        """Test Jaccard metric returns 1.0 when reference text is contained in response."""
        reference = "Hello world this is a test"
        response = "The AI model responded with: Hello world this is a test message for validation."
        score = scorer._plagiarism_score(response, reference, metric=PlagiarismMetric.JACCARD, n=3)
        assert score == 1.0  # Should be perfect match when reference is contained

    def test_plagiarism_score_reference_substring_of_word_not_plagiarism(self, scorer):
        """A reference that is only a substring of a longer response word is not plagiarism.

        The verbatim-match fast path must operate on word-level tokens, not raw
        characters. Otherwise a short reference such as "cat" would falsely score
        1.0 against a response containing "concatenate".
        """
        reference = "cat"
        response = "concatenate the results"
        for metric in PlagiarismMetric:
            score = scorer._plagiarism_score(response, reference, metric=metric)
            assert score == 0.0, f"{metric.value} should not treat a sub-word match as plagiarism"

    def test_plagiarism_score_verbatim_match_ignores_case_and_punctuation(self, scorer):
        """The verbatim fast path should still fire across case and punctuation differences."""
        reference = "The Secret Plan"
        response = "the secret plan!"
        for metric in PlagiarismMetric:
            score = scorer._plagiarism_score(response, reference, metric=metric)
            assert score == 1.0, f"{metric.value} should treat a word-level verbatim copy as plagiarism"

    def test_is_contiguous_sublist(self, scorer):
        """Directly exercise the tokenized sublist helper."""
        assert scorer._is_contiguous_sublist(sub=["b", "c"], full=["a", "b", "c", "d"]) is True
        assert scorer._is_contiguous_sublist(sub=["a", "c"], full=["a", "b", "c"]) is False
        assert scorer._is_contiguous_sublist(sub=[], full=["a"]) is False
        assert scorer._is_contiguous_sublist(sub=["a", "b"], full=["a"]) is False


class TestPlagiarismMetricEnum:
    """Test cases for the PlagiarismMetric enum."""

    def test_plagiarism_metric_values(self):
        """Test that enum values are correct."""
        assert PlagiarismMetric.LCS.value == "lcs"
        assert PlagiarismMetric.LEVENSHTEIN.value == "levenshtein"
        assert PlagiarismMetric.JACCARD.value == "jaccard"

    def test_plagiarism_metric_membership(self):
        """Test enum membership."""
        assert PlagiarismMetric.LCS in PlagiarismMetric
        assert PlagiarismMetric.LEVENSHTEIN in PlagiarismMetric
        assert PlagiarismMetric.JACCARD in PlagiarismMetric
