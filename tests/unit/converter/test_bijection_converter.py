# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import string

import pytest

from pyrit.converter import DigitBijectionConverter, LetterBijectionConverter, TokenBijectionConverter
from pyrit.converter.bijection_converter import BijectionConverter


def test_mapping_generated():
    converter = LetterBijectionConverter()
    assert converter.mapping is not None
    assert len(converter.mapping) == 26


def test_abstract_converter_cannot_be_instantiated():
    with pytest.raises(TypeError, match="_generate_mapping"):
        BijectionConverter()


def test_all_letters_mapped():
    converter = LetterBijectionConverter()
    for letter in string.ascii_lowercase:
        assert letter in converter.mapping


def test_mapping_is_bijection():
    converter = LetterBijectionConverter()
    values = list(converter.mapping.values())
    assert len(values) == len(set(values))


def test_inverse_mapping_generated():
    converter = LetterBijectionConverter()
    for k, v in converter.mapping.items():
        assert converter.inverse_mapping[v] == k


def test_fixed_size_zero():
    converter = LetterBijectionConverter(fixed_size=0)
    changed = sum(1 for k, v in converter.mapping.items() if k != v)
    assert changed > 0


def test_fixed_size_keeps_letters():
    converter = LetterBijectionConverter(fixed_size=5)
    letters = list(string.ascii_lowercase)
    for letter in letters[:5]:
        assert converter.mapping[letter] == letter


def test_seed_reproducibility():
    converter1 = LetterBijectionConverter(seed=42)
    converter2 = LetterBijectionConverter(seed=42)
    assert converter1.mapping == converter2.mapping


def test_explicit_mapping():
    custom_mapping = {chr(ord("a") + i): chr(ord("z") - i) for i in range(26)}
    converter = LetterBijectionConverter(mapping=custom_mapping)
    assert converter.mapping == custom_mapping


async def test_letter_converter_explicit_mapping_round_trip_preserves_case_and_punctuation():
    custom_mapping = {chr(ord("a") + i): chr(ord("z") - i) for i in range(26)}
    converter = LetterBijectionConverter(mapping=custom_mapping)

    encoded = await converter.convert_async(prompt="Hello, World!")

    assert encoded.output_text == "Svool, Dliow!"
    assert converter.decode(encoded.output_text) == "Hello, World!"


def test_letter_converter_fixed_size_all_letters_is_identity_mapping():
    converter = LetterBijectionConverter(fixed_size=26)
    assert converter.mapping == {letter: letter for letter in string.ascii_lowercase}


def test_letter_converter_identifier_includes_fixed_size_and_mapping():
    converter = LetterBijectionConverter(fixed_size=5, seed=42)
    identifier = converter.get_identifier()

    assert identifier.params["fixed_size"] == 5
    assert identifier.params["mapping"] == str(converter.mapping)


def test_digit_converter_mapping():
    converter = DigitBijectionConverter()
    # maps all 26 letters to digit strings
    assert len(converter.mapping) == 26
    for letter in string.ascii_lowercase:
        assert letter in converter.mapping
        # each value should be a digit string
        assert converter.mapping[letter].isdigit()


@pytest.mark.parametrize("num_digits", [2, 3, 4])
def test_digit_converter_values_have_configured_length(num_digits: int):
    converter = DigitBijectionConverter(num_digits=num_digits, seed=42)
    assert {len(value) for value in converter.mapping.values()} == {num_digits}


def test_digit_converter_seed_reproducibility():
    converter1 = DigitBijectionConverter(seed=42)
    converter2 = DigitBijectionConverter(seed=42)
    assert converter1.mapping == converter2.mapping


async def test_digit_converter_explicit_mapping_round_trip():
    custom_mapping = {letter: str(index + 10) for index, letter in enumerate(string.ascii_lowercase)}
    converter = DigitBijectionConverter(mapping=custom_mapping)

    encoded = await converter.convert_async(prompt="abc xyz!")

    assert encoded.output_text == "101112 333435!"
    assert converter.decode(encoded.output_text) == "abc xyz!"


def test_digit_converter_encodes_letters():
    converter = DigitBijectionConverter(num_digits=2)
    # encoding "hello" should produce digit strings
    result_chars = "".join(converter.mapping.get(c, c) for c in "hello")
    assert result_chars != "hello"
    assert result_chars.replace(" ", "").isdigit()


@pytest.mark.parametrize("num_digits", [1, 5])
def test_digit_converter_invalid_num_digits(num_digits: int):
    with pytest.raises(ValueError, match="num_digits must be between 2 and 4"):
        DigitBijectionConverter(num_digits=num_digits)


def test_digit_converter_is_bijection():
    converter = DigitBijectionConverter()
    values = list(converter.mapping.values())
    assert len(values) == len(set(values))


def test_digit_converter_identifier_includes_num_digits_and_mapping():
    converter = DigitBijectionConverter(num_digits=3, seed=42)
    identifier = converter.get_identifier()

    assert identifier.params["num_digits"] == 3
    assert identifier.params["mapping"] == str(converter.mapping)


async def test_encode_prompt():
    converter = LetterBijectionConverter(fixed_size=0)
    result = await converter.convert_async(prompt="hello world")
    assert result.output_text != "hello world"


async def test_decode_reverses_encoding():
    converter = LetterBijectionConverter()
    original = "hello world"
    encoded = await converter.convert_async(prompt=original)
    decoded = converter.decode(encoded.output_text)
    assert decoded == original


async def test_spaces_preserved():
    converter = LetterBijectionConverter()
    result = await converter.convert_async(prompt="hello world")
    assert " " in result.output_text


async def test_uppercase_preserved():
    converter = LetterBijectionConverter()
    result = await converter.convert_async(prompt="Hello World")
    assert result.output_text[0].isupper()


async def test_unsupported_input_type():
    converter = LetterBijectionConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image")


async def test_digit_converter_unsupported_input_type():
    converter = DigitBijectionConverter()
    with pytest.raises(ValueError):
        await converter.convert_async(prompt="hello", input_type="image")


def test_token_converter_mapping_generated():
    mock_tokenizer = type(
        "MockTokenizer",
        (),
        {
            "get_vocab": lambda self: {
                word: i
                for i, word in enumerate(
                    [
                        "cat",
                        "dog",
                        "fox",
                        "run",
                        "jump",
                        "tree",
                        "fish",
                        "bird",
                        "rock",
                        "sand",
                        "moon",
                        "star",
                        "rain",
                        "wind",
                        "fire",
                        "lake",
                        "hill",
                        "road",
                        "farm",
                        "town",
                        "book",
                        "door",
                        "hand",
                        "face",
                        "mind",
                        "body",
                        "soul",
                        "hope",
                        "love",
                        "time",
                    ]
                )
            }
        },
    )()
    converter = TokenBijectionConverter(tokenizer=mock_tokenizer)
    assert len(converter.mapping) == 26
    for letter in string.ascii_lowercase:
        assert letter in converter.mapping


def test_token_converter_is_bijection():
    mock_tokenizer = type(
        "MockTokenizer",
        (),
        {
            "get_vocab": lambda self: {
                word: i
                for i, word in enumerate(
                    [
                        "cat",
                        "dog",
                        "fox",
                        "run",
                        "jump",
                        "tree",
                        "fish",
                        "bird",
                        "rock",
                        "sand",
                        "moon",
                        "star",
                        "rain",
                        "wind",
                        "fire",
                        "lake",
                        "hill",
                        "road",
                        "farm",
                        "town",
                        "book",
                        "door",
                        "hand",
                        "face",
                        "mind",
                        "body",
                        "soul",
                        "hope",
                        "love",
                        "time",
                    ]
                )
            }
        },
    )()
    converter = TokenBijectionConverter(tokenizer=mock_tokenizer)
    values = list(converter.mapping.values())
    assert len(values) == len(set(values))


def test_token_converter_seed_reproducibility():
    mock_tokenizer = type(
        "MockTokenizer",
        (),
        {
            "get_vocab": lambda self: {
                word: i
                for i, word in enumerate(
                    [
                        "cat",
                        "dog",
                        "fox",
                        "run",
                        "jump",
                        "tree",
                        "fish",
                        "bird",
                        "rock",
                        "sand",
                        "moon",
                        "star",
                        "rain",
                        "wind",
                        "fire",
                        "lake",
                        "hill",
                        "road",
                        "farm",
                        "town",
                        "book",
                        "door",
                        "hand",
                        "face",
                        "mind",
                        "body",
                        "soul",
                        "hope",
                        "love",
                        "time",
                    ]
                )
            }
        },
    )()
    converter1 = TokenBijectionConverter(tokenizer=mock_tokenizer, seed=42)
    converter2 = TokenBijectionConverter(tokenizer=mock_tokenizer, seed=42)
    assert converter1.mapping == converter2.mapping


def test_token_converter_not_enough_tokens():
    mock_tokenizer = type("MockTokenizer", (), {"get_vocab": lambda self: {"ab": 1, "cd": 2}})()
    with pytest.raises(ValueError):
        TokenBijectionConverter(tokenizer=mock_tokenizer)


async def test_token_converter_encodes_letters():
    mock_tokenizer = type(
        "MockTokenizer",
        (),
        {
            "get_vocab": lambda self: {
                word: i
                for i, word in enumerate(
                    [
                        "cat",
                        "dog",
                        "fox",
                        "run",
                        "jump",
                        "tree",
                        "fish",
                        "bird",
                        "rock",
                        "sand",
                        "moon",
                        "star",
                        "rain",
                        "wind",
                        "fire",
                        "lake",
                        "hill",
                        "road",
                        "farm",
                        "town",
                        "book",
                        "door",
                        "hand",
                        "face",
                        "mind",
                        "body",
                        "soul",
                        "hope",
                        "love",
                        "time",
                    ]
                )
            }
        },
    )()
    converter = TokenBijectionConverter(tokenizer=mock_tokenizer, seed=42)
    result = await converter.convert_async(prompt="hello")
    assert result.output_text != "hello"


_PLAIN_VOCAB_WORDS = [
    "cat",
    "dog",
    "fox",
    "run",
    "jump",
    "tree",
    "fish",
    "bird",
    "rock",
    "sand",
    "moon",
    "star",
    "rain",
    "wind",
    "fire",
    "lake",
    "hill",
    "road",
    "farm",
    "town",
    "book",
    "door",
    "hand",
    "face",
    "mind",
    "body",
    "soul",
    "hope",
    "love",
    "time",
]


def _mock_tokenizer(vocab: dict[str, int]):
    return type("MockTokenizer", (), {"get_vocab": lambda self: vocab})()


async def test_token_converter_delimits_encoded_units():
    # Regression test: without a delimiter between mapped tokens, a multi-letter word
    # collapses into an unsegmentable run-on string that the target model can't learn to
    # produce or reliably decode.
    mock_tokenizer = _mock_tokenizer({word: i for i, word in enumerate(_PLAIN_VOCAB_WORDS)})
    converter = TokenBijectionConverter(tokenizer=mock_tokenizer, seed=42)

    result = await converter.convert_async(prompt="hi")
    expected = f"{converter.mapping['h']}-{converter.mapping['i']}"
    assert result.output_text == expected


async def test_token_converter_round_trip_multi_letter_word():
    mock_tokenizer = _mock_tokenizer({word: i for i, word in enumerate(_PLAIN_VOCAB_WORDS)})
    converter = TokenBijectionConverter(tokenizer=mock_tokenizer, seed=1)

    original = "Hello, World!"
    encoded = await converter.convert_async(prompt=original)
    assert converter.decode(encoded.output_text) == original


async def test_token_converter_preserves_word_boundaries_on_round_trip():
    # Real spaces between words must survive distinctly from the hyphen delimiter
    # used between per-letter code words within a single word.
    mock_tokenizer = _mock_tokenizer({word: i for i, word in enumerate(_PLAIN_VOCAB_WORDS)})
    converter = TokenBijectionConverter(tokenizer=mock_tokenizer, seed=7)

    original = "hi there"
    encoded = await converter.convert_async(prompt=original)
    assert " " in encoded.output_text
    assert converter.decode(encoded.output_text) == original


def test_token_converter_excludes_sentencepiece_continuation_fragments():
    # SentencePiece-style vocabs mark word-initial tokens with "▁"; bare tokens are
    # mid-word continuation fragments and should never be selected as standalone "words".
    vocab = {}
    for i, word in enumerate(_PLAIN_VOCAB_WORDS):
        vocab[f"▁{word}"] = i
        vocab[f"{word}x"] = i + 1000  # continuation fragment, no marker

    converter = TokenBijectionConverter(tokenizer=_mock_tokenizer(vocab), seed=42)

    for token in converter.mapping.values():
        assert not token.startswith("▁")
        assert token in _PLAIN_VOCAB_WORDS


def test_token_converter_excludes_gpt2_continuation_fragments():
    # GPT-2/RoBERTa byte-level BPE marks word-initial tokens with "Ġ".
    vocab = {}
    for i, word in enumerate(_PLAIN_VOCAB_WORDS):
        vocab[f"Ġ{word}"] = i
        vocab[f"{word}y"] = i + 1000  # continuation fragment, no marker

    converter = TokenBijectionConverter(tokenizer=_mock_tokenizer(vocab), seed=42)

    for token in converter.mapping.values():
        assert not token.startswith("Ġ")
        assert token in _PLAIN_VOCAB_WORDS


def test_token_converter_excludes_wordpiece_continuation_fragments():
    # WordPiece (BERT) marks continuation pieces with "##"; unmarked tokens are standalone.
    vocab = {}
    for i, word in enumerate(_PLAIN_VOCAB_WORDS):
        vocab[word] = i
        vocab[f"##{word}"] = i + 1000  # continuation piece

    converter = TokenBijectionConverter(tokenizer=_mock_tokenizer(vocab), seed=42)

    for token in converter.mapping.values():
        assert not token.startswith("##")
        assert token in _PLAIN_VOCAB_WORDS
