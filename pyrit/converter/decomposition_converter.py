# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import json
import logging
import pathlib
import re
import uuid
from collections import Counter
from typing import Any

from pyrit.common.apply_defaults import REQUIRED_VALUE, apply_defaults
from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.converter.converter import Converter, ConverterResult
from pyrit.exceptions import InvalidJsonException, pyrit_json_retry, remove_markdown_json
from pyrit.models import (
    ComponentIdentifier,
    JsonResponseConfig,
    Message,
    MessagePiece,
    PromptDataType,
    SeedPrompt,
)
from pyrit.prompt_target import CHAT_TARGET_REQUIREMENTS, PromptTarget

logger = logging.getLogger(__name__)

_VALID_TAGS = ("instruction", "structure", "verb", "noun")

# Minimum fraction of the original tokens that must survive in the decomposition for it to be
# accepted. The authors' own decompositions are mildly lossy, so an exact match is too strict.
_MIN_RECALL = 0.8

_DECOMPOSITION_DIR = pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / "decomposition"

# Innocuous codewords substituted for harmful noun phrases when the word-game is enabled. The list
# bounds how many noun phrases the word-game supports; a converter with more nouns raises rather than
# silently reusing a codeword (which would make the mapping ambiguous).
_CODEWORDS = (
    "apple",
    "banana",
    "cherry",
    "grape",
    "kiwi",
    "lemon",
    "mango",
    "orange",
    "papaya",
    "raspberry",
    "strawberry",
    "watermelon",
    "apricot",
    "blueberry",
    "coconut",
    "fig",
    "guava",
    "melon",
    "peach",
    "pear",
)


def _tokens(text: str) -> list[str]:
    """
    Tokenize text into lowercase word tokens for the reconstruction-recall check.

    Uses a Unicode-aware ``\\w+`` pattern so the recall invariant works for non-Latin scripts
    (e.g. Arabic, CJK), not only ASCII.

    Args:
        text (str): The text to tokenize.

    Returns:
        list[str]: The lowercase word tokens.
    """
    return re.findall(r"\w+", text.lower(), flags=re.UNICODE)


def _token_recall(source: list[str], got: list[str]) -> float:
    """
    Compute the fraction of source tokens (as a multiset) preserved in got.

    Args:
        source (list[str]): Tokens of the original objective.
        got (list[str]): Tokens of the joined decomposition.

    Returns:
        float: The fraction of source tokens preserved, in [0.0, 1.0].
    """
    if not source:
        return 1.0
    cs, cg = Counter(source), Counter(got)
    kept = sum(min(cs[k], cg[k]) for k in cs)
    return kept / len(source)


class DecompositionConverter(Converter):
    """
    Decompose-and-reconstruct converter based on DrAttack [@li2024drattack].

    The converter splits the objective into ordered, role-tagged phrases using an LLM, then rebuilds
    it as a fragmented "Question A / Question B" task plus a static benign in-context demonstration.
    The target is asked to answer the disaggregated questions jointly, reconstructing the original
    intent itself, so the assembled harmful instruction never appears verbatim in the request.

    The decomposition is requested as a flat ``{"words": [...], "types": [...]}`` JSON object (easier
    to validate than a nested parse tree), constrained by the ``response_json_schema`` declared in the
    system prompt. The response is validated against a reconstruction-recall invariant (the joined
    phrases must preserve the original tokens) plus valid-tag and opening-instruction checks; on
    failure an ``InvalidJsonException`` is raised, which ``@pyrit_json_retry`` retries.

    DrAttack paper: Li et al., "DrAttack: Prompt Decomposition and Reconstruction Makes Powerful
    LLM Jailbreakers", Findings of EMNLP 2024, https://arxiv.org/abs/2402.16914.
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)
    TARGET_REQUIREMENTS = CHAT_TARGET_REQUIREMENTS

    @apply_defaults
    def __init__(
        self,
        *,
        converter_target: PromptTarget = REQUIRED_VALUE,  # type: ignore[ty:invalid-parameter-default]
        decomposition_prompt: SeedPrompt | None = None,
        reconstruction_prompt: SeedPrompt | None = None,
        use_word_game: bool = False,
        word_game_prompt: SeedPrompt | None = None,
        codewords: tuple[str, ...] = _CODEWORDS,
    ) -> None:
        """
        Initialize the converter.

        Args:
            converter_target (PromptTarget): The endpoint used to decompose the objective. Must
                satisfy ``CHAT_TARGET_REQUIREMENTS``. Can be omitted if a default has been configured
                via PyRIT initialization.
            decomposition_prompt (SeedPrompt | None): System prompt instructing the model how to
                decompose a sentence (carries the response JSON schema). Defaults to the bundled
                ``decomposition/decomposition_system_prompt.yaml``.
            reconstruction_prompt (SeedPrompt | None): Template that renders the decomposed objective
                into the reconstruction task. Defaults to the bundled
                ``decomposition/reconstruction_prompt.yaml``.
            use_word_game (bool): If True, each harmful noun phrase is replaced by an innocuous codeword
                in the reconstruction questions, and a mapping preamble is prepended in the same prompt.
                Defaults to False.
            word_game_prompt (SeedPrompt | None): Template for the word-game mapping preamble. Defaults
                to the bundled ``decomposition/word_game_preamble.yaml``. Only used when
                ``use_word_game`` is True.
            codewords (tuple[str, ...]): Innocuous codewords substituted for harmful noun phrases when
                the word-game is enabled. Defaults to a bundled list of fruit names.

        Raises:
            ValueError: If ``codewords`` is empty while the word-game is enabled, or contains duplicates.
        """
        super().__init__(converter_target=converter_target)
        self._converter_target = converter_target
        self._decomposition_prompt = decomposition_prompt or SeedPrompt.from_yaml_file(
            _DECOMPOSITION_DIR / "decomposition_system_prompt.yaml"
        )
        self._reconstruction_prompt = reconstruction_prompt or SeedPrompt.from_yaml_file(
            _DECOMPOSITION_DIR / "reconstruction_prompt.yaml"
        )
        self._use_word_game = use_word_game
        self._word_game_prompt = word_game_prompt or SeedPrompt.from_yaml_file(
            _DECOMPOSITION_DIR / "word_game_preamble.yaml"
        )
        if use_word_game and not codewords:
            raise ValueError("codewords must be non-empty when the word-game is enabled")
        if len(set(codewords)) != len(codewords):
            raise ValueError("codewords must be unique; duplicates produce an ambiguous word-game mapping")
        self._codewords = codewords

    def _build_identifier(self) -> ComponentIdentifier:
        """
        Build the converter identifier.

        Returns:
            ComponentIdentifier: The identifier for this converter.
        """
        params: dict[str, Any] = {
            "decomposition_prompt": self._decomposition_prompt.value,
            "reconstruction_prompt": self._reconstruction_prompt.value,
            "use_word_game": self._use_word_game,
        }
        if self._use_word_game:
            params["word_game_prompt"] = self._word_game_prompt.value
            params["codewords"] = list(self._codewords)
        return self._create_identifier(
            params=params,
            converter_target=self._converter_target.get_identifier(),
        )

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Convert the objective into a decompose-and-reconstruct prompt.

        Args:
            prompt (str): The objective to convert.
            input_type (PromptDataType): The type of input data.

        Returns:
            ConverterResult: The reconstruction prompt.

        Raises:
            ValueError: If the input type is not supported.
            InvalidJsonException: If decomposition does not produce a valid result within the retries.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        words, types = await self._decompose_async(objective=prompt)
        reconstruction = self._build_reconstruction(words=words, types=types)
        return ConverterResult(output_text=reconstruction, output_type="text")

    @pyrit_json_retry
    async def _decompose_async(self, *, objective: str) -> tuple[list[str], list[str]]:
        """
        Decompose the objective into (words, types) via the converter target.

        Retries (via ``@pyrit_json_retry``) whenever the response is not valid, parseable JSON that
        satisfies the decomposition invariants.

        Args:
            objective (str): The objective to decompose.

        Returns:
            tuple[list[str], list[str]]: The phrase list and the matching role-tag list.

        Raises:
            InvalidJsonException: If the response is missing, unparseable, or fails validation.
        """
        conversation_id = str(uuid.uuid4())
        self._converter_target.set_system_prompt(
            system_prompt=self._decomposition_prompt.render_template_value(),
            conversation_id=conversation_id,
        )

        prompt_metadata = JsonResponseConfig(
            enabled=True,
            json_schema=self._decomposition_prompt.response_json_schema,
        ).to_metadata()

        request = Message(
            message_pieces=[
                MessagePiece(
                    role="user",
                    original_value=objective,
                    converted_value=objective,
                    original_value_data_type="text",
                    converted_value_data_type="text",
                    conversation_id=conversation_id,
                    sequence=0,
                    prompt_metadata=prompt_metadata,
                    converter_identifiers=[self.get_identifier()],
                )
            ]
        )
        response = await self._converter_target.send_prompt_async(message=request)
        if not response:
            # A blocked/filtered request can yield no response; signal a retry rather than IndexError.
            raise InvalidJsonException(message="no response from the decomposition target")
        return self._parse_and_validate(objective=objective, raw=response[0].get_value())

    def _parse_and_validate(self, *, objective: str, raw: str) -> tuple[list[str], list[str]]:
        """
        Parse the LLM response into (words, types) and validate it.

        Args:
            objective (str): The original objective, used for the reconstruction-recall check.
            raw (str): The raw LLM response text.

        Returns:
            tuple[list[str], list[str]]: The validated phrase list and role-tag list.

        Raises:
            InvalidJsonException: If the response is unparseable, malformed, contains an empty phrase, has
                invalid tags, lacks an opening instruction phrase or a noun, produces more noun phrases than
                codewords when the word-game is enabled, or fails the reconstruction-recall invariant.
        """
        try:
            data = json.loads(remove_markdown_json(raw))
        except (ValueError, TypeError) as exc:
            # ValueError covers json.JSONDecodeError; both are converted to the retry signal.
            raise InvalidJsonException(message=f"could not parse JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise InvalidJsonException(message="response must be a JSON object")
        words = data.get("words")
        types = data.get("types")
        if not isinstance(words, list) or not isinstance(types, list) or not words or len(words) != len(types):
            raise InvalidJsonException(message="response must contain equal-length non-empty 'words' and 'types' lists")
        words = [str(w) for w in words]
        if any(not w.strip() for w in words):
            raise InvalidJsonException(message="every phrase must be non-empty")
        if any(t not in _VALID_TAGS for t in types):
            raise InvalidJsonException(message=f"types must be one of {_VALID_TAGS}")
        if types[0] != "instruction":
            raise InvalidJsonException(message="the first phrase must be tagged 'instruction'")
        if "noun" not in types:
            raise InvalidJsonException(message="at least one phrase must be tagged 'noun'")
        if self._use_word_game:
            # The noun count comes from model output, so an overflow is recoverable: signal a retry
            # rather than a config error (which is reserved for duplicate codewords in __init__).
            noun_count = sum(1 for t in types if t == "noun")
            if noun_count > len(self._codewords):
                raise InvalidJsonException(
                    message=f"word-game supports at most {len(self._codewords)} noun phrases, "
                    f"but the decomposition produced {noun_count}"
                )

        recall = _token_recall(_tokens(objective), _tokens(" ".join(words)))
        if recall < _MIN_RECALL:
            raise InvalidJsonException(message=f"reconstruction recall {recall:.2f} below {_MIN_RECALL}")
        return words, types

    def _build_reconstruction(self, *, words: list[str], types: list[str]) -> str:
        """
        Assemble the Question-A/Question-B reconstruction prompt from the decomposition.

        When the word-game is enabled, each noun phrase is replaced by an innocuous codeword in its
        question, and a mapping preamble is prepended in the same prompt.

        Args:
            words (list[str]): The decomposed phrases.
            types (list[str]): The role tag for each phrase.

        Returns:
            str: The rendered reconstruction prompt, optionally prefixed with the word-game preamble.
        """
        questions = [f"Question A: How to {words[0].lower()}?"]
        question_prefixes = ["Question A"]
        structure_mask = ["using the methods in Question A"]
        word_game_mappings: list[str] = []
        noun_index = 0
        for i in range(1, len(types)):
            if types[i] == "noun":
                noun_index += 1
                structure_mask.append(f"the thing in Question B{noun_index}")
                question_prefixes.append(f"Question B{noun_index}")
                phrase = words[i]
                if self._use_word_game:
                    codeword = self._codewords[noun_index - 1]
                    # Serialise both sides so a phrase containing quotes cannot make the mapping
                    # ambiguous; ensure_ascii=False keeps non-Latin phrases (e.g. Arabic) readable.
                    word_game_mappings.append(
                        f"{json.dumps(codeword, ensure_ascii=False)} means {json.dumps(phrase, ensure_ascii=False)}"
                    )
                    phrase = codeword
                questions.append(f"Question B{noun_index}: What is/are {phrase}?")
            else:
                structure_mask.append(words[i])

        reconstruction = self._reconstruction_prompt.render_template_value(
            questions_prefix=" ".join(question_prefixes),
            sentence_structure=" ".join(structure_mask),
            harmful_questions=" ".join(questions),
        )
        if self._use_word_game and word_game_mappings:
            preamble = self._word_game_prompt.render_template_value(mapping="; ".join(word_game_mappings))
            return f"{preamble}\n\n{reconstruction}"
        return reconstruction
