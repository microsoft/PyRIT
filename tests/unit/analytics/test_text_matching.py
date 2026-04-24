# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

from pyrit.analytics.text_matching import ApproximateTextMatching, ExactTextMatching


class TestExactTextMatching:
    def test_case_insensitive_match(self):
        matcher = ExactTextMatching(case_sensitive=False)
        assert matcher.is_match(target="Hello", text="Hello World") is True
        assert matcher.is_match(target="HELLO", text="hello world") is True
        assert matcher.is_match(target="hello", text="HELLO WORLD") is True

    def test_case_sensitive_match(self):
        matcher = ExactTextMatching(case_sensitive=True)
        assert matcher.is_match(target="Hello", text="Hello World") is True
        assert matcher.is_match(target="HELLO", text="hello world") is False
        assert matcher.is_match(target="hello", text="HELLO WORLD") is False

    def test_no_match(self):
        matcher_insensitive = ExactTextMatching(case_sensitive=False)
        matcher_sensitive = ExactTextMatching(case_sensitive=True)
        assert matcher_insensitive.is_match(target="xyz", text="Hello World") is False
        assert matcher_sensitive.is_match(target="xyz", text="Hello World") is False

    def test_empty_text(self):
        matcher = ExactTextMatching(case_sensitive=False)
        assert matcher.is_match(target="hello", text="") is False

    def test_partial_match(self):
        matcher = ExactTextMatching(case_sensitive=False)
        assert matcher.is_match(target="World", text="Hello World") is True
        assert matcher.is_match(target="worl", text="Hello World") is True

    def test_default_case_insensitive(self):
        matcher = ExactTextMatching()  # Default is case_sensitive=False
        assert matcher.is_match(target="hello", text="HELLO WORLD") is True


class TestApproximateTextMatching:
    def test_perfect_match_above_threshold(self):
        matcher = ApproximateTextMatching(threshold=0.5, n=3, case_sensitive=False)
        assert matcher.is_match(target="hello", text="hello world") is True

    def test_no_match_below_threshold(self):
        matcher = ApproximateTextMatching(threshold=0.5, n=3, case_sensitive=False)
        assert matcher.is_match(target="xyz", text="abc") is False

    def test_partial_match(self):
        # "hello" -> n-grams: "hel", "ell", "llo"
        # "hallo" contains "llo" but not "hel" or "ell"
        matcher = ApproximateTextMatching(threshold=0.3, n=3, case_sensitive=False)
        assert matcher.is_match(target="hello", text="hallo") is True

        # With higher threshold, should not match
        matcher_high = ApproximateTextMatching(threshold=0.8, n=3, case_sensitive=False)
        assert matcher_high.is_match(target="hello", text="hallo") is False

    def test_case_insensitive(self):
        matcher = ApproximateTextMatching(threshold=0.8, n=3, case_sensitive=False)
        assert matcher.is_match(target="HELLO", text="hello world") is True
        assert matcher.is_match(target="hello", text="HELLO WORLD") is True

    def test_case_sensitive(self):
        matcher = ApproximateTextMatching(threshold=0.8, n=3, case_sensitive=True)
        assert matcher.is_match(target="HELLO", text="hello world") is False

    def test_different_n_values(self):
        text = "hello world"
        target = "hello"

        matcher_n2 = ApproximateTextMatching(threshold=0.8, n=2)
        matcher_n3 = ApproximateTextMatching(threshold=0.8, n=3)
        matcher_n4 = ApproximateTextMatching(threshold=0.8, n=4)

        # All should match for perfect substring match
        assert matcher_n2.is_match(target=target, text=text) is True
        assert matcher_n3.is_match(target=target, text=text) is True
        assert matcher_n4.is_match(target=target, text=text) is True

    def test_target_too_short(self):
        # Target shorter than n should return False
        matcher = ApproximateTextMatching(threshold=0.5, n=3)
        assert matcher.is_match(target="hi", text="hello world") is False

    def test_empty_text(self):
        matcher = ApproximateTextMatching(threshold=0.5, n=3)
        assert matcher.is_match(target="hello", text="") is False

    def test_approximate_detection(self):
        # Test detecting encoded/modified text
        original = "secretmessage"
        modified = "secret message with extra stuff"

        matcher = ApproximateTextMatching(threshold=0.5, n=4, case_sensitive=False)
        assert matcher.is_match(target=original, text=modified) is True

    def test_low_ngram_for_short_strings(self):
        matcher = ApproximateTextMatching(threshold=0.8, n=2, case_sensitive=False)
        assert matcher.is_match(target="cat", text="the cat sat") is True

    def test_get_overlap_score(self):
        matcher = ApproximateTextMatching(threshold=0.5, n=3, case_sensitive=False)

        # Perfect match should give 1.0
        score = matcher.get_overlap_score(target="hello", text="hello world")
        assert score == 1.0

        # No match should give 0.0
        score = matcher.get_overlap_score(target="xyz", text="abc")
        assert score == 0.0

        # Partial match should be between 0 and 1
        score = matcher.get_overlap_score(target="hello", text="hallo")
        assert 0.0 < score < 1.0

    def test_default_parameters(self):
        matcher = ApproximateTextMatching()  # Default threshold=0.5, n=3, case_sensitive=False
        # Should work with defaults
        assert matcher.is_match(target="hello", text="hello world") is True
