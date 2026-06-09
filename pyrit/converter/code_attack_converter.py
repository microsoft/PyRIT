# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pathlib
import re
from typing import Literal

from pyrit.common.path import CONVERTER_SEED_PROMPT_PATH
from pyrit.models import PromptDataType, SeedPrompt
from pyrit.converter.converter import Converter, ConverterResult

# Maps (language, verbose) to the YAML template filename stem.
# C++ and Go have no separate verbose variant in the reference implementation.
_TEMPLATE_NAMES: dict[tuple[str, bool], str] = {
    ("python_stack", False): "code_attack_python_stack",
    ("python_stack", True): "code_attack_python_stack_plus",
    ("python_list", False): "code_attack_python_list",
    ("python_list", True): "code_attack_python_list_plus",
    ("python_string", False): "code_attack_python_string",
    ("python_string", True): "code_attack_python_string_plus",
    ("cpp", False): "code_attack_cpp",
    ("cpp", True): "code_attack_cpp",
    ("go", False): "code_attack_go",
    ("go", True): "code_attack_go",
}

_VALID_LANGUAGES = frozenset({"python_stack", "python_list", "python_string", "cpp", "go"})


class CodeAttackConverter(Converter):
    """
    Encodes a prompt as a code-completion task (CodeAttack, Ren et al. ACL 2024).

    The prompt is encoded word-by-word into a data-structure initialisation
    sequence embedded inside a partial code template. The model is asked to
    complete the code, which sidesteps natural-language safety training.

    **Separator normalisation (python_stack and python_list only):** tokens
    are split on ``[\\s\\-]+``, so hyphens and runs of whitespace are treated
    as delimiters and do not survive the encode/decode cycle. Byte-identical
    round-trips are only guaranteed for inputs whose words are separated by a
    single space with no hyphens.

    CodeAttack [@ren2024codeattack].
    """

    SUPPORTED_INPUT_TYPES = ("text",)
    SUPPORTED_OUTPUT_TYPES = ("text",)

    def __init__(
        self,
        *,
        language: Literal["python_stack", "python_list", "python_string", "cpp", "go"] = "python_stack",
        verbose: bool = True,
    ) -> None:
        """
        Args:
            language: Data-structure family to use for encoding. One of
                ``"python_stack"``, ``"python_list"``, ``"python_string"``,
                ``"cpp"``, ``"go"``.
            verbose: When ``True`` (default) the ``_plus`` template variant is
                used, which instructs the model to produce detailed paragraphs.
                When ``False`` the standard variant requests numbered steps.
                Intentionally a no-op for ``"cpp"`` and ``"go"``: the
                reference implementation provides no plus-variant for those
                languages, so both values resolve to the same template.

        Raises:
            ValueError: If ``language`` is not one of the supported values.
        """
        if language not in _VALID_LANGUAGES:
            raise ValueError(f"Invalid language {language!r}. Must be one of: {sorted(_VALID_LANGUAGES)}")
        self._language = language
        self._verbose = verbose

    async def convert_async(self, *, prompt: str, input_type: PromptDataType = "text") -> ConverterResult:
        """
        Encode the prompt into a code-completion template.

        Args:
            prompt: The natural-language prompt to encode.
            input_type: Must be ``"text"``.

        Returns:
            ConverterResult containing the code-completion template with the
            prompt encoded as data-structure initialisation calls.

        Raises:
            ValueError: If the input type is not supported.
        """
        if not self.input_supported(input_type):
            raise ValueError("Input type not supported")

        wrapped_input = self._encode(prompt)

        template_name = _TEMPLATE_NAMES[(self._language, self._verbose)]
        seed_prompt = SeedPrompt.from_yaml_file(pathlib.Path(CONVERTER_SEED_PROMPT_PATH) / f"{template_name}.yaml")
        formatted = seed_prompt.render_template_value(wrapped_input=wrapped_input)
        return ConverterResult(output_text=formatted, output_type="text")

    # ------------------------------------------------------------------
    # Internal encoding helpers
    # ------------------------------------------------------------------

    def _encode(self, prompt: str) -> str:
        """
        Dispatch to the appropriate encoding method for the selected language.

        Returns:
            The encoded wrapped_input string ready for template injection.

        Raises:
            ValueError: If ``self._language`` is not a recognised value (guard
                against future inconsistency).
        """
        match self._language:
            case "python_stack":
                return self._encode_python_stack(prompt)
            case "python_list":
                return self._encode_python_list(prompt)
            case "python_string":
                return self._encode_python_string(prompt)
            case "cpp":
                return self._encode_cpp(prompt)
            case "go":
                return self._encode_go(prompt)
            case _:
                raise ValueError(f"Unsupported language: {self._language!r}")

    def _encode_python_stack(self, prompt: str) -> str:
        words = re.split(r"[\s\-]+", prompt)
        if len(words) == 1:
            words = list(words[0])
        words = words[::-1]
        return "\n".join(f'    my_stack.append("{word}")' for word in words)

    def _encode_python_list(self, prompt: str) -> str:
        words = prompt.split()
        return "\n".join(f'    my_list.append("{word}")' for word in words)

    def _encode_python_string(self, prompt: str) -> str:
        return f'    my_string = "{prompt}"'

    def _encode_cpp(self, prompt: str) -> str:
        return f'    std::string my_string = "{prompt}";'

    def _encode_go(self, prompt: str) -> str:
        return f'        myQueue := "{prompt}"'
